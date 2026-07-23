"""Model registry unit tests."""

from __future__ import annotations

from pathlib import Path

from app.services import model_registry as mr


def test_register_and_public_info(tmp_path: Path, monkeypatch):
    reg = tmp_path / "model_registry.json"
    monkeypatch.setattr(mr, "registry_path", lambda: reg)
    mr.register_model(
        model_id="demo-lora",
        display_name="Demo LoRA",
        base_model="Qwen3-8B",
        adapter_path="training/llamafactory/outputs/demo",
        served_name="bidpilot-qwen3-8b",
        train_track="course_pilot",
        version="t-1",
        notes="unit",
        metrics={"train_loss": 1.0},
        activate=True,
    )
    info = mr.public_model_info()
    assert info["version"] == "t-1"
    assert info["active_finetune"]["adapter_name"] == "demo"
    assert info["active_finetune"]["train_track"] == "course_pilot"
    assert "/root/" not in str(info)
