"""Model availability: registered / adapter_exists / served (no absolute paths in API)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import httpx

from app.core.config import get_settings
from app.services import model_registry as registry

ModelType = Literal["base", "lora"]
BaseMatch = Literal["match", "mismatch", "unverified", "n/a"]

# Stable public ids (not host paths).
BASE_MODEL_ID = "qwen3-8b-base"
COURSE_LORA_MODEL_ID = "qwen3-8b-lora-course"
CANONICAL_QWEN3_8B = "Qwen/Qwen3-8B"

REASON_NOT_REGISTERED = "not_registered"
REASON_ADAPTER_MISSING = "adapter_missing"
REASON_ADAPTER_INCOMPLETE = "adapter_incomplete"
REASON_NOT_SERVED = "model_not_served"
REASON_LLM_DISABLED = "provider_not_configured"
REASON_UNREACHABLE = "llm_unreachable"
REASON_BASE_MISMATCH = "base_model_mismatch"
REASON_BASE_UNVERIFIED = "base_model_unverified"
REASON_RANK_EXCEEDED = "lora_rank_exceeded"
REASON_UNKNOWN_MODEL = "unknown_model_id"
REASON_CAPABILITY = "capability_unsupported"

# Model capability ids (only register what is implemented and truthful).
CAP_GROUNDED_QA = "grounded_qa"
CAP_STRUCTURED_EXTRACTION = "structured_extraction"
CAP_COMPLIANCE_ANALYSIS = "compliance_analysis"

BASE_CAPABILITIES: tuple[str, ...] = (CAP_GROUNDED_QA, CAP_STRUCTURED_EXTRACTION)
# Course LoRA SFT is clause JSON — not grounded [S1] Ask, not rule Compliance.
COURSE_LORA_CAPABILITIES: tuple[str, ...] = (CAP_STRUCTURED_EXTRACTION,)


def model_capabilities(model_id: str, model_type: ModelType | None) -> list[str]:
    if model_id == BASE_MODEL_ID or model_type == "base":
        return list(BASE_CAPABILITIES)
    if model_id == COURSE_LORA_MODEL_ID or model_type == "lora":
        # Smoke LoRA shares structured_extraction only.
        return list(COURSE_LORA_CAPABILITIES)
    return []


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _adapter_dir(rel_or_name: str | None) -> Path | None:
    if not rel_or_name:
        return None
    p = Path(rel_or_name)
    if p.is_absolute():
        return p if p.exists() else None
    candidate = repo_root() / p
    return candidate if candidate.exists() else None


def public_model_label(raw: str | None) -> str | None:
    """Safe display label: Hub id or basename only (never host absolute roots)."""
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if "/" in text and not text.startswith("/") and text.count("/") == 1:
        # Hub-style org/name
        return text
    return Path(text).name or text


def canonicalize_base_identity(raw: str | None) -> str | None:
    """Map known Qwen3-8B path/id variants to a single canonical Hub id.

    Returns None when identity cannot be confirmed from known aliases.
    Does not use fuzzy substring matching across unrelated names.
    """
    if not raw:
        return None
    text = str(raw).strip().replace("\\", "/")
    if not text:
        return None
    lower = text.lower()
    # Exact Hub id
    if text == CANONICAL_QWEN3_8B or lower == CANONICAL_QWEN3_8B.lower():
        return CANONICAL_QWEN3_8B
    # Basename or trailing path segment
    name = Path(text.rstrip("/")).name
    if name in {"Qwen3-8B", "qwen3-8b", "Qwen3-8b"}:
        return CANONICAL_QWEN3_8B
    # Local snapshot that contains config.json and model_type Qwen3
    p = Path(text)
    if p.is_dir() and (p / "config.json").is_file():
        try:
            cfg = json.loads((p / "config.json").read_text(encoding="utf-8"))
            arch = str(cfg.get("architectures") or cfg.get("model_type") or "")
            if "qwen3" in arch.lower() and name in {"Qwen3-8B", "qwen3-8b"}:
                return CANONICAL_QWEN3_8B
            # Explicit _name_or_path in config
            nop = str(cfg.get("_name_or_path") or "")
            if nop == CANONICAL_QWEN3_8B or Path(nop).name in {"Qwen3-8B", "qwen3-8b"}:
                return CANONICAL_QWEN3_8B
        except Exception:  # noqa: BLE001
            return None
    return None


def compare_base_models(configured: str | None, adapter_base: str | None) -> BaseMatch:
    """Strict identity compare via canonicalization (no fuzzy substring)."""
    left = canonicalize_base_identity(configured)
    right = canonicalize_base_identity(adapter_base)
    if left and right:
        return "match" if left == right else "mismatch"

    def _distinct_foreign_hub(raw: str | None, known: str | None) -> bool:
        if not raw or not known:
            return False
        text = str(raw).strip().replace("\\", "/")
        # Hub-style org/name that is not our canonical model.
        if "/" in text and not text.startswith("/") and canonicalize_base_identity(text) is None:
            return text != known
        name = Path(text).name
        if (
            name
            and canonicalize_base_identity(name) is None
            and name
            not in {
                "Qwen3-8B",
                "qwen3-8b",
            }
        ):
            # Distinct basename that we cannot map to the known model.
            return Path(known).name not in {name, "Qwen3-8B"}
        return False

    if left and _distinct_foreign_hub(adapter_base, left):
        return "mismatch"
    if right and _distinct_foreign_hub(configured, right):
        return "mismatch"
    if (configured and str(configured).strip()) or (adapter_base and str(adapter_base).strip()):
        return "unverified"
    return "unverified"


def check_adapter_files(adapter_path: Path | None) -> tuple[bool, list[str]]:
    """Return (files_ok, reason_codes) — does not include base match."""
    if adapter_path is None or not adapter_path.is_dir():
        return False, [REASON_ADAPTER_MISSING]
    reasons: list[str] = []
    cfg = adapter_path / "adapter_config.json"
    if not cfg.is_file():
        reasons.append(REASON_ADAPTER_INCOMPLETE)
    weight_ok = any(
        (adapter_path / name).is_file()
        for name in (
            "adapter_model.safetensors",
            "adapter_model.bin",
            "adapter_model.pt",
        )
    )
    if not weight_ok:
        reasons.append(REASON_ADAPTER_INCOMPLETE)
    if reasons:
        return False, reasons
    return True, []


def read_adapter_config(adapter_path: Path) -> dict[str, Any]:
    raw: Any = json.loads((adapter_path / "adapter_config.json").read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    return raw


def validate_adapter_for_serving(
    adapter_path: Path | None,
    *,
    configured_base: str | None,
    max_lora_rank: int = 16,
) -> dict[str, Any]:
    """Full preflight used by API status and scripts (no absolute paths in result)."""
    files_ok, file_reasons = check_adapter_files(adapter_path)
    result: dict[str, Any] = {
        "files_ok": files_ok,
        "adapter_exists": False,
        "reason_codes": list(file_reasons),
        "base_model_match": "n/a",
        "configured_base_model": public_model_label(configured_base),
        "adapter_base_model": None,
        "lora_rank": None,
        "rank_ok": True,
    }
    if not files_ok or adapter_path is None:
        return result
    try:
        cfg = read_adapter_config(adapter_path)
    except Exception:  # noqa: BLE001
        result["reason_codes"] = [REASON_ADAPTER_INCOMPLETE]
        return result
    adapter_base = str(cfg.get("base_model_name_or_path") or "")
    result["adapter_base_model"] = public_model_label(adapter_base)
    rank = int(cfg.get("r") or 0)
    result["lora_rank"] = rank
    if rank > int(max_lora_rank):
        result["rank_ok"] = False
        result["reason_codes"].append(REASON_RANK_EXCEEDED)
    match = compare_base_models(configured_base, adapter_base)
    result["base_model_match"] = match
    if match == "mismatch":
        result["reason_codes"].append(REASON_BASE_MISMATCH)
    elif match == "unverified":
        result["reason_codes"].append(REASON_BASE_UNVERIFIED)
    # adapter_exists requires files + match + rank
    ok = files_ok and match == "match" and result["rank_ok"]
    result["adapter_exists"] = ok
    result["reason_codes"] = list(dict.fromkeys(result["reason_codes"]))
    return result


def configured_base_for_compare() -> str:
    settings = get_settings()
    # Prefer local path when set (can canonicalize via config.json), else Hub id.
    path = (settings.llm_model_path or "").strip()
    if path:
        return path
    return (settings.llm_model_source or CANONICAL_QWEN3_8B).strip()


def list_served_model_ids(*, timeout: float = 5.0) -> tuple[list[str], str | None]:
    """Probe OpenAI-compatible /models. Returns (ids, error_reason_code|None)."""
    settings = get_settings()
    if not settings.llm_enabled:
        return [], REASON_LLM_DISABLED
    base = (settings.llm_base_url or "").rstrip("/")
    if not base:
        return [], REASON_LLM_DISABLED
    url = f"{base}/models"
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(
                url,
                headers={"Authorization": f"Bearer {settings.llm_api_key}"}
                if settings.llm_api_key
                else {},
            )
        if response.status_code >= 400:
            return [], REASON_UNREACHABLE
        data = response.json()
        ids = [
            str(m.get("id")) for m in data.get("data", []) if isinstance(m, dict) and m.get("id")
        ]
        return ids, None
    except Exception:  # noqa: BLE001 — probe must never raise to callers
        return [], REASON_UNREACHABLE


@dataclass
class ModelStatusView:
    model_id: str
    display_name: str
    model_type: ModelType
    registered: bool
    adapter_exists: bool
    served: bool
    served_model_name: str | None
    version: str | None
    train_track: str | None
    reason_codes: list[str]
    notes: str | None = None
    base_model_match: BaseMatch = "n/a"
    configured_base_model: str | None = None
    adapter_base_model: str | None = None
    last_probe_at: str | None = None
    capabilities: list[str] | None = None


@dataclass
class ModelResolution:
    available: bool
    requested_model_id: str
    resolved_model_id: str | None
    served_model_name: str | None
    model_type: ModelType | None
    adapter_version: str | None
    train_track: str | None
    fallback_used: bool
    reason_codes: list[str]
    display_name: str | None = None
    capabilities: list[str] | None = None

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def _base_status(
    served_ids: list[str], probe_err: str | None, *, probed_at: str
) -> ModelStatusView:
    settings = get_settings()
    served_name = settings.llm_model
    reasons: list[str] = []
    served = served_name in served_ids
    if probe_err:
        reasons.append(probe_err)
    elif not served:
        reasons.append(REASON_NOT_SERVED)
    return ModelStatusView(
        model_id=BASE_MODEL_ID,
        display_name="Qwen3-8B Base",
        model_type="base",
        registered=True,
        adapter_exists=True,  # base has no adapter requirement
        served=served and probe_err is None,
        served_model_name=served_name,
        version="base",
        train_track=None,
        reason_codes=reasons,
        notes="基座模型（无 Adapter）",
        base_model_match="n/a",
        configured_base_model=public_model_label(configured_base_for_compare()),
        adapter_base_model=None,
        last_probe_at=probed_at,
        capabilities=model_capabilities(BASE_MODEL_ID, "base"),
    )


def _lora_status_from_record(
    rec: dict[str, Any],
    served_ids: list[str],
    probe_err: str | None,
    *,
    probed_at: str,
) -> ModelStatusView:
    settings = get_settings()
    model_id = str(rec.get("model_id") or "")
    served_name = str(rec.get("served_name") or model_id)
    adapter_rel = rec.get("adapter_path")
    adapter_path = _adapter_dir(str(adapter_rel) if adapter_rel else None)
    max_rank = int(getattr(settings, "llm_max_lora_rank", 16) or 16)
    # Allow env override without requiring Settings field
    import os

    max_rank = int(os.getenv("LLM_MAX_LORA_RANK", str(max_rank)) or max_rank)
    validation = validate_adapter_for_serving(
        adapter_path,
        configured_base=configured_base_for_compare(),
        max_lora_rank=max_rank,
    )
    reasons = list(validation["reason_codes"])
    adapter_ok = bool(validation["adapter_exists"])
    served = served_name in served_ids
    if probe_err:
        reasons.append(probe_err)
    elif not served:
        reasons.append(REASON_NOT_SERVED)
    # served only when adapter_exists (strict match) AND live probe
    live = bool(served and adapter_ok and probe_err is None)
    return ModelStatusView(
        model_id=model_id,
        display_name=str(rec.get("display_name") or model_id),
        model_type="lora",
        registered=True,
        adapter_exists=adapter_ok,
        served=live,
        served_model_name=served_name,
        version=str(rec.get("version") or "") or None,
        train_track=str(rec.get("train_track") or "") or None,
        reason_codes=list(dict.fromkeys(reasons)),
        notes=str(rec.get("notes") or "") or None,
        base_model_match=cast(BaseMatch, validation["base_model_match"]),
        configured_base_model=validation["configured_base_model"],
        adapter_base_model=validation["adapter_base_model"],
        last_probe_at=probed_at,
        capabilities=model_capabilities(model_id, "lora"),
    )


def list_model_statuses(*, probe: bool = True) -> list[ModelStatusView]:
    served_ids: list[str] = []
    probe_err: str | None = None
    probed_at = datetime.now(UTC).isoformat()
    if probe:
        served_ids, probe_err = list_served_model_ids()
    out: list[ModelStatusView] = [_base_status(served_ids, probe_err, probed_at=probed_at)]
    data = registry.load_registry()
    for rec in data.get("models") or []:
        if not isinstance(rec, dict):
            continue
        out.append(_lora_status_from_record(rec, served_ids, probe_err, probed_at=probed_at))
    return out


def get_model_status(model_id: str, *, probe: bool = True) -> ModelStatusView | None:
    for item in list_model_statuses(probe=probe):
        if item.model_id == model_id:
            return item
    return None


def resolve_model_selection(
    model_id: str | None,
    *,
    allow_fallback: bool = False,
    probe: bool = True,
    required_capability: str | None = None,
) -> ModelResolution:
    """Map public model_id → served name. Never silently substitutes LoRA with Base
    unless allow_fallback=True (and then records fallback_used).

    When ``required_capability`` is set, reject models that do not advertise it
    (e.g. Course LoRA must not be used for grounded_qa).
    """
    requested = (model_id or BASE_MODEL_ID).strip() or BASE_MODEL_ID

    statuses = {m.model_id: m for m in list_model_statuses(probe=probe)}
    by_served = {m.served_model_name: m for m in statuses.values() if m.served_model_name}
    status = statuses.get(requested) or by_served.get(requested)

    if status is None:
        return ModelResolution(
            available=False,
            requested_model_id=requested,
            resolved_model_id=None,
            served_model_name=None,
            model_type=None,
            adapter_version=None,
            train_track=None,
            fallback_used=False,
            reason_codes=[REASON_UNKNOWN_MODEL],
            capabilities=[],
        )

    caps = list(status.capabilities or model_capabilities(status.model_id, status.model_type))
    if required_capability and required_capability not in caps:
        return ModelResolution(
            available=False,
            requested_model_id=requested,
            resolved_model_id=status.model_id,
            served_model_name=status.served_model_name,
            model_type=status.model_type,
            adapter_version=status.version,
            train_track=status.train_track,
            fallback_used=False,
            reason_codes=[REASON_CAPABILITY],
            display_name=status.display_name,
            capabilities=caps,
        )

    if status.served and status.served_model_name:
        return ModelResolution(
            available=True,
            requested_model_id=requested,
            resolved_model_id=status.model_id,
            served_model_name=status.served_model_name,
            model_type=status.model_type,
            adapter_version=status.version,
            train_track=status.train_track,
            fallback_used=False,
            reason_codes=[],
            display_name=status.display_name,
            capabilities=caps,
        )

    reasons = list(status.reason_codes) or [REASON_NOT_SERVED]
    if allow_fallback and status.model_type == "lora":
        base = statuses.get(BASE_MODEL_ID)
        if base and base.served and base.served_model_name:
            base_caps = list(
                base.capabilities or model_capabilities(base.model_id, base.model_type)
            )
            if required_capability and required_capability not in base_caps:
                return ModelResolution(
                    available=False,
                    requested_model_id=requested,
                    resolved_model_id=status.model_id,
                    served_model_name=status.served_model_name,
                    model_type=status.model_type,
                    adapter_version=status.version,
                    train_track=status.train_track,
                    fallback_used=False,
                    reason_codes=[REASON_CAPABILITY, *reasons],
                    display_name=status.display_name,
                    capabilities=caps,
                )
            return ModelResolution(
                available=True,
                requested_model_id=requested,
                resolved_model_id=base.model_id,
                served_model_name=base.served_model_name,
                model_type="base",
                adapter_version=base.version,
                train_track=None,
                fallback_used=True,
                reason_codes=reasons,
                display_name=base.display_name,
                capabilities=base_caps,
            )

    return ModelResolution(
        available=False,
        requested_model_id=requested,
        resolved_model_id=status.model_id,
        served_model_name=status.served_model_name,
        model_type=status.model_type,
        adapter_version=status.version,
        train_track=status.train_track,
        fallback_used=False,
        reason_codes=reasons,
        display_name=status.display_name,
        capabilities=caps,
    )


def public_models_payload(*, probe: bool = True) -> dict[str, Any]:
    """Safe payload for Dashboard / Evaluation model pickers."""
    settings = get_settings()
    items = []
    for m in list_model_statuses(probe=probe):
        primary_reason = m.reason_codes[0] if m.reason_codes else None
        items.append(
            {
                "model_id": m.model_id,
                "display_name": m.display_name,
                "model_type": m.model_type,
                "registered": m.registered,
                "adapter_exists": m.adapter_exists,
                "served": m.served,
                "served_model_name": m.served_model_name,
                "version": m.version,
                "train_track": m.train_track,
                "reason_codes": m.reason_codes,
                "reason_code": primary_reason,
                "base_model_match": m.base_model_match,
                "configured_base_model": m.configured_base_model,
                "adapter_base_model": m.adapter_base_model,
                "last_probe_at": m.last_probe_at,
                "capabilities": m.capabilities or model_capabilities(m.model_id, m.model_type),
                "notes": m.notes,
                "status_label": (
                    "online"
                    if m.served
                    else (
                        "adapter_ready"
                        if m.adapter_exists and m.registered and m.model_type == "lora"
                        else ("registered" if m.registered else "unavailable")
                    )
                ),
            }
        )
    active = registry.get_active_model()
    blob = json.dumps(items)
    if "/root/" in blob or "autodl-tmp" in blob:
        raise RuntimeError("model catalog leaked host absolute path")
    return {
        "llm_enabled": bool(settings.llm_enabled),
        "default_model_id": BASE_MODEL_ID,
        "active_finetune_model_id": (active or {}).get("model_id"),
        "items": items,
    }
