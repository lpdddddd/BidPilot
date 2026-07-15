from __future__ import annotations

import json
from pathlib import Path

import pytest

from bidpilot_data.settings import Settings, override_settings


@pytest.fixture()
def tmp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    datasets = repo / "datasets"
    for p in [
        datasets / "manifests",
        datasets / "raw" / "documents",
        datasets / "interim" / "parsed",
        datasets / "interim" / "cleaned",
        datasets / "interim" / "chunks",
        datasets / "interim" / "candidates",
        datasets / "silver",
        datasets / "gold",
        datasets / "review" / "pending",
        datasets / "review" / "exported",
        datasets / "review" / "imported",
        datasets / "eval" / "rag",
        datasets / "eval" / "agent",
        datasets / "sft" / "source",
        datasets / "sft" / "train",
        datasets / "sft" / "validation",
        datasets / "sft" / "test",
        datasets / "rejected",
        datasets / "reports" / "checkpoints",
        repo / "training" / "llamafactory" / "data",
        repo / "data_pipeline" / "configs",
        repo / "demo_data",
        repo / "backend",
    ]:
        p.mkdir(parents=True, exist_ok=True)

    src_cfg = Path(__file__).resolve().parents[1] / "configs"
    for name in ("pipeline.yaml", "taxonomy.yaml", "sft_tasks.yaml", "sft_balance.yaml"):
        src = src_cfg / name
        if src.exists():
            (repo / "data_pipeline" / "configs" / name).write_text(
                src.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
    (repo / "training" / "llamafactory" / "data" / "dataset_info.json").write_text(
        json.dumps(
            {
                "bidpilot_sample_sharegpt": {
                    "file_name": "sample_sharegpt.json",
                    "formatting": "sharegpt",
                    "columns": {"messages": "messages"},
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    override_settings(Settings(repo_root=repo))
    yield repo
    override_settings(None)


@pytest.fixture()
def tmp_datasets(tmp_repo: Path) -> Path:
    return tmp_repo / "datasets"
