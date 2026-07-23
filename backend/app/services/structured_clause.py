"""Course LoRA structured clause analysis — exact SFT training protocol.

Protocol source of truth:
  data_pipeline/configs/sft_tasks.yaml
  data_pipeline/bidpilot_data/sft/build.py (user prompt prefixes)

No RAG context. No [S1] citation markers. Assistant must be compact JSON.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.structured_clause_analysis import StructuredClauseAnalysis
from app.schemas.structured_clause import TASK_OUTPUT_MODELS, StructuredClauseResponse
from app.services.llm_client import LlmClient, LlmError, get_llm_client
from app.services.model_serving import (
    CAP_STRUCTURED_EXTRACTION,
    COURSE_LORA_MODEL_ID,
    resolve_model_selection,
)

# Exact system prompts from data_pipeline/configs/sft_tasks.yaml
TASK_SPECS: dict[str, dict[str, Any]] = {
    "requirement_classify": {
        "system": "你是招投标文件分析助手，负责对条款进行分类并判断是否强制。",
        "user_prefix": "判断以下条款的类别与是否强制：\n",
        "required_keys": ["category", "mandatory", "risk_level", "confidence"],
    },
    "qualification_extract": {
        "system": "你是招投标文件分析助手，负责抽取资格要求。",
        "user_prefix": "抽取资格要求：\n",
        "required_keys": ["requirements", "mandatory", "evidence_required"],
    },
    "scoring_extract": {
        "system": "你是招投标文件分析助手，负责抽取评分办法条目。",
        "user_prefix": "抽取评分条目：\n",
        "required_keys": ["item", "score", "method"],
    },
    "risk_detect": {
        "system": "你是招投标合规风险识别助手。",
        "user_prefix": "识别风险：\n",
        "required_keys": ["risk_level", "risk_type", "reason", "is_rejection_clause"],
    },
    "project_info_extract": {
        "system": "你是招投标文件分析助手，负责抽取项目基本信息。",
        "user_prefix": "从以下官方项目材料中抽取项目要素：\n",
        "required_keys": ["project_name", "purchaser", "budget_cny", "deadline", "region"],
    },
}

COURSE_DATASET_VERSION = "course_pilot"
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
_THINK_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)


def build_messages(task_type: str, clause_text: str) -> list[dict[str, str]]:
    spec = TASK_SPECS.get(task_type)
    if not spec:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "unsupported structured task_type",
                "reason_code": "unsupported_task_type",
                "task_type": task_type,
            },
        )
    text = (clause_text or "").strip()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "clause_text required", "reason_code": "empty_clause"},
        )
    return [
        {"role": "system", "content": str(spec["system"])},
        {"role": "user", "content": f"{spec['user_prefix']}{text}"},
    ]


def extract_json_object(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse compact JSON from model output; return (obj, parse_error)."""
    text = _THINK_RE.sub("", raw or "").strip()
    text = _FENCE_RE.sub("", text).strip()
    if not text:
        return None, "empty_output"
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj, None
        return None, "not_object"
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict):
                return obj, None
        except json.JSONDecodeError as exc:
            return None, f"json_error:{exc.msg}"
    return None, "json_error"


def validate_task_schema(
    obj: dict[str, Any] | None, task_type: str
) -> tuple[bool, float, list[str], dict[str, Any] | None, str | None]:
    """Strict Pydantic validation for the SFT task output schema."""
    keys = list(TASK_SPECS[task_type]["required_keys"])
    model_cls = TASK_OUTPUT_MODELS.get(task_type)
    if model_cls is None:
        return False, 0.0, keys, None, "unknown_task"
    if obj is None:
        # Leave schema_err empty so extract_json_object's parse_error surfaces.
        return False, 0.0, keys, None, None
    present = [k for k in keys if k in obj]
    coverage = (len(present) / len(keys)) if keys else 1.0
    missing = [k for k in keys if k not in obj]
    try:
        validated = model_cls.model_validate(obj)
        return True, 1.0, [], validated.model_dump(mode="json"), None
    except ValidationError as exc:
        err = ";".join(
            f"{'.'.join(str(x) for x in e.get('loc', ()))}:{e.get('type')}"
            for e in exc.errors()[:8]
        )
        return False, coverage, missing, None, f"schema_error:{err}"


@dataclass
class StructuredClauseResult:
    task_type: str
    clause_text: str
    raw_output: str
    parsed: dict[str, Any] | None
    schema_valid: bool
    required_field_coverage: float
    missing_fields: list[str]
    parse_error: str | None
    requested_model_id: str
    resolved_model_id: str | None
    served_model_name: str | None
    model_type: str | None
    adapter_version: str | None
    dataset_version: str
    fallback_used: bool
    latency_ms: float
    capability: str
    id: UUID | None = None
    project_id: UUID | None = None
    created_at: Any = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "task_type": self.task_type,
            "clause_text": self.clause_text,
            "raw_output": self.raw_output[:4000],
            "parsed": self.parsed,
            "schema_valid": self.schema_valid,
            "required_field_coverage": self.required_field_coverage,
            "missing_fields": self.missing_fields,
            "parse_error": self.parse_error,
            "requested_model_id": self.requested_model_id,
            "resolved_model_id": self.resolved_model_id,
            "served_model_name": self.served_model_name,
            "model_type": self.model_type,
            "adapter_version": self.adapter_version,
            "dataset_version": self.dataset_version,
            "fallback_used": self.fallback_used,
            "latency_ms": self.latency_ms,
            "capability": self.capability,
            "created_at": self.created_at,
        }


class StructuredClauseService:
    """Run one SFT-protocol structured clause task against Base or Course LoRA."""

    def __init__(self, db: Session | None = None, *, llm: LlmClient | None = None) -> None:
        self.db = db
        self._injected = llm

    def analyze(
        self,
        *,
        clause_text: str,
        task_type: str = "requirement_classify",
        model_id: str | None = None,
        allow_base_fallback: bool = False,
        temperature: float = 0.1,
        max_tokens: int = 512,
        project_id: UUID | None = None,
        persist: bool = True,
    ) -> StructuredClauseResult:
        if task_type not in TASK_SPECS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "message": "unsupported structured task_type",
                    "reason_code": "unsupported_task_type",
                    "task_type": task_type,
                },
            )

        resolution = resolve_model_selection(
            model_id,
            allow_fallback=allow_base_fallback,
            probe=True,
            required_capability=CAP_STRUCTURED_EXTRACTION,
        )
        if not resolution.available or not resolution.served_model_name:
            codes = resolution.reason_codes or ["model_not_served"]
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "message": "所选模型当前不可用于结构化抽取",
                    "reason_code": codes[0],
                    "reason_codes": codes,
                    "requested_model_id": resolution.requested_model_id,
                },
            )

        messages = build_messages(task_type, clause_text)
        client = self._injected or get_llm_client()
        if self._injected is None:
            client = LlmClient(
                base_url=client.base_url if isinstance(client, LlmClient) else None,
                api_key=client.api_key if isinstance(client, LlmClient) else None,
                model=resolution.served_model_name,
                timeout_seconds=(client.timeout_seconds if isinstance(client, LlmClient) else None),
                enabled=client.enabled if isinstance(client, LlmClient) else None,
            )
        elif isinstance(client, LlmClient):
            client = LlmClient(
                base_url=client.base_url,
                api_key=client.api_key,
                model=resolution.served_model_name,
                timeout_seconds=client.timeout_seconds,
                enabled=client.enabled,
            )

        t0 = time.perf_counter()
        try:
            if hasattr(client, "chat"):
                result = client.chat(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                raw = getattr(result, "content", None) or str(result)
                latency = float(getattr(result, "latency_ms", (time.perf_counter() - t0) * 1000))
            else:
                raise LlmError("llm client missing chat()")
        except LlmError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "message": "结构化抽取调用失败",
                    "reason_code": "llm_error",
                    "detail": exc.detail,
                    "requested_model_id": resolution.requested_model_id,
                    "served_model_name": resolution.served_model_name,
                },
            ) from exc

        parsed_raw, parse_error = extract_json_object(raw)
        schema_ok, coverage, missing, parsed, schema_err = validate_task_schema(
            parsed_raw, task_type
        )
        if parse_error and not schema_err:
            schema_err = parse_error
        out = StructuredClauseResult(
            task_type=task_type,
            clause_text=clause_text.strip(),
            raw_output=raw,
            parsed=parsed if schema_ok else parsed_raw,
            schema_valid=bool(schema_ok and parse_error is None),
            required_field_coverage=coverage,
            missing_fields=missing,
            parse_error=schema_err or parse_error,
            requested_model_id=resolution.requested_model_id,
            resolved_model_id=resolution.resolved_model_id,
            served_model_name=resolution.served_model_name,
            model_type=resolution.model_type,
            adapter_version=resolution.adapter_version
            or ("course-1.0" if resolution.resolved_model_id == COURSE_LORA_MODEL_ID else "base"),
            dataset_version=COURSE_DATASET_VERSION,
            fallback_used=bool(resolution.fallback_used),
            latency_ms=latency,
            capability=CAP_STRUCTURED_EXTRACTION,
            project_id=project_id,
        )

        if persist and self.db is not None and project_id is not None:
            row = StructuredClauseAnalysis(
                project_id=project_id,
                task_type=out.task_type,
                clause_text=out.clause_text,
                raw_output=out.raw_output[:20000],
                parsed_json=out.parsed,
                schema_valid=out.schema_valid,
                required_field_coverage=out.required_field_coverage,
                missing_fields_json=out.missing_fields,
                parse_error=(out.parse_error or "")[:512] or None,
                requested_model_id=out.requested_model_id,
                resolved_model_id=out.resolved_model_id,
                served_model_name=out.served_model_name,
                model_type=out.model_type,
                adapter_version=out.adapter_version,
                dataset_version=out.dataset_version,
                fallback_used=out.fallback_used,
                latency_ms=out.latency_ms,
                capability=out.capability,
            )
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)
            out.id = row.id
            out.created_at = row.created_at
        return out

    def list_analyses(
        self, project_id: UUID, *, limit: int = 20, offset: int = 0
    ) -> tuple[list[StructuredClauseResponse], int]:
        if self.db is None:
            return [], 0
        total = int(
            self.db.scalar(
                select(func.count())
                .select_from(StructuredClauseAnalysis)
                .where(StructuredClauseAnalysis.project_id == project_id)
            )
            or 0
        )
        rows = list(
            self.db.scalars(
                select(StructuredClauseAnalysis)
                .where(StructuredClauseAnalysis.project_id == project_id)
                .order_by(StructuredClauseAnalysis.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        items = [
            StructuredClauseResponse(
                id=r.id,
                project_id=r.project_id,
                task_type=r.task_type,
                clause_text=r.clause_text,
                raw_output=r.raw_output[:4000],
                parsed=r.parsed_json,
                schema_valid=r.schema_valid,
                required_field_coverage=r.required_field_coverage,
                missing_fields=list(r.missing_fields_json or []),
                parse_error=r.parse_error,
                requested_model_id=r.requested_model_id,
                resolved_model_id=r.resolved_model_id,
                served_model_name=r.served_model_name,
                model_type=r.model_type,
                adapter_version=r.adapter_version,
                dataset_version=r.dataset_version or COURSE_DATASET_VERSION,
                fallback_used=r.fallback_used,
                latency_ms=r.latency_ms,
                capability=r.capability,
                created_at=r.created_at,
            )
            for r in rows
        ]
        return items, total
