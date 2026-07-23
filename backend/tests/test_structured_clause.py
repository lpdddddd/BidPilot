"""Structured clause analysis + capability + compose preflight tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from app.services import model_serving as ms
from app.services.structured_clause import (
    StructuredClauseService,
    build_messages,
    extract_json_object,
    validate_required_keys,
)
from fastapi import HTTPException


class _FakeChat:
    def __init__(self, content: str, model: str = "bidpilot-qwen3-8b"):
        self.content = content
        self.model = model
        self.latency_ms = 12.0
        self.finish_reason = "stop"
        self.request_id = "req-1"


class _FakeLlm:
    def __init__(self, content: str, model: str = "bidpilot-qwen3-8b"):
        self._content = content
        self.model = model
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs):  # noqa: ANN001
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return _FakeChat(self._content, self.model)


def test_build_messages_matches_sft_protocol() -> None:
    msgs = build_messages("requirement_classify", "须具备一级资质")
    assert msgs[0]["role"] == "system"
    assert "分类" in msgs[0]["content"]
    assert msgs[1]["content"].startswith("判断以下条款的类别与是否强制：\n")
    assert "须具备一级资质" in msgs[1]["content"]


def test_json_parse_and_schema() -> None:
    obj, err = extract_json_object(
        '{"category":"technical","mandatory":true,"risk_level":"medium","confidence":0.5}'
    )
    assert err is None and obj is not None
    ok, cov, missing = validate_required_keys(obj, "requirement_classify")
    assert ok and cov == 1.0 and missing == []
    bad, err2 = extract_json_object("not json")
    assert bad is None and err2


def test_structured_base_and_lora_routes(monkeypatch) -> None:
    clause = "须具备电子与智能化工程专业承包贰级及以上资质"

    def resolve(model_id, **kwargs):  # noqa: ANN001
        mid = model_id or ms.BASE_MODEL_ID
        if mid == ms.COURSE_LORA_MODEL_ID:
            return ms.ModelResolution(
                available=True,
                requested_model_id=mid,
                resolved_model_id=mid,
                served_model_name="bidpilot-qwen3-8b-course-lora",
                model_type="lora",
                adapter_version="course-1.0",
                train_track="course_pilot",
                fallback_used=False,
                reason_codes=[],
                display_name="Course LoRA",
                capabilities=[ms.CAP_STRUCTURED_EXTRACTION],
            )
        return ms.ModelResolution(
            available=True,
            requested_model_id=ms.BASE_MODEL_ID,
            resolved_model_id=ms.BASE_MODEL_ID,
            served_model_name="bidpilot-qwen3-8b",
            model_type="base",
            adapter_version="base",
            train_track=None,
            fallback_used=False,
            reason_codes=[],
            display_name="Base",
            capabilities=list(ms.BASE_CAPABILITIES),
        )

    monkeypatch.setattr(
        "app.services.structured_clause.resolve_model_selection",
        resolve,
    )
    json_ok = '{"category":"qualification","mandatory":true,"risk_level":"high","confidence":0.8}'
    for mid, served in (
        (ms.BASE_MODEL_ID, "bidpilot-qwen3-8b"),
        (ms.COURSE_LORA_MODEL_ID, "bidpilot-qwen3-8b-course-lora"),
    ):
        llm = _FakeLlm(json_ok, model=served)
        result = StructuredClauseService(llm=llm).analyze(  # type: ignore[arg-type]
            clause_text=clause,
            task_type="requirement_classify",
            model_id=mid,
        )
        assert result.served_model_name == served
        assert result.requested_model_id == mid
        assert result.fallback_used is False
        assert result.schema_valid is True
        assert result.parsed is not None
        assert llm.calls[0]["kwargs"]["temperature"] == 0.1
        assert llm.calls[0]["messages"][0]["content"].startswith("你是招投标")


def test_structured_unavailable_and_no_silent_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.structured_clause.resolve_model_selection",
        lambda *a, **k: ms.ModelResolution(
            available=False,
            requested_model_id=ms.COURSE_LORA_MODEL_ID,
            resolved_model_id=ms.COURSE_LORA_MODEL_ID,
            served_model_name="bidpilot-qwen3-8b-course-lora",
            model_type="lora",
            adapter_version="course-1.0",
            train_track="course_pilot",
            fallback_used=False,
            reason_codes=[ms.REASON_NOT_SERVED],
            capabilities=[ms.CAP_STRUCTURED_EXTRACTION],
        ),
    )
    with pytest.raises(HTTPException) as exc:
        StructuredClauseService(llm=_FakeLlm("{}")).analyze(  # type: ignore[arg-type]
            clause_text="x",
            model_id=ms.COURSE_LORA_MODEL_ID,
            allow_base_fallback=False,
        )
    assert exc.value.status_code == 422


def test_capability_blocks_lora_from_grounded_qa(monkeypatch, tmp_path: Path) -> None:
    adapter = tmp_path / "a"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text(
        '{"r":16,"base_model_name_or_path":"Qwen/Qwen3-8B","peft_type":"LORA"}',
        encoding="utf-8",
    )
    (adapter / "adapter_model.safetensors").write_bytes(b"x")
    with (
        patch.object(
            ms.registry,
            "load_registry",
            return_value={
                "active_model_id": "qwen3-8b-lora-course",
                "models": [
                    {
                        "model_id": "qwen3-8b-lora-course",
                        "display_name": "Course",
                        "adapter_path": str(adapter),
                        "served_name": "bidpilot-qwen3-8b-course-lora",
                        "version": "course-1.0",
                        "train_track": "course_pilot",
                    }
                ],
            },
        ),
        patch.object(
            ms,
            "list_served_model_ids",
            return_value=(["bidpilot-qwen3-8b", "bidpilot-qwen3-8b-course-lora"], None),
        ),
        patch.object(ms, "configured_base_for_compare", return_value="Qwen/Qwen3-8B"),
        patch("app.core.config.get_settings") as gs,
    ):
        settings = MagicMock()
        settings.llm_enabled = True
        settings.llm_model = "bidpilot-qwen3-8b"
        settings.llm_model_path = ""
        settings.llm_model_source = "Qwen/Qwen3-8B"
        settings.llm_max_lora_rank = 16
        gs.return_value = settings
        denied = ms.resolve_model_selection(
            ms.COURSE_LORA_MODEL_ID,
            required_capability=ms.CAP_GROUNDED_QA,
            probe=True,
        )
        assert denied.available is False
        assert ms.REASON_CAPABILITY in denied.reason_codes
        allowed = ms.resolve_model_selection(
            ms.COURSE_LORA_MODEL_ID,
            required_capability=ms.CAP_STRUCTURED_EXTRACTION,
            probe=True,
        )
        assert allowed.available is True


def test_compose_entrypoint_enforces_preflight() -> None:
    root = Path(__file__).resolve().parents[2]
    compose = (root / "infra" / "docker-compose.llm.yml").read_text(encoding="utf-8")
    assert "vllm_compose_entrypoint.sh" in compose
    assert "LLM_LORA_ADAPTER_PATH: /models/bidpilot-course-lora" in compose
    assert "LLM_LORA_HOST_PATH" in compose
    assert "target: /models/bidpilot-course-lora" in compose
    entry = (root / "scripts" / "vllm_compose_entrypoint.sh").read_text(encoding="utf-8")
    assert "validate_adapter_for_serving" in entry
    # Must not let host ADAPTER_PATH leak into container target via substitution alone.
    assert "target: ${LLM_LORA_ADAPTER_PATH" not in compose


def test_public_payload_includes_capabilities(monkeypatch) -> None:
    with patch.object(ms, "list_served_model_ids", return_value=([], ms.REASON_LLM_DISABLED)):
        payload = ms.public_models_payload(probe=True)
    base = next(i for i in payload["items"] if i["model_id"] == ms.BASE_MODEL_ID)
    assert ms.CAP_GROUNDED_QA in base["capabilities"]
    assert ms.CAP_STRUCTURED_EXTRACTION in base["capabilities"]
    blob = str(payload)
    assert "/root/" not in blob and "autodl-tmp" not in blob
