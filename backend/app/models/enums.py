from enum import StrEnum


class MemberRole(StrEnum):
    owner = "owner"
    admin = "admin"
    manager = "manager"
    member = "member"
    reviewer = "reviewer"


class ProjectStatus(StrEnum):
    draft = "draft"
    parsing = "parsing"
    analyzing = "analyzing"
    reviewing = "reviewing"
    completed = "completed"
    archived = "archived"


class DocumentType(StrEnum):
    tender = "tender"
    announcement = "announcement"
    amendment = "amendment"
    result = "result"
    contract = "contract"
    company_profile = "company_profile"
    qualification = "qualification"
    case = "case"
    personnel = "personnel"
    product = "product"
    other = "other"


class ParseStatus(StrEnum):
    pending = "pending"
    processing = "processing"
    success = "success"
    partial = "partial"
    ocr_required = "ocr_required"
    failed = "failed"


class RequirementCategory(StrEnum):
    project_info = "project_info"
    qualification = "qualification"
    commercial = "commercial"
    technical = "technical"
    scoring = "scoring"
    material = "material"
    deadline = "deadline"
    mandatory = "mandatory"
    invalid_bid = "invalid_bid"
    contract = "contract"


class RiskLevel(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class QualityLevel(StrEnum):
    gold = "gold"
    silver = "silver"
    pending = "pending"


class ReviewStatus(StrEnum):
    reviewed = "reviewed"
    auto_checked = "auto_checked"
    unreviewed = "unreviewed"


class MatchStatus(StrEnum):
    satisfied = "satisfied"
    partially_satisfied = "partially_satisfied"
    missing = "missing"
    uncertain = "uncertain"


class MessageRole(StrEnum):
    system = "system"
    user = "user"
    assistant = "assistant"
    tool = "tool"


class AgentRunStatus(StrEnum):
    pending = "pending"
    running = "running"
    waiting_for_user = "waiting_for_user"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ExtractionRunStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


# Match runs reuse ExtractionRunStatus values (queued/running/succeeded/failed/cancelled).
MatchRunStatus = ExtractionRunStatus


class EvidenceMatchStatus(StrEnum):
    supported = "supported"
    partially_supported = "partially_supported"
    insufficient_evidence = "insufficient_evidence"
    conflicting_evidence = "conflicting_evidence"
    not_applicable = "not_applicable"


class MatchReviewStatus(StrEnum):
    pending = "pending"
    confirmed = "confirmed"
    rejected = "rejected"
    needs_more_material = "needs_more_material"


class MatchReviewAction(StrEnum):
    confirm = "confirm"
    reject = "reject"
    needs_more_material = "needs_more_material"
    reopen = "reopen"


class MatchReviewReasonCode(StrEnum):
    evidence_insufficient = "evidence_insufficient"
    evidence_incorrect = "evidence_incorrect"
    status_incorrect = "status_incorrect"
    scope_unclear = "scope_unclear"
    needs_updated_material = "needs_updated_material"
    other = "other"


class ActorAuthn(StrEnum):
    authenticated = "authenticated"
    unverified_local_operator = "unverified_local_operator"


class ProposalDraftStatus(StrEnum):
    draft_pending_review = "draft_pending_review"
    reviewed = "reviewed"
    reopened = "reopened"
    archived = "archived"


class ProposalDraftVersionKind(StrEnum):
    generated = "generated"
    manual_revision = "manual_revision"


class ProposalDraftSourceRole(StrEnum):
    tender_requirement = "tender_requirement"
    company_support = "company_support"
    company_conflict = "company_conflict"
    company_scope_exclusion = "company_scope_exclusion"


class ProposalDraftReviewAction(StrEnum):
    mark_reviewed = "mark_reviewed"
    reopen = "reopen"


class ProposalDraftGenerationMode(StrEnum):
    response_outline = "response_outline"
    compliance_preparation_pack = "compliance_preparation_pack"


# Generation runs reuse ExtractionRunStatus values.
ProposalDraftRunStatus = ExtractionRunStatus

# Compliance runs reuse ExtractionRunStatus values (queued/running/succeeded/failed).
ComplianceRunStatus = ExtractionRunStatus


class ComplianceSeverity(StrEnum):
    info = "info"
    warning = "warning"
    error = "error"
    critical = "critical"


class ComplianceFindingStatus(StrEnum):
    """Finding outcome. Member ``pass_`` maps to DB/API value ``pass``."""

    pass_ = "pass"
    fail = "fail"
    unknown = "unknown"


class ComplianceRuleCategory(StrEnum):
    coverage = "coverage"
    evidence = "evidence"
    qualification_risk = "qualification_risk"
    draft_safety = "draft_safety"
    consistency = "consistency"
    engine = "engine"
