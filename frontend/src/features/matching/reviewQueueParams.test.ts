import {
  buildReviewQueueParams,
  commentRequiredForAction,
  createDefaultReviewQueueFilters,
  DEFAULT_REVIEW_QUEUE_PARAMS,
  isConflictStatus,
  validateReviewComment,
} from "./reviewQueueParams";

describe("reviewQueueParams defaults", () => {
  it("defaults to active pending without superseded", () => {
    expect(DEFAULT_REVIEW_QUEUE_PARAMS.review_status).toBe("pending");
    expect(DEFAULT_REVIEW_QUEUE_PARAMS.include_superseded).toBe(false);
    const filters = createDefaultReviewQueueFilters();
    expect(filters.review_status).toBe("pending");
    expect(filters.include_superseded).toBe(false);
  });

  it("buildReviewQueueParams includes filters and history toggle", () => {
    const params = buildReviewQueueParams({
      ...createDefaultReviewQueueFilters(),
      match_status: "conflicting_evidence",
      has_conflict: true,
      include_superseded: true,
      requirement_category: "qualification",
    });
    expect(params.review_status).toBe("pending");
    expect(params.match_status).toBe("conflicting_evidence");
    expect(params.has_conflict).toBe(true);
    expect(params.include_superseded).toBe(true);
    expect(params.requirement_category).toBe("qualification");
  });
});

describe("review action helpers", () => {
  it("requires comment for reject / needs_more_material / reopen", () => {
    expect(commentRequiredForAction("confirm")).toBe(false);
    expect(commentRequiredForAction("reject")).toBe(true);
    expect(commentRequiredForAction("needs_more_material")).toBe(true);
    expect(commentRequiredForAction("reopen")).toBe(true);
  });

  it("blocks empty required comments", () => {
    expect(validateReviewComment("reject", "   ")).toBeTruthy();
    expect(validateReviewComment("reopen", "")).toBeTruthy();
    expect(validateReviewComment("confirm", "")).toBeNull();
    expect(validateReviewComment("reject", "证据不足")).toBeNull();
  });

  it("detects 409 conflicts", () => {
    expect(isConflictStatus(409)).toBe(true);
    expect(isConflictStatus(400)).toBe(false);
  });
});
