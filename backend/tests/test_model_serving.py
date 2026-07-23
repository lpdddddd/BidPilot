"""Unit tests for registered / adapter_exists / served model status."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from app.services import model_serving as ms


def test_check_adapter_files_ok(tmp_path: Path) -> None:
    (tmp_path / "adapter_config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "adapter_model.safetensors").write_bytes(b"x")
    ok, reasons = ms.check_adapter_files(tmp_path)
    assert ok
    assert reasons == []


def test_check_adapter_files_missing(tmp_path: Path) -> None:
    ok, reasons = ms.check_adapter_files(tmp_path / "nope")
    assert not ok
    assert ms.REASON_ADAPTER_MISSING in reasons


def test_resolve_base_served(monkeypatch) -> None:
    monkeypatch.setenv("LLM_ENABLED", "true")
    with patch.object(ms, "list_served_model_ids", return_value=(["bidpilot-qwen3-8b"], None)):
        res = ms.resolve_model_selection("qwen3-8b-base", allow_fallback=False, probe=True)
    assert res.available
    assert res.served_model_name == "bidpilot-qwen3-8b"
    assert res.model_type == "base"
    assert res.fallback_used is False


def test_resolve_lora_not_served_no_fallback(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LLM_ENABLED", "true")
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text(
        json.dumps({"r": 16, "base_model_name_or_path": "Qwen3-8B"}),
        encoding="utf-8",
    )
    (adapter / "adapter_model.safetensors").write_bytes(b"x")
    fake_reg = {
        "active_model_id": "qwen3-8b-lora-course",
        "models": [
            {
                "model_id": "qwen3-8b-lora-course",
                "display_name": "Course LoRA",
                "base_model": "Qwen3-8B",
                "adapter_path": str(adapter),
                "served_name": "bidpilot-qwen3-8b-course-lora",
                "train_track": "course_pilot",
                "version": "course-1.0",
                "notes": "",
            }
        ],
    }
    with (
        patch.object(ms.registry, "load_registry", return_value=fake_reg),
        patch.object(ms, "list_served_model_ids", return_value=(["bidpilot-qwen3-8b"], None)),
        patch.object(ms, "_adapter_dir", return_value=adapter),
    ):
        res = ms.resolve_model_selection("qwen3-8b-lora-course", allow_fallback=False, probe=True)
    assert not res.available
    assert ms.REASON_NOT_SERVED in res.reason_codes


def test_resolve_lora_fallback_records_flag(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LLM_ENABLED", "true")
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text(
        json.dumps({"r": 16, "base_model_name_or_path": "Qwen3-8B"}),
        encoding="utf-8",
    )
    (adapter / "adapter_model.safetensors").write_bytes(b"x")
    fake_reg = {
        "active_model_id": "qwen3-8b-lora-course",
        "models": [
            {
                "model_id": "qwen3-8b-lora-course",
                "display_name": "Course LoRA",
                "base_model": "Qwen3-8B",
                "adapter_path": str(adapter),
                "served_name": "bidpilot-qwen3-8b-course-lora",
                "train_track": "course_pilot",
                "version": "course-1.0",
                "notes": "",
            }
        ],
    }
    with (
        patch.object(ms.registry, "load_registry", return_value=fake_reg),
        patch.object(ms, "list_served_model_ids", return_value=(["bidpilot-qwen3-8b"], None)),
        patch.object(ms, "_adapter_dir", return_value=adapter),
    ):
        res = ms.resolve_model_selection("qwen3-8b-lora-course", allow_fallback=True, probe=True)
    assert res.available
    assert res.fallback_used is True
    assert res.resolved_model_id == "qwen3-8b-base"
    assert res.served_model_name == "bidpilot-qwen3-8b"


def test_public_payload_never_contains_absolute_root(monkeypatch) -> None:
    monkeypatch.setenv("LLM_ENABLED", "false")
    with patch.object(ms, "list_served_model_ids", return_value=([], ms.REASON_LLM_DISABLED)):
        payload = ms.public_models_payload(probe=True)
    blob = json.dumps(payload)
    assert "/root/" not in blob
    assert "autodl-tmp" not in blob
