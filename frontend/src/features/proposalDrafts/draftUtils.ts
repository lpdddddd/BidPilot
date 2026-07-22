import type {
  EligibilityRequirementItem,
  ProposalDraftDetail,
  ProposalDraftStatus,
} from "../../types/api";
import { PROPOSAL_DRAFT_DISCLAIMER } from "../../types/api";

export const DRAFT_STATUS_LABELS: Record<ProposalDraftStatus, string> = {
  draft_pending_review: "待人工复核",
  reviewed: "已复核",
  reopened: "已重开",
  archived: "已归档",
};

export function canExportDraft(draft: Pick<
  ProposalDraftDetail,
  "status" | "export_allowed" | "has_unevidenced_manual_content"
>): boolean {
  if (draft.status !== "reviewed") return false;
  if (draft.has_unevidenced_manual_content) return false;
  if (draft.export_allowed === false) return false;
  return true;
}

export function canMarkReviewed(draft: Pick<
  ProposalDraftDetail,
  "status" | "has_unevidenced_manual_content"
>): boolean {
  if (draft.status === "reviewed") return false;
  if (draft.has_unevidenced_manual_content) return false;
  return true;
}

export function isDraftReadOnly(status: ProposalDraftStatus): boolean {
  return status === "reviewed";
}

export function partitionEligibility(items: {
  eligible: EligibilityRequirementItem[];
  excluded: EligibilityRequirementItem[];
  material_gaps: EligibilityRequirementItem[];
  risks: EligibilityRequirementItem[];
  scope_items: EligibilityRequirementItem[];
}) {
  return {
    positive: items.eligible,
    gaps: items.material_gaps,
    risks: items.risks,
    scope: items.scope_items,
    excluded: items.excluded,
  };
}

export function selectableRequirementIds(eligibility: {
  eligible: EligibilityRequirementItem[];
  material_gaps: EligibilityRequirementItem[];
  risks: EligibilityRequirementItem[];
  scope_items: EligibilityRequirementItem[];
  excluded: EligibilityRequirementItem[];
}): { defaultSelected: string[]; allSelectable: string[] } {
  const positive = eligibility.eligible.map((x) => x.requirement_id);
  const confirmedSide = [
    ...eligibility.material_gaps,
    ...eligibility.risks,
    ...eligibility.scope_items,
  ].map((x) => x.requirement_id);
  return {
    defaultSelected: positive,
    allSelectable: [...positive, ...confirmedSide, ...eligibility.excluded.map((x) => x.requirement_id)],
  };
}

export function conflictMessage(status?: number): string | null {
  if (status === 409) {
    return "发生并发冲突，请刷新后重试";
  }
  return null;
}

export { PROPOSAL_DRAFT_DISCLAIMER };
