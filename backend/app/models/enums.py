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
