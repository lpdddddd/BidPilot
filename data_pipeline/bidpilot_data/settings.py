from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_repo_root() / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://bidpilot:change_me_postgres@localhost:5432/bidpilot"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    dataset_model_name: str = Field(
        default="gpt-4o-mini",
        validation_alias=AliasChoices("DATASET_MODEL_NAME", "dataset_model_name"),
    )
    model_name: str = "gpt-4o-mini"

    repo_root: Path = Field(default_factory=_repo_root)
    pipeline_config_path: Path | None = None

    @property
    def data_pipeline_root(self) -> Path:
        return self.repo_root / "data_pipeline"

    @property
    def datasets_root(self) -> Path:
        return self.repo_root / "datasets"

    @property
    def configs_root(self) -> Path:
        return self.data_pipeline_root / "configs"

    def resolved_model_name(self) -> str:
        return self.dataset_model_name or self.model_name


_SETTINGS_OVERRIDE: Settings | None = None


@lru_cache
def _cached_settings() -> Settings:
    return Settings()


def get_settings() -> Settings:
    if _SETTINGS_OVERRIDE is not None:
        return _SETTINGS_OVERRIDE
    return _cached_settings()


def override_settings(settings: Settings | None) -> None:
    """Test helper to force a Settings instance across all modules."""
    global _SETTINGS_OVERRIDE
    _SETTINGS_OVERRIDE = settings
    _cached_settings.cache_clear()


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be mapping: {path}")
    return data


def load_pipeline_config(path: Path | None = None) -> dict[str, Any]:
    settings = get_settings()
    cfg_path = path or settings.pipeline_config_path or (settings.configs_root / "pipeline.yaml")
    return load_yaml(Path(cfg_path))


def load_taxonomy(path: Path | None = None) -> dict[str, Any]:
    settings = get_settings()
    return load_yaml(path or (settings.configs_root / "taxonomy.yaml"))


def load_sft_tasks(path: Path | None = None) -> dict[str, Any]:
    settings = get_settings()
    return load_yaml(path or (settings.configs_root / "sft_tasks.yaml"))
