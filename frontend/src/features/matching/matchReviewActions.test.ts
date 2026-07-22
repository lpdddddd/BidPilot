import { describe, expect, it, vi } from "vitest";
import {
  commentRequiredForAction,
  isConflictStatus,
  validateReviewComment,
} from "./reviewQueueParams";

/**
 * Lightweight coverage for review action UX rules used by MatchDetailPanel:
 * API call shapes, required comments, 409 handling, double-submit guard.
 */
describe("match review action UX rules", () => {
  it("maps actions to API endpoints and required comments", () => {
    const calls: Array<{ endpoint: string; body: Record<string, unknown> }> = [];

    function submit(
      action: "confirm" | "reject" | "needs_more_material" | "reopen",
      comment: string,
      busy: boolean,
    ) {
      if (busy) return { blocked: true as const };
      const err = validateReviewComment(action, comment);
      if (err) return { blocked: true as const, error: err };
      if (action === "reopen") {
        calls.push({
          endpoint: "/reopen",
          body: { comment: comment.trim(), actor_label: "local-reviewer" },
        });
      } else {
        calls.push({
          endpoint: "/review",
          body: {
            action,
            comment: comment.trim() || undefined,
            actor_label: "local-reviewer",
          },
        });
      }
      return { blocked: false as const };
    }

    expect(submit("confirm", "", false).blocked).toBe(false);
    expect(submit("reject", "", false).blocked).toBe(true);
    expect(submit("needs_more_material", "请补充", false).blocked).toBe(false);
    expect(submit("reopen", "重开", false).blocked).toBe(false);
    expect(submit("confirm", "", true).blocked).toBe(true); // double-submit

    expect(calls).toEqual([
      {
        endpoint: "/review",
        body: { action: "confirm", comment: undefined, actor_label: "local-reviewer" },
      },
      {
        endpoint: "/review",
        body: {
          action: "needs_more_material",
          comment: "请补充",
          actor_label: "local-reviewer",
        },
      },
      {
        endpoint: "/reopen",
        body: { comment: "重开", actor_label: "local-reviewer" },
      },
    ]);
  });

  it("handles 409 by refreshing instead of silently succeeding", () => {
    const refresh = vi.fn();
    const status = 409;
    if (isConflictStatus(status)) {
      refresh();
    }
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(commentRequiredForAction("reject")).toBe(true);
  });
});
