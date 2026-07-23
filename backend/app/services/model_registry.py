"""Registered BidPilot LLM / LoRA adapters (file-backed, no secrets)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings

# Repo-relative default.
_DEFAULT_REL = (
    Path(__file__).resolve().parents[3] / "training" / "llamafactory" / "model_registry.json"
)


def _relpath_or_name(path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    try:
        root = Path(__file__).resolve().parents[3]
        return str(p.resolve().relative_to(root))
    except Exception:
        return p.name


@dataclass
class ModelRecord:
    model_id: str
    display_name: str
    base_model: str
    adapter_path: str | None
    served_name: str
    train_track: str
    version: str
    created_at: str
    notes: str
    metrics: dict[str, Any]


def registry_path() -> Path:
    settings = get_settings()
    custom = getattr(settings, "model_registry_path", "") or ""
    if custom:
        return Path(custom)
    return _DEFAULT_REL


def load_registry() -> dict[str, Any]:
    path = registry_path()
    if not path.exists():
        return {"active_model_id": None, "models": []}
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {"active_model_id": None, "models": []}
    return raw


def save_registry(data: dict[str, Any]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def register_model(
    *,
    model_id: str,
    display_name: str,
    base_model: str,
    adapter_path: str | None,
    served_name: str,
    train_track: str,
    version: str,
    notes: str = "",
    metrics: dict[str, Any] | None = None,
    activate: bool = True,
) -> dict[str, Any]:
    data = load_registry()
    models = list(data.get("models") or [])
    models = [m for m in models if m.get("model_id") != model_id]
    rec = ModelRecord(
        model_id=model_id,
        display_name=display_name,
        base_model=_relpath_or_name(base_model) or base_model,
        adapter_path=_relpath_or_name(adapter_path),
        served_name=served_name,
        train_track=train_track,
        version=version,
        created_at=datetime.now(UTC).isoformat(),
        notes=notes,
        metrics=metrics or {},
    )
    models.append(asdict(rec))
    data["models"] = models
    if activate:
        data["active_model_id"] = model_id
    save_registry(data)
    return data


def get_active_model() -> dict[str, Any] | None:
    data = load_registry()
    active = data.get("active_model_id")
    for m in data.get("models") or []:
        if isinstance(m, dict) and m.get("model_id") == active:
            return m
    return None


def public_model_info() -> dict[str, Any]:
    """Safe payload for API / UI — no absolute secret paths with credentials."""
    settings = get_settings()
    active = get_active_model()
    base = {
        "llm_enabled": bool(settings.llm_enabled),
        "served_model": settings.llm_model,
        "base_model_source": settings.llm_model_source,
        "provider": "vllm_openai_compatible" if settings.llm_enabled else "disabled",
    }
    if not active:
        return {
            **base,
            "active_finetune": None,
            "train_track": None,
            "version": None,
            "notes": "Using base served model without registered LoRA adapter.",
        }
    adapter = active.get("adapter_path")
    adapter_name = Path(str(adapter)).name if adapter else None
    base_model = active.get("base_model") or settings.llm_model_source
    # Never expose host-specific absolute roots in API responses.
    if isinstance(base_model, str) and ("/" in base_model or "\\" in base_model):
        base_model = Path(base_model).name or settings.llm_model_source
    return {
        **base,
        "active_finetune": {
            "model_id": active.get("model_id"),
            "display_name": active.get("display_name"),
            "train_track": active.get("train_track"),
            "version": active.get("version"),
            "base_model": base_model,
            "adapter_name": adapter_name,
            "metrics": active.get("metrics") or {},
            "notes": active.get("notes"),
        },
        "train_track": active.get("train_track"),
        "version": active.get("version"),
        "notes": active.get("notes"),
    }
