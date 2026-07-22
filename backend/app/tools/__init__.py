"""Tool adapters used by agents (search, evidence, compliance checks)."""

from app.tools.agent_tools import (
    extract_requirements,
    generate_proposal_draft,
    get_project_context,
    get_proposal_draft,
    list_proposal_drafts,
    match_company_evidence,
    search_evidence,
)
from app.tools.compliance_tools import (
    check_draft_compliance,
    check_evidence_integrity,
    check_requirement_coverage,
    get_compliance_report,
    run_project_compliance_check,
)

__all__ = [
    "check_draft_compliance",
    "check_evidence_integrity",
    "check_requirement_coverage",
    "extract_requirements",
    "generate_proposal_draft",
    "get_compliance_report",
    "get_project_context",
    "get_proposal_draft",
    "list_proposal_drafts",
    "match_company_evidence",
    "run_project_compliance_check",
    "search_evidence",
]
