"""Resolve the weights path/id used by vLLM (script + compose + health)."""

from __future__ import annotations

import os
from pathlib import Path

from app.core.config import Settings, get_settings


def resolve_llm_load_target(settings: Settings | None = None) -> str:
    """Return local directory or Hub id for `vllm serve <target>`.

    Priority:
    1. LLM_MODEL_PATH if set and contains config.json
    2. LLM_MODEL_SOURCE (Hub id)

    Never auto-picks a machine-specific directory; local path must be explicit.
    """
    cfg = settings or get_settings()
    explicit = (cfg.llm_model_path or os.getenv("LLM_MODEL_PATH", "")).strip()
    if explicit:
        path = Path(explicit)
        if (path / "config.json").is_file():
            return str(path.resolve())
        raise FileNotFoundError(
            f"LLM_MODEL_PATH={explicit!r} is set but config.json was not found"
        )
    env_source = os.getenv("LLM_MODEL_SOURCE", "").strip()
    return env_source or cfg.llm_model_source
