"""Unit tests for registered / adapter_exists / served + base match."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from app.schemas.ask import AskRequest
from app.services import model_serving as ms
from app.services.evaluation.report import build_report_dict
from app.services.rag_answer_service import RagAnswerService
from fastapi import HTTPException
from sqlalchemy.orm import Session


def _write_adapter(
    tmp_path: Path,
    *,
    base: str = "Qwen/Qwen3-8B",
    rank: int = 16,
    weights: bool = True,
    config: bool = True,
) -> Path:
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    if config:
        (adapter / "adapter_config.json").write_text(
            json.dumps({"r": rank, "base_model_name_or_path": base, "peft_type": "LORA"}),
            encoding="utf-8",
        )
    if weights:
        (adapter / "adapter_model.safetensors").write_bytes(b"x")
    return adapter


def _fake_reg(adapter: Path) -> dict:
    return {
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
                "notes": "course_pilot",
            }
        ],
    }


def test_check_adapter_files_ok(tmp_path: Path) -> None:
    adapter = _write_adapter(tmp_path)
    ok, reasons = ms.check_adapter_files(adapter)
    assert ok and reasons == []


def test_adapter_dir_missing(tmp_path: Path) -> None:
    ok, reasons = ms.check_adapter_files(tmp_path / "nope")
    assert not ok and ms.REASON_ADAPTER_MISSING in reasons


def test_adapter_config_missing(tmp_path: Path) -> None:
    adapter = _write_adapter(tmp_path, config=False)
    ok, reasons = ms.check_adapter_files(adapter)
    assert not ok and ms.REASON_ADAPTER_INCOMPLETE in reasons


def test_adapter_weights_missing(tmp_path: Path) -> None:
    adapter = _write_adapter(tmp_path, weights=False)
    ok, reasons = ms.check_adapter_files(adapter)
    assert not ok and ms.REASON_ADAPTER_INCOMPLETE in reasons


def test_canonicalize_hub_and_basename() -> None:
    assert ms.canonicalize_base_identity("Qwen/Qwen3-8B") == "Qwen/Qwen3-8B"
    assert ms.canonicalize_base_identity("Qwen3-8B") == "Qwen/Qwen3-8B"
    assert ms.canonicalize_base_identity("/data/models/Qwen3-8B") == "Qwen/Qwen3-8B"
    assert ms.canonicalize_base_identity("SomeOtherModel") is None


def test_base_match_exact_and_snapshot(tmp_path: Path) -> None:
    snap = tmp_path / "Qwen3-8B"
    snap.mkdir()
    (snap / "config.json").write_text(
        json.dumps({"architectures": ["Qwen3ForCausalLM"], "_name_or_path": "Qwen/Qwen3-8B"}),
        encoding="utf-8",
    )
    assert ms.compare_base_models("Qwen/Qwen3-8B", str(snap)) == "match"
    assert ms.compare_base_models("Qwen/Qwen3-8B", "Qwen3-8B") == "match"


def test_base_mismatch() -> None:
    assert ms.compare_base_models("Qwen/Qwen3-8B", "meta-llama/Llama-3-8B") == "mismatch"


def test_base_unverified() -> None:
    assert ms.compare_base_models("weird-local-name", "another-weird") == "unverified"


def test_validate_rank_exceeded(tmp_path: Path) -> None:
    adapter = _write_adapter(tmp_path, rank=64)
    result = ms.validate_adapter_for_serving(
        adapter, configured_base="Qwen/Qwen3-8B", max_lora_rank=16
    )
    assert not result["adapter_exists"]
    assert ms.REASON_RANK_EXCEEDED in result["reason_codes"]


def test_validate_mismatch_blocks_adapter_exists(tmp_path: Path) -> None:
    adapter = _write_adapter(tmp_path, base="meta-llama/Llama-3-8B")
    result = ms.validate_adapter_for_serving(
        adapter, configured_base="Qwen/Qwen3-8B", max_lora_rank=16
    )
    assert result["files_ok"]
    assert not result["adapter_exists"]
    assert result["base_model_match"] == "mismatch"
    assert ms.REASON_BASE_MISMATCH in result["reason_codes"]


def test_served_only_base(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LLM_ENABLED", "true")
    adapter = _write_adapter(tmp_path)
    with (
        patch.object(ms.registry, "load_registry", return_value=_fake_reg(adapter)),
        patch.object(ms, "list_served_model_ids", return_value=(["bidpilot-qwen3-8b"], None)),
        patch.object(ms, "_adapter_dir", return_value=adapter),
        patch.object(ms, "configured_base_for_compare", return_value="Qwen/Qwen3-8B"),
    ):
        statuses = {m.model_id: m for m in ms.list_model_statuses(probe=True)}
    assert statuses["qwen3-8b-base"].served is True
    assert statuses["qwen3-8b-lora-course"].adapter_exists is True
    assert statuses["qwen3-8b-lora-course"].served is False
    assert ms.REASON_NOT_SERVED in statuses["qwen3-8b-lora-course"].reason_codes


def test_served_base_and_lora(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LLM_ENABLED", "true")
    adapter = _write_adapter(tmp_path)
    ids = ["bidpilot-qwen3-8b", "bidpilot-qwen3-8b-course-lora"]
    with (
        patch.object(ms.registry, "load_registry", return_value=_fake_reg(adapter)),
        patch.object(ms, "list_served_model_ids", return_value=(ids, None)),
        patch.object(ms, "_adapter_dir", return_value=adapter),
        patch.object(ms, "configured_base_for_compare", return_value="Qwen/Qwen3-8B"),
    ):
        statuses = {m.model_id: m for m in ms.list_model_statuses(probe=True)}
    assert statuses["qwen3-8b-lora-course"].served is True


def test_models_probe_failure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LLM_ENABLED", "true")
    adapter = _write_adapter(tmp_path)
    with (
        patch.object(ms.registry, "load_registry", return_value=_fake_reg(adapter)),
        patch.object(ms, "list_served_model_ids", return_value=([], ms.REASON_UNREACHABLE)),
        patch.object(ms, "_adapter_dir", return_value=adapter),
        patch.object(ms, "configured_base_for_compare", return_value="Qwen/Qwen3-8B"),
    ):
        statuses = {m.model_id: m for m in ms.list_model_statuses(probe=True)}
    assert statuses["qwen3-8b-lora-course"].served is False
    assert ms.REASON_UNREACHABLE in statuses["qwen3-8b-lora-course"].reason_codes


def test_mismatch_never_served_even_if_vllm_lists(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LLM_ENABLED", "true")
    adapter = _write_adapter(tmp_path, base="meta-llama/Llama-3-8B")
    ids = ["bidpilot-qwen3-8b", "bidpilot-qwen3-8b-course-lora"]
    with (
        patch.object(ms.registry, "load_registry", return_value=_fake_reg(adapter)),
        patch.object(ms, "list_served_model_ids", return_value=(ids, None)),
        patch.object(ms, "_adapter_dir", return_value=adapter),
        patch.object(ms, "configured_base_for_compare", return_value="Qwen/Qwen3-8B"),
    ):
        st = ms.get_model_status("qwen3-8b-lora-course", probe=True)
    assert st is not None
    assert st.adapter_exists is False
    assert st.served is False
    assert ms.REASON_BASE_MISMATCH in st.reason_codes


def test_resolve_fallback_and_no_silent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LLM_ENABLED", "true")
    adapter = _write_adapter(tmp_path)
    with (
        patch.object(ms.registry, "load_registry", return_value=_fake_reg(adapter)),
        patch.object(ms, "list_served_model_ids", return_value=(["bidpilot-qwen3-8b"], None)),
        patch.object(ms, "_adapter_dir", return_value=adapter),
        patch.object(ms, "configured_base_for_compare", return_value="Qwen/Qwen3-8B"),
    ):
        denied = ms.resolve_model_selection(
            "qwen3-8b-lora-course", allow_fallback=False, probe=True
        )
        allowed = ms.resolve_model_selection(
            "qwen3-8b-lora-course", allow_fallback=True, probe=True
        )
    assert denied.available is False and denied.fallback_used is False
    assert allowed.available is True and allowed.fallback_used is True
    assert allowed.served_model_name == "bidpilot-qwen3-8b"


def test_public_payload_no_absolute_path(monkeypatch) -> None:
    monkeypatch.setenv("LLM_ENABLED", "false")
    with patch.object(ms, "list_served_model_ids", return_value=([], ms.REASON_LLM_DISABLED)):
        payload = ms.public_models_payload(probe=True)
    blob = json.dumps(payload)
    assert "/root/" not in blob and "autodl-tmp" not in blob


class _FakeLlm:
    enabled = True
    model = "test-llm"

    def __init__(self, content: str = "ok [S1]。") -> None:
        self.content = content
        self.chat_calls: list[dict] = []

    def chat(self, messages, **kwargs):  # noqa: ANN001
        self.chat_calls.append({"messages": messages, **kwargs})
        return MagicMock(
            content=self.content,
            model=self.model,
            latency_ms=1.0,
            finish_reason="stop",
            request_id="r1",
        )


def test_ask_base_and_lora_use_resolved_served_names(monkeypatch) -> None:
    from app.schemas.search import SearchResponse

    from tests.test_rag_ask import FakeRetrieval, _item, _trace

    pid = __import__("uuid").uuid4()
    item = _item(chunk_id="c1", document_id="d1", content="须具备营业执照")
    retrieval = FakeRetrieval(
        SearchResponse(query="q", results=[item], trace=_trace(returned_count=1))
    )

    def fake_resolve(model_id, **kwargs):  # noqa: ANN001
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
        )

    monkeypatch.setattr("app.services.rag_answer_service.resolve_model_selection", fake_resolve)
    # Force LlmClient path by using real LlmClient mock via isinstance branch:
    # inject FakeLlm (non-LlmClient) — still records generation_trace model ids.
    for mid, expected in (
        (ms.BASE_MODEL_ID, "bidpilot-qwen3-8b"),
        (ms.COURSE_LORA_MODEL_ID, "bidpilot-qwen3-8b-course-lora"),
    ):
        llm = _FakeLlm("须具备营业执照 [S1]。")
        # Patch isinstance path: FakeLlm is not LlmClient, uses injected client.
        # Generation trace served name comes from resolution; override by making
        # resolve return expected and FakeLlm still used.
        svc = RagAnswerService(db=MagicMock(), retrieval=retrieval, llm=llm)  # type: ignore[arg-type]

        # Monkeypatch _resolve_llm to use fake_resolve + FakeLlm
        def _res(req, mid=mid, llm=llm):  # noqa: ANN001
            return llm, fake_resolve(mid)

        monkeypatch.setattr(svc, "_resolve_llm", _res)
        resp = svc.answer(pid, AskRequest(question="资质？", model_id=mid))
        assert resp.generation_trace is not None
        assert resp.generation_trace.requested_model_id == mid
        assert resp.generation_trace.served_model_name == expected
        assert resp.generation_trace.fallback_used is False


def test_ask_lora_unavailable_rejects(monkeypatch) -> None:
    from app.schemas.search import SearchResponse

    from tests.test_rag_ask import FakeRetrieval, _item, _trace

    pid = __import__("uuid").uuid4()
    item = _item(chunk_id="c1", document_id="d1", content="正文")
    retrieval = FakeRetrieval(
        SearchResponse(query="q", results=[item], trace=_trace(returned_count=1))
    )
    llm = _FakeLlm()
    svc = RagAnswerService(db=MagicMock(), retrieval=retrieval, llm=llm)  # type: ignore[arg-type]

    def boom(_req):  # noqa: ANN001
        raise HTTPException(status_code=503, detail={"reason_codes": ["model_not_served"]})

    monkeypatch.setattr(svc, "_resolve_llm", boom)
    with pytest.raises(HTTPException) as exc:
        svc.answer(pid, AskRequest(question="问", model_id=ms.COURSE_LORA_MODEL_ID))
    assert exc.value.status_code == 503


def test_evaluation_export_includes_model_metadata() -> None:
    run = MagicMock()
    run.id = "00000000-0000-0000-0000-000000000001"
    run.status = MagicMock(value="completed")
    run.dataset_hash = "abc"
    run.evaluator_version = "v1"
    run.target_type = MagicMock(value="rag")
    run.target_config_snapshot = {
        "model_id": "qwen3-8b-lora-course",
        "model_display_name": "Course LoRA",
        "model_type": "lora",
        "adapter_version": "course-1.0",
        "served_model_name": "bidpilot-qwen3-8b-course-lora",
        "dataset_version": "abc",
    }
    run.seed = 14
    run.source_commit_sha = "deadbeef"
    run.overall_score = 0.1
    run.summary_json = {"pass_rate": 0.0, "error_rate": 0.0, "reference_coverage": 1.0}
    run.started_at = None
    run.finished_at = None
    run.duration_ms = 10
    report = build_report_dict(run, [])
    assert report["model"]["model_id"] == "qwen3-8b-lora-course"
    assert report["model"]["served_model_name"] == "bidpilot-qwen3-8b-course-lora"
    assert report["model"]["model_type"] == "lora"
    assert report["model"]["adapter_version"] == "course-1.0"
    assert report["model"]["git_commit"] == "deadbeef"


def test_evaluation_runner_snapshots_model_id(monkeypatch, db: Session) -> None:
    from app.models import BidProject, Organization
    from app.services.evaluation.service import EvaluationService
    from app.services.model_serving import ModelResolution

    org = Organization(name="ModelSnap")
    db.add(org)
    db.flush()
    proj = BidProject(organization_id=org.id, project_code="MS1", project_name="MS")
    db.add(proj)
    db.commit()

    resolution = ModelResolution(
        available=True,
        requested_model_id=ms.COURSE_LORA_MODEL_ID,
        resolved_model_id=ms.COURSE_LORA_MODEL_ID,
        served_model_name="bidpilot-qwen3-8b-course-lora",
        model_type="lora",
        adapter_version="course-1.0",
        train_track="course_pilot",
        fallback_used=False,
        reason_codes=[],
        display_name="BidPilot Course LoRA",
    )

    class Cap:
        available = True
        reason = None
        reason_code = None

    class FakeTarget:
        def capability(self):
            return Cap()

    monkeypatch.setattr(
        "app.services.model_serving.resolve_model_selection",
        lambda *a, **k: resolution,
    )
    monkeypatch.setattr(
        "app.services.evaluation.targets.get_target",
        lambda *a, **k: FakeTarget(),
    )

    fixture = str(Path(__file__).parent / "fixtures" / "evaluation" / "mini_suite.jsonl")
    svc = EvaluationService(db)
    run, _ = svc.create_run(
        proj.id,
        {
            "target": "deterministic_fake",
            "fixture_path": fixture,
            "target_config": {"model_id": ms.COURSE_LORA_MODEL_ID},
            "case_limit": 1,
            "seed": 14,
        },
        idempotency_key="model-snap-1",
        execute=False,
    )
    snap = run.target_config_snapshot or {}
    assert snap["model_id"] == ms.COURSE_LORA_MODEL_ID
    assert snap["served_model_name"] == "bidpilot-qwen3-8b-course-lora"
    assert snap["model_type"] == "lora"
    assert snap["adapter_version"] == "course-1.0"
    assert snap.get("requested_model_id") in (None, ms.COURSE_LORA_MODEL_ID)


def test_evaluation_compare_surfaces_base_vs_lora_models(db: Session) -> None:
    from app.models import BidProject, Organization
    from app.models.enums import EvaluationRunStatus, EvaluationTargetType
    from app.models.evaluation import EvaluationRun, EvaluationSuite
    from app.services.evaluation.service import EvaluationService

    org = Organization(name="CmpOrg")
    db.add(org)
    db.flush()
    proj = BidProject(organization_id=org.id, project_code="C1", project_name="Cmp")
    db.add(proj)
    db.flush()
    suite = EvaluationSuite(
        name="mini",
        version="1.0.0",
        dataset_hash="hash14",
        evaluator_profile_version="bidpilot-eval-1.0.0",
    )
    db.add(suite)
    db.flush()

    def _run(mid: str, served: str, mtype: str, adapter: str) -> EvaluationRun:
        run = EvaluationRun(
            project_id=proj.id,
            suite_id=suite.id,
            status=EvaluationRunStatus.completed,
            target_type=EvaluationTargetType.rag,
            target_config_snapshot={
                "model_id": mid,
                "served_model_name": served,
                "model_type": mtype,
                "adapter_version": adapter,
                "model_display_name": mid,
            },
            dataset_hash="hash14",
            evaluator_version="bidpilot-eval-1.0.0",
            seed=14,
            total_cases=1,
            completed_cases=1,
            passed_cases=0,
            failed_cases=1,
            error_cases=0,
            overall_score=0.0,
            summary_json={"pass_rate": 0.0, "error_rate": 0.0, "reference_coverage": 1.0},
        )
        db.add(run)
        db.flush()
        return run

    left = _run("qwen3-8b-base", "bidpilot-qwen3-8b", "base", "base")
    right = _run(
        "qwen3-8b-lora-course",
        "bidpilot-qwen3-8b-course-lora",
        "lora",
        "course-1.0",
    )
    db.commit()
    cmp = EvaluationService(db).compare(proj.id, left.id, right.id)
    changed = (cmp.get("config_diff") or {}).get("changed") or {}
    assert changed["model_id"]["left"] == "qwen3-8b-base"
    assert changed["model_id"]["right"] == "qwen3-8b-lora-course"
    assert changed["served_model_name"]["left"] == "bidpilot-qwen3-8b"
    assert changed["served_model_name"]["right"] == "bidpilot-qwen3-8b-course-lora"
