"""Resolve the weights path/id used by vLLM (script + compose + health)."""

from __future__ import annotations

import os
from pathlib import Path

from app.core.config import Settings, get_settings


def resolve_llm_load_target(settings: Settings | None = None) -> str:
    """Return local directory or Hub id for `vllm serve <target>`.

    Priority:
    1. LLM_MODEL_PATH if set and contains config.json
    2. Settings.llm_default_local_path if present on disk
    3. LLM_MODEL_SOURCE (Hub id)
    """
    cfg = settings or get_settings()
    explicit = (cfg.llm_model_path or "").strip()
    if explicit:
        path = Path(explicit)
        if (path / "config.json").is_file():
            return str(path.resolve())
        # Allow Hub-style values mistakenly placed in PATH.
        if "/" in explicit and not path.exists():
            return explicit
    default_local = Path(cfg.llm_default_local_path)
    if (default_local / "config.json").is_file():
        return str(default_local.resolve())
    # Env override used by shell/compose without reloading Settings.
    env_source = os.getenv("LLM_MODEL_SOURCE", "").strip()
    return env_source or cfg.llm_model_source
