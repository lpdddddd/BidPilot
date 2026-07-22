from __future__ import annotations

from uuid import UUID

from app.agent.nodes._helpers import (
    begin_node,
    finish_node,
    get_runtime,
    mark_fatal_error,
    mark_retryable_error,
    run_tool,
)
from app.agent.state import NODE_COMPLIANCE, AgentState, append_warning, touch
from app.tools.compliance_tools import (
    ProjectComplianceInput,
    run_project_compliance_check,
)


def run_compliance_check(state: AgentState) -> AgentState:
    state, skipped = begin_node(state, NODE_COMPLIANCE)
    if skipped:
        return state
    runtime = get_runtime()
    project_id = UUID(state["project_id"])  # type: ignore[arg-type]

    if state.get("compliance_run_id"):
        run_tool(
            state,
            "run_project_compliance_check",
            lambda: state["compliance_run_id"],
            summary_on_ok=lambda rid: f"reused compliance_run_id={rid}",
        )
        return finish_node(state, NODE_COMPLIANCE)

    idem = f"agent-{state['run_id']}-compliance"

    def _call():
        return run_project_compliance_check(
            runtime.db,
            ProjectComplianceInput(
                project_id=project_id,
                idempotency_key=idem,
            ),
        )

    try:
        result = run_tool(
            state,
            "run_project_compliance_check",
            _call,
            summary_on_ok=lambda r: r.detail or (r.report.run.status if r.report else None),
        )
    except Exception as exc:  # noqa: BLE001
        mark_retryable_error(state, f"{type(exc).__name__}: {exc}", "compliance_error")
        return touch(state)

    if not result.ok or result.report is None:
        mark_fatal_error(state, result.detail or "compliance failed", "compliance_error")
        return touch(state)

    report = result.report
    state["compliance_run_id"] = str(report.run.id)
    findings = list(report.findings or [])
    critical_qual = any(
        (f.severity.value if hasattr(f.severity, "value") else f.severity) == "critical"
        and (
            (f.category.value if hasattr(f.category, "value") else f.category)
            == "qualification_risk"
            or "qualification" in (f.rule_id or "").lower()
            or "qual" in (f.rule_id or "").lower()
        )
        and (f.status.value if hasattr(f.status, "value") else f.status) == "fail"
        for f in findings
    )
    if not critical_qual:
        critical_qual = any(
            (f.severity.value if hasattr(f.severity, "value") else f.severity) == "critical"
            and (f.status.value if hasattr(f.status, "value") else f.status) == "fail"
            for f in findings
        )
    meta = state.get("metadata") or {}
    if "force_critical_qualification" in meta:
        critical_qual = bool(meta["force_critical_qualification"])

    state["critical_qualification"] = critical_qual
    state["compliance_summary"] = {
        "run_id": str(report.run.id),
        "status": report.run.status.value
        if hasattr(report.run.status, "value")
        else str(report.run.status),
        "finding_count": report.finding_count,
        "critical_count": sum(
            1
            for f in findings
            if (f.severity.value if hasattr(f.severity, "value") else f.severity) == "critical"
        ),
        "error_count": sum(
            1
            for f in findings
            if (f.severity.value if hasattr(f.severity, "value") else f.severity) == "error"
        ),
        "warning_count": sum(
            1
            for f in findings
            if (f.severity.value if hasattr(f.severity, "value") else f.severity) == "warning"
        ),
        "critical_qualification": critical_qual,
    }
    if critical_qual:
        append_warning(state, "critical qualification finding detected")
    return finish_node(state, NODE_COMPLIANCE)
