"""Tests for BidPilot auto reference dataset builder."""

from __future__ import annotations

import hashlib
from pathlib import Path

from bidpilot_data.reference_dataset.build import build_reference_dataset
from bidpilot_data.reference_dataset.schema import GENERATOR_VERSION, ReferenceSample
from bidpilot_data.reference_dataset.select import load_corpus, select_projects, selection_fingerprint
from bidpilot_data.reference_dataset.split import assign_splits
from bidpilot_data.reference_dataset.validate import (
    dedupe_samples,
    quote_contiguous_in_text,
    sample_content_hash,
    validate_sample,
)
from bidpilot_data.utils import read_jsonl, write_jsonl

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "reference"


def _install_fixture_corpus(datasets: Path) -> None:
    write_jsonl(datasets / "manifests" / "projects.jsonl", read_jsonl(FIXTURES / "projects.jsonl"))
    write_jsonl(datasets / "manifests" / "documents.jsonl", read_jsonl(FIXTURES / "documents.jsonl"))
    write_jsonl(datasets / "interim" / "chunks" / "chunks.jsonl", read_jsonl(FIXTURES / "chunks.jsonl"))
    write_jsonl(datasets / "silver" / "requirements.jsonl", read_jsonl(FIXTURES / "requirements.jsonl"))
    write_jsonl(datasets / "silver" / "evidence.jsonl", read_jsonl(FIXTURES / "evidence.jsonl"))
    write_jsonl(datasets / "silver" / "disclosed_suppliers.jsonl", read_jsonl(FIXTURES / "disclosed_suppliers.jsonl"))
    write_jsonl(datasets / "silver" / "requirement_matches.jsonl", read_jsonl(FIXTURES / "requirement_matches.jsonl"))
    write_jsonl(datasets / "eval" / "rag" / "questions.jsonl", read_jsonl(FIXTURES / "questions.jsonl"))


def _base_rag_dict(chunk: dict) -> dict:
    return {
        "sample_id": "cite-test",
        "task_type": "rag",
        "project_id": chunk["project_id"],
        "document_id": chunk["document_id"],
        "input": {"question": "截止时间是什么？"},
        "reference_output": {"answer": "2026年08月04日", "answerable": True},
        "evidence": [],
        "confidence": 0.7,
        "generation_model": "test",
        "label_source": "auto_reference",
    }


def test_selection_reproducible_with_seed(tmp_datasets: Path) -> None:
    _install_fixture_corpus(tmp_datasets)
    corpus = load_corpus(tmp_datasets)
    a = selection_fingerprint(select_projects(corpus, seed=42, max_projects=10))
    b = selection_fingerprint(select_projects(corpus, seed=42, max_projects=10))
    assert a == b
    assert a  # non-empty
    # Same seed is stable; different seed changes tie-break order when scores collide
    assert selection_fingerprint(select_projects(corpus, seed=42, max_projects=10)) == a
    # With tiny equal-score fixtures, seed may or may not change membership — only require determinism
    assert selection_fingerprint(select_projects(corpus, seed=99, max_projects=10)) == selection_fingerprint(
        select_projects(corpus, seed=99, max_projects=10)
    )


def test_quote_grounding_pass_fail() -> None:
    text = "投标人必须具备信息系统集成资质。投标文件递交截止时间为2026年08月04日。"
    assert quote_contiguous_in_text("投标文件递交截止时间为2026年08月04日。", text)
    assert quote_contiguous_in_text("投标文件  递交截止时间为2026年08月04日。", text)
    assert not quote_contiguous_in_text("完全不存在的引用句子XYZ", text)


def test_citation_empty_evidence_invalid_citation(tmp_datasets: Path) -> None:
    """Citation checks run even when evidence is empty."""
    _install_fixture_corpus(tmp_datasets)
    corpus = load_corpus(tmp_datasets)
    chunk = next(iter(corpus.chunks.values()))
    sample = ReferenceSample.model_validate(
        {
            **_base_rag_dict(chunk),
            "evidence": [],
            "citation_metadata": {
                "chunk_ids": ["does-not-exist"],
                "document_ids": [chunk["document_id"]],
                "quotes": ["完全不存在的引用句子XYZ"],
            },
        }
    )
    ok, msgs, _ = validate_sample(sample, chunk_index=corpus.chunks, document_index=corpus.documents)
    assert not ok
    assert any("missing chunk_id" in m for m in msgs)
    assert any("not grounded" in m for m in msgs)


def test_citation_empty_evidence_with_citation_fails_answerable(tmp_datasets: Path) -> None:
    _install_fixture_corpus(tmp_datasets)
    corpus = load_corpus(tmp_datasets)
    chunk = next(iter(corpus.chunks.values()))
    quote = (chunk.get("text") or "")[:40]
    sample = ReferenceSample.model_validate(
        {
            **_base_rag_dict(chunk),
            "evidence": [],
            "citation_metadata": {
                "chunk_ids": [chunk["chunk_id"]],
                "document_ids": [chunk["document_id"]],
                "quotes": [quote],
            },
        }
    )
    ok, msgs, _ = validate_sample(sample, chunk_index=corpus.chunks, document_index=corpus.documents)
    assert not ok
    assert any("citations present but evidence empty" in m for m in msgs)


def test_citation_valid_evidence_and_citation(tmp_datasets: Path) -> None:
    _install_fixture_corpus(tmp_datasets)
    corpus = load_corpus(tmp_datasets)
    chunk = next(iter(corpus.chunks.values()))
    quote = (chunk.get("text") or "")[:60]
    sample = ReferenceSample.model_validate(
        {
            **_base_rag_dict(chunk),
            "evidence": [
                {
                    "chunk_id": chunk["chunk_id"],
                    "document_id": chunk["document_id"],
                    "page_number": chunk.get("page_start") or 1,
                    "quote": quote,
                }
            ],
            "citation_metadata": {
                "chunk_ids": [chunk["chunk_id"]],
                "document_ids": [chunk["document_id"]],
                "quotes": [quote],
                "page_numbers": [chunk.get("page_start") or 1],
            },
        }
    )
    ok, msgs, _ = validate_sample(sample, chunk_index=corpus.chunks, document_index=corpus.documents)
    assert ok, msgs


def test_citation_quote_not_in_chunk(tmp_datasets: Path) -> None:
    _install_fixture_corpus(tmp_datasets)
    corpus = load_corpus(tmp_datasets)
    chunk = next(iter(corpus.chunks.values()))
    sample = ReferenceSample.model_validate(
        {
            **_base_rag_dict(chunk),
            "evidence": [
                {
                    "chunk_id": chunk["chunk_id"],
                    "document_id": chunk["document_id"],
                    "quote": (chunk.get("text") or "")[:40],
                }
            ],
            "citation_metadata": {
                "chunk_ids": [chunk["chunk_id"]],
                "document_ids": [chunk["document_id"]],
                "quotes": ["这段话完全不在任何chunk里XYZABC"],
            },
        }
    )
    ok, msgs, _ = validate_sample(sample, chunk_index=corpus.chunks, document_index=corpus.documents)
    assert not ok
    assert any("not grounded" in m for m in msgs)


def test_citation_missing_document_or_chunk_id(tmp_datasets: Path) -> None:
    _install_fixture_corpus(tmp_datasets)
    corpus = load_corpus(tmp_datasets)
    chunk = next(iter(corpus.chunks.values()))
    quote = (chunk.get("text") or "")[:40]
    sample = ReferenceSample.model_validate(
        {
            **_base_rag_dict(chunk),
            "evidence": [
                {
                    "chunk_id": chunk["chunk_id"],
                    "document_id": chunk["document_id"],
                    "quote": quote,
                }
            ],
            "citation_metadata": {
                "chunk_ids": ["missing-chunk-xyz"],
                "document_ids": ["missing-doc-xyz"],
                "quotes": [quote],
            },
        }
    )
    ok, msgs, _ = validate_sample(sample, chunk_index=corpus.chunks, document_index=corpus.documents)
    assert not ok
    assert any("missing chunk_id" in m for m in msgs)
    assert any("missing citation document_id" in m for m in msgs)


def test_unanswerable_rejects_definitive_claims(tmp_datasets: Path) -> None:
    _install_fixture_corpus(tmp_datasets)
    corpus = load_corpus(tmp_datasets)
    chunk = next(iter(corpus.chunks.values()))
    bad = ReferenceSample(
        sample_id="bad-una",
        task_type="unanswerable",
        project_id=chunk["project_id"],
        document_id=chunk["document_id"],
        input={"question": "质保巡检次数？"},
        reference_output={
            "answer": "明确要求每月巡检两次",
            "answerable": False,
            "abstain": False,
        },
        evidence=[],
        confidence=0.5,
        generation_model="test",
        generator_version=GENERATOR_VERSION,
        label_source="auto_reference",
    )
    ok, msgs, _ = validate_sample(bad, chunk_index=corpus.chunks, document_index=corpus.documents)
    assert not ok
    assert any("definitive" in m for m in msgs)

    good = bad.model_copy(
        update={
            "sample_id": "good-una",
            "reference_output": {
                "answer": "依据所给材料无法确定，证据不足。",
                "answerable": False,
                "abstain": True,
                "status": "insufficient_evidence",
            },
        }
    )
    ok2, msgs2, _ = validate_sample(good, chunk_index=corpus.chunks, document_index=corpus.documents)
    assert ok2, msgs2


def test_dedupe() -> None:
    base = {
        "sample_id": "d1",
        "task_type": "rag",
        "project_id": "p",
        "document_id": "d",
        "input": {"question": "截止时间是什么？"},
        "reference_output": {"answer": "8月4日", "answerable": True},
        "evidence": [],
        "confidence": 0.7,
        "generation_model": "test",
        "label_source": "auto_reference",
    }
    a = ReferenceSample.model_validate({**base, "sample_id": "d1"})
    b = ReferenceSample.model_validate({**base, "sample_id": "d2", "input": {"question": "截止时间是什么？  "}})
    c = ReferenceSample.model_validate(
        {**base, "sample_id": "d3", "input": {"question": "完全不同的问题"}, "reference_output": {"answer": "其他", "answerable": True}}
    )
    assert sample_content_hash(a) == sample_content_hash(b)
    kept, rejected = dedupe_samples([a, b, c])
    assert len(kept) == 2
    assert len(rejected) == 1


def test_project_isolated_splits_no_doc_leakage(tmp_datasets: Path) -> None:
    _install_fixture_corpus(tmp_datasets)
    corpus = load_corpus(tmp_datasets)
    samples: list[ReferenceSample] = []
    for i, (cid, chunk) in enumerate(sorted(corpus.chunks.items())):
        samples.append(
            ReferenceSample(
                sample_id=f"s-{i}",
                task_type="extraction",
                project_id=chunk["project_id"],
                document_id=chunk["document_id"],
                input={"text": chunk["text"][:80]},
                reference_output={"title": f"t{i}", "category": "technical", "normalized_requirement": "x", "mandatory": False},
                evidence=[],
                confidence=0.6,
                generation_model="test",
                label_source="auto_reference",
            )
        )
    updated, manifest = assign_splits(samples, seed=42, document_index=corpus.documents)
    assert manifest["ok"] is True
    assert not manifest["document_leakage"]
    assert not manifest["project_leakage"]
    # document never appears in two splits
    doc_splits: dict[str, set[str]] = {}
    for s in updated:
        doc_splits.setdefault(s.document_id, set()).add(s.split or "")
    assert all(len(v) == 1 for v in doc_splits.values())


def test_export_schema_and_min_counts(tmp_datasets: Path) -> None:
    _install_fixture_corpus(tmp_datasets)
    # Lower targets to fit tiny fixture while still exercising pipeline
    targets = {
        "rag": 3,
        "extraction": 3,
        "matching": 3,
        "compliance": 2,
        "drafting": 2,
        "unanswerable": 2,
    }
    out = tmp_datasets / "eval" / "reference"
    report = build_reference_dataset(
        seed=42,
        output_dir=out,
        dry_run=False,
        use_llm=False,
        max_retries=3,
        targets=targets,
        max_projects=10,
        datasets_root=tmp_datasets,
        build_timestamp="2026-07-22T00:00:00Z",
    )
    assert report["all_targets_met"], report
    assert (out / "reference_dataset.jsonl").exists()
    assert (out / "reference_dataset_report.json").exists()
    assert (out / "reference_dataset_summary.md").exists()
    assert (out / "splits.json").exists()
    rows = read_jsonl(out / "reference_dataset.jsonl")
    assert len(rows) >= sum(targets.values())
    for row in rows:
        parsed = ReferenceSample.model_validate(row)
        assert parsed.label_source in {"auto_reference", "silver"}
        assert parsed.generator_version == GENERATOR_VERSION
        assert parsed.task_type in targets
        # No fictional synthetic company materials
        material = str((parsed.input or {}).get("company_material") or "")
        assert "粤海信息" not in material
        assert "南粤数智" not in material
        assert "珠三角云网" not in material
        assert not (parsed.input or {}).get("synthetic_company_profile")
    matching_rows = [r for r in rows if r.get("task_type") == "matching"]
    assert matching_rows
    assert report.get("matching_status_histogram")
    # Supplier-name-only pairs must NEVER count as real bilateral evidence
    for row in matching_rows:
        method = ((row.get("data_provenance") or {}).get("method") or "")
        notes = ((row.get("data_provenance") or {}).get("notes") or "")
        cite = ((row.get("citation_metadata") or {}).get("notes") or "")
        status = ((row.get("reference_output") or {}).get("status") or "")
        if (
            method in {"company_name_only", "disclosed_supplier_bilateral"}
            or "company_name_only" in notes
            or "company_name_only" in cite
        ):
            assert status in {"insufficient_evidence", "unknown"}
            assert "real_bilateral_evidence" not in notes
    assert "matching_with_company_evidence_but_not_requirement_aligned" in report
    assert "matching_with_tender_evidence_only" in report
    # per-task files exist
    for name in (
        "rag_reference.jsonl",
        "extraction_reference.jsonl",
        "matching_reference.jsonl",
        "compliance_reference.jsonl",
        "drafting_reference.jsonl",
        "unanswerable_reference.jsonl",
        "rejected_samples.jsonl",
    ):
        assert (out / name).exists()


def test_reproducible_build_same_seed_and_timestamp(tmp_datasets: Path) -> None:
    _install_fixture_corpus(tmp_datasets)
    targets = {
        "rag": 2,
        "extraction": 2,
        "matching": 2,
        "compliance": 1,
        "drafting": 1,
        "unanswerable": 1,
    }
    ts = "2026-07-22T00:00:00Z"
    out_a = tmp_datasets / "eval" / "reference_a"
    out_b = tmp_datasets / "eval" / "reference_b"
    build_reference_dataset(
        seed=42,
        output_dir=out_a,
        datasets_root=tmp_datasets,
        targets=targets,
        build_timestamp=ts,
        max_projects=10,
        max_retries=3,
    )
    build_reference_dataset(
        seed=42,
        output_dir=out_b,
        datasets_root=tmp_datasets,
        targets=targets,
        build_timestamp=ts,
        max_projects=10,
        max_retries=3,
    )
    ha = hashlib.sha256((out_a / "reference_dataset.jsonl").read_bytes()).hexdigest()
    hb = hashlib.sha256((out_b / "reference_dataset.jsonl").read_bytes()).hexdigest()
    assert ha == hb


def test_supplier_name_only_never_counted_as_bilateral(tmp_datasets: Path) -> None:
    """Name attestation must not inflate matching_with_real_bilateral_evidence."""
    from bidpilot_data.reference_dataset.export import matching_stats
    from bidpilot_data.reference_dataset.generate import generate_matching_samples

    _install_fixture_corpus(tmp_datasets)
    corpus = load_corpus(tmp_datasets)
    selected = select_projects(corpus, seed=42, max_projects=10)
    import random

    samples = generate_matching_samples(
        corpus, selected, rng=random.Random(42), target=10
    )
    assert samples
    name_only = [
        s
        for s in samples
        if (s.data_provenance and "company_name_only" in (s.data_provenance.notes or ""))
        or (s.data_provenance and s.data_provenance.method == "company_name_only")
    ]
    # Fixture has disclosed supplier with name in chunk → expect name-only rows
    assert name_only, "expected at least one company_name_only matching sample"
    for s in name_only:
        assert (s.reference_output or {}).get("status") == "insufficient_evidence"
        assert "real_bilateral_evidence" not in (s.data_provenance.notes or "")

    stats = matching_stats(samples)
    # No silver positive matches in fixture → bilateral must be 0
    assert stats["matching_with_real_bilateral_evidence"] == 0
    assert stats["matching_with_company_evidence_but_not_requirement_aligned"] >= len(name_only)
    for s in name_only:
        # Explicitly ensure name-only rows are not in the bilateral bucket
        assert (s.reference_output or {}).get("status") not in {"supported", "partially_supported"}


def test_dry_run_fixture_mode(tmp_datasets: Path) -> None:
    _install_fixture_corpus(tmp_datasets)
    report = build_reference_dataset(
        seed=7,
        output_dir=tmp_datasets / "eval" / "reference_dry",
        dry_run=True,
        use_llm=False,
        targets={"rag": 2, "extraction": 2, "matching": 2, "compliance": 1, "drafting": 1, "unanswerable": 1},
        datasets_root=tmp_datasets,
    )
    assert "counts" in report
    assert not (tmp_datasets / "eval" / "reference_dry" / "reference_dataset.jsonl").exists()
