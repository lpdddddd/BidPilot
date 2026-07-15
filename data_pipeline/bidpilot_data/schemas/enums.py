from __future__ import annotations

from enum import StrEnum


class DocumentType(StrEnum):
    # Legacy aliases kept for demo fixtures / backward compatibility
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
    # Official project-bundle document types
    intention_notice = "intention_notice"
    tender_notice = "tender_notice"
    tender_document = "tender_document"
    clarification = "clarification"
    award_notice = "award_notice"
    contract_notice = "contract_notice"
    acceptance_notice = "acceptance_notice"
    evaluation_result = "evaluation_result"
    sme_declaration = "sme_declaration"
    other_notice = "other_notice"


class BundleLevel(StrEnum):
    level_a = "level_a"
    level_b = "level_b"
    level_c = "level_c"
    incomplete = "incomplete"


class ParseStatus(StrEnum):
    pending = "pending"
    processing = "processing"
    success = "success"
    partial = "partial"
    failed = "failed"
    ocr_required = "ocr_required"


class SourceStatus(StrEnum):
    pending = "pending"
    downloaded = "downloaded"
    failed = "failed"
    skipped = "skipped"
    duplicate = "duplicate"


class TaxonomyCategory(StrEnum):
    project_info = "project_info"
    qualification = "qualification"
    commercial = "commercial"
    technical = "technical"
    scoring = "scoring"
    pricing = "pricing"
    contract = "contract"
    delivery = "delivery"
    service = "service"
    personnel = "personnel"
    performance = "performance"
    certification = "certification"
    financial = "financial"
    legal = "legal"
    mandatory_rejection = "mandatory_rejection"
    submission = "submission"
    other = "other"


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
    pending = "pending"


class MatchStatus(StrEnum):
    satisfied = "satisfied"
    partially_satisfied = "partially_satisfied"
    missing = "missing"
    uncertain = "uncertain"
    unknown = "unknown"  # legacy; forbidden in formal evidence-backed matches


class ReviewDecision(StrEnum):
    accept = "accept"
    corrected = "corrected"
    reject = "reject"
    skip = "skip"


class QuestionType(StrEnum):
    project_basic = "project_basic"
    qualification = "qualification"
    scoring = "scoring"
    commercial = "commercial"
    technical = "technical"
    rejection = "rejection"
    time_location = "time_location"
    evidence = "evidence"
    multi_section = "multi_section"
    unanswerable = "unanswerable"


class Difficulty(StrEnum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


class SplitName(StrEnum):
    train = "train"
    validation = "validation"
    test = "test"


class SFTTaskType(StrEnum):
    requirement_classify = "requirement_classify"
    project_info_extract = "project_info_extract"
    qualification_extract = "qualification_extract"
    scoring_extract = "scoring_extract"
    risk_detect = "risk_detect"
    evidence_match = "evidence_match"
    tool_call = "tool_call"
    citation_qa = "citation_qa"


class DerivationMethod(StrEnum):
    extract = "extract"
    classify = "classify"
    grounded_qa = "grounded_qa"
    tool_trace = "tool_trace"
