"""Model availability: registered / adapter_exists / served (no absolute paths in API)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

from app.core.config import get_settings
from app.services import model_registry as registry

ModelType = Literal["base", "lora"]

# Stable public ids (not host paths).
BASE_MODEL_ID = "qwen3-8b-base"
COURSE_LORA_MODEL_ID = "qwen3-8b-lora-course"

REASON_NOT_REGISTERED = "not_registered"
REASON_ADAPTER_MISSING = "adapter_missing"
REASON_ADAPTER_INCOMPLETE = "adapter_incomplete"
REASON_NOT_SERVED = "model_not_served"
REASON_LLM_DISABLED = "provider_not_configured"
REASON_UNREACHABLE = "llm_unreachable"
REASON_BASE_MISMATCH = "base_model_mismatch"
REASON_UNKNOWN_MODEL = "unknown_model_id"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _adapter_dir(rel_or_name: str | None) -> Path | None:
    if not rel_or_name:
        return None
    p = Path(rel_or_name)
    if p.is_absolute():
        # Never expose absolute paths in API; still allow local existence checks.
        return p if p.exists() else None
    candidate = repo_root() / p
    return candidate if candidate.exists() else None


def check_adapter_files(adapter_path: Path | None) -> tuple[bool, list[str]]:
    """Return (ok, reason_codes)."""
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

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def _base_status(served_ids: list[str], probe_err: str | None) -> ModelStatusView:
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
        adapter_exists=True,  # base has no adapter; treat as N/A-success for UI
        served=served and probe_err is None,
        served_model_name=served_name,
        version="base",
        train_track=None,
        reason_codes=reasons,
        notes="基座模型（无 Adapter）",
    )


def _lora_status_from_record(
    rec: dict[str, Any],
    served_ids: list[str],
    probe_err: str | None,
) -> ModelStatusView:
    model_id = str(rec.get("model_id") or "")
    served_name = str(rec.get("served_name") or model_id)
    adapter_rel = rec.get("adapter_path")
    adapter_path = _adapter_dir(str(adapter_rel) if adapter_rel else None)
    adapter_ok, adapter_reasons = check_adapter_files(adapter_path)
    reasons: list[str] = []
    registered = True
    if not adapter_ok:
        reasons.extend(adapter_reasons)
    # Soft base match: adapter_config base folder name vs known Qwen3-8B
    if adapter_ok and adapter_path is not None:
        try:
            cfg = json.loads((adapter_path / "adapter_config.json").read_text(encoding="utf-8"))
            base_name = Path(str(cfg.get("base_model_name_or_path") or "")).name
            if base_name and "Qwen3-8B" not in base_name and "qwen3-8b" not in base_name.lower():
                reasons.append(REASON_BASE_MISMATCH)
        except Exception:  # noqa: BLE001
            reasons.append(REASON_ADAPTER_INCOMPLETE)
            adapter_ok = False
    served = served_name in served_ids
    if probe_err:
        reasons.append(probe_err)
    elif not served:
        reasons.append(REASON_NOT_SERVED)
    return ModelStatusView(
        model_id=model_id,
        display_name=str(rec.get("display_name") or model_id),
        model_type="lora",
        registered=registered,
        adapter_exists=adapter_ok,
        served=bool(served and adapter_ok and probe_err is None),
        served_model_name=served_name,
        version=str(rec.get("version") or "") or None,
        train_track=str(rec.get("train_track") or "") or None,
        reason_codes=list(dict.fromkeys(reasons)),
        notes=str(rec.get("notes") or "") or None,
    )


def list_model_statuses(*, probe: bool = True) -> list[ModelStatusView]:
    served_ids: list[str] = []
    probe_err: str | None = None
    if probe:
        served_ids, probe_err = list_served_model_ids()
    out: list[ModelStatusView] = [_base_status(served_ids, probe_err)]
    data = registry.load_registry()
    for rec in data.get("models") or []:
        if not isinstance(rec, dict):
            continue
        out.append(_lora_status_from_record(rec, served_ids, probe_err))
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
) -> ModelResolution:
    """Map public model_id → served name. Never silently substitutes LoRA with Base
    unless allow_fallback=True (and then records fallback_used)."""
    requested = (model_id or BASE_MODEL_ID).strip() or BASE_MODEL_ID

    statuses = {m.model_id: m for m in list_model_statuses(probe=probe)}
    # Also allow selecting by served name for convenience.
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
        )

    # Unavailable path
    reasons = list(status.reason_codes) or [REASON_NOT_SERVED]
    if allow_fallback and status.model_type == "lora":
        base = statuses.get(BASE_MODEL_ID)
        if base and base.served and base.served_model_name:
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
    )


def public_models_payload(*, probe: bool = True) -> dict[str, Any]:
    """Safe payload for Dashboard / Evaluation model pickers."""
    settings = get_settings()
    items = []
    for m in list_model_statuses(probe=probe):
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
                "notes": m.notes,
                # Convenience for UI copy (structured codes kept above).
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
    return {
        "llm_enabled": bool(settings.llm_enabled),
        "default_model_id": BASE_MODEL_ID,
        "active_finetune_model_id": (active or {}).get("model_id"),
        "items": items,
    }
