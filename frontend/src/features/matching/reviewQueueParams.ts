import type {
  EvidenceMatchStatus,
  MatchReviewAction,
  MatchReviewStatus,
  RequirementCategory,
  ReviewQueueParams,
  RiskLevel,
} from "../../types/api";

/** Default review-queue query: active + pending, no superseded history. */
export const DEFAULT_REVIEW_QUEUE_PARAMS: Required<
  Pick<ReviewQueueParams, "review_status" | "include_superseded" | "page" | "limit" | "sort">
> = {
  review_status: "pending",
  include_superseded: false,
  page: 1,
  limit: 20,
  sort: "created_at_desc",
};

export type ReviewQueueFilterState = {
  review_status: MatchReviewStatus | "all";
  match_status?: EvidenceMatchStatus;
  risk_level?: RiskLevel;
  requirement_category?: RequirementCategory;
  has_conflict?: boolean;
  has_scope_exclusion?: boolean;
  include_superseded: boolean;
  page: number;
  limit: number;
  sort: string;
};

export function createDefaultReviewQueueFilters(): ReviewQueueFilterState {
  return {
    review_status: DEFAULT_REVIEW_QUEUE_PARAMS.review_status,
    include_superseded: DEFAULT_REVIEW_QUEUE_PARAMS.include_superseded,
    page: DEFAULT_REVIEW_QUEUE_PARAMS.page,
    limit: DEFAULT_REVIEW_QUEUE_PARAMS.limit,
    sort: DEFAULT_REVIEW_QUEUE_PARAMS.sort,
  };
}

export function buildReviewQueueParams(filters: ReviewQueueFilterState): ReviewQueueParams {
  const params: ReviewQueueParams = {
    review_status: filters.review_status,
    include_superseded: filters.include_superseded,
    page: filters.page,
    limit: filters.limit,
    sort: filters.sort,
  };
  if (filters.match_status) params.match_status = filters.match_status;
  if (filters.risk_level) params.risk_level = filters.risk_level;
  if (filters.requirement_category) {
    params.requirement_category = filters.requirement_category;
  }
  if (filters.has_conflict !== undefined) params.has_conflict = filters.has_conflict;
  if (filters.has_scope_exclusion !== undefined) {
    params.has_scope_exclusion = filters.has_scope_exclusion;
  }
  return params;
}

export function commentRequiredForAction(action: MatchReviewAction): boolean {
  return action === "reject" || action === "needs_more_material" || action === "reopen";
}

export function validateReviewComment(
  action: MatchReviewAction,
  comment: string | null | undefined,
): string | null {
  const cleaned = (comment ?? "").trim().replace(/\s+/g, " ");
  if (commentRequiredForAction(action) && !cleaned) {
    return "请填写审核说明";
  }
  if (cleaned.length > 2000) {
    return "说明过长（最多 2000 字）";
  }
  return null;
}

export function isConflictStatus(status: number | undefined): boolean {
  return status === 409;
}
