"""Routing condition unit tests."""

from __future__ import annotations

from app.agent import routing
from app.agent.state import (
    NODE_DRAFT,
    NODE_FINALIZE,
    NODE_LOAD_CONTEXT,
    NODE_MATCH,
    NODE_RETRIEVE,
    NODE_REVISE,
    NODE_VALIDATE,
    empty_state,
)


def _base(**kwargs):
    s = empty_state(run_id="r", project_id="p")
    s.update(kwargs)
    return s


def test_after_initialize_missing_project():
    s = _base(project_id=None)
    assert routing.after_initialize(s) == NODE_FINALIZE
    assert s["status"] == "failed"


def test_after_initialize_authz():
    s = _base(metadata={"authz_denied": True})
    assert routing.after_initialize(s) == NODE_FINALIZE
    assert s["error_code"] == "authz_denied"


def test_after_initialize_ok():
    assert routing.after_initialize(_base()) == NODE_LOAD_CONTEXT


def test_after_load_no_docs_blocked():
    s = _base(has_documents=False)
    assert routing.after_load_context(s) == NODE_FINALIZE
    assert s["status"] == "blocked"


def test_after_extract_no_requirements():
    s = _base(has_requirements=False, requirements=[])
    assert routing.after_extract(s) == NODE_FINALIZE
    assert s["status"] == "completed_with_warnings"


def test_after_extract_block_on_no_requirements():
    s = _base(has_requirements=False, requirements=[], metadata={"block_on_no_requirements": True})
    assert routing.after_extract(s) == NODE_FINALIZE
    assert s["status"] == "blocked"


def test_after_match_continues_with_insufficient():
    s = _base(company_evidence_insufficient=True)
    assert routing.after_match(s) == "run_compliance_check"


def test_after_compliance_critical_block():
    s = _base(critical_qualification=True, metadata={"block_on_critical_qualification": True})
    assert routing.after_compliance(s) == NODE_FINALIZE
    assert s["status"] == "blocked"


def test_after_compliance_critical_risk_draft_path():
    s = _base(critical_qualification=True, metadata={"block_on_critical_qualification": False})
    assert routing.after_compliance(s) == NODE_DRAFT


def test_validate_pass_finalize():
    s = _base(draft_validation_ok=True)
    assert routing.after_validate(s) == NODE_FINALIZE


def test_validate_fail_revise():
    s = _base(draft_validation_ok=False, draft_revise_count=0)
    assert routing.after_validate(s) == NODE_REVISE


def test_validate_exceed_retries():
    s = _base(draft_validation_ok=False, draft_revise_count=2)
    assert routing.after_validate(s) == NODE_FINALIZE
    assert s["status"] == "completed_with_warnings"


def test_retryable_node_limited():
    s = _base(last_error_retryable=True, retry_counts={})
    assert routing.after_retrieve(s) == NODE_RETRIEVE
    assert s["retry_counts"][NODE_RETRIEVE] == 1
    s["last_error_retryable"] = True
    assert routing.after_retrieve(s) == NODE_RETRIEVE
    s["last_error_retryable"] = True
    assert routing.after_retrieve(s) == NODE_FINALIZE
    assert s["status"] == "failed"


def test_after_revise_to_validate():
    assert routing.after_revise(_base()) == NODE_VALIDATE


def test_after_extract_has_requirements():
    s = _base(requirements=[{"id": "1", "title": "t"}], has_requirements=True)
    assert routing.after_extract(s) == NODE_MATCH
