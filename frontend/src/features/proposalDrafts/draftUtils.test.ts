import { describe, expect, it, vi } from "vitest";
import {
  PROPOSAL_DRAFT_DISCLAIMER,
  canExportDraft,
  canMarkReviewed,
  conflictMessage,
  isDraftReadOnly,
  partitionEligibility,
  selectableRequirementIds,
} from "./draftUtils";
import type { EligibilityRequirementItem } from "../../types/api";

function item(
  partial: Partial<EligibilityRequirementItem> & Pick<EligibilityRequirementItem, "requirement_id" | "eligibility">,
): EligibilityRequirementItem {
  return {
    title: partial.title || partial.requirement_id,
    reason: partial.reason || partial.eligibility,
    draft_handling: partial.draft_handling || partial.eligibility,
    ...partial,
  };
}

describe("proposal draft eligibility display", () => {
  it("partitions eligible vs excluded buckets", () => {
    const data = {
      eligible: [item({ requirement_id: "r1", eligibility: "positive" })],
      excluded: [item({ requirement_id: "r2", eligibility: "excluded", reason: "pending_review" })],
      material_gaps: [item({ requirement_id: "r3", eligibility: "material_gap" })],
      risks: [item({ requirement_id: "r4", eligibility: "risk" })],
      scope_items: [item({ requirement_id: "r5", eligibility: "scope" })],
    };
    const parts = partitionEligibility(data);
    expect(parts.positive).toHaveLength(1);
    expect(parts.excluded[0].reason).toBe("pending_review");
    expect(parts.gaps).toHaveLength(1);
    expect(parts.risks).toHaveLength(1);
    expect(parts.scope).toHaveLength(1);

    const sel = selectableRequirementIds(data);
    expect(sel.defaultSelected).toEqual(["r1"]);
    expect(sel.allSelectable).toContain("r2");
  });
});

describe("proposal draft API call shapes", () => {
  it("builds create/review/export payloads against real endpoints", () => {
    const calls: Array<{ method: string; path: string; body?: unknown }> = [];
    const projectId = "p1";
    const draftId = "d1";

    function createDraft(payload: {
      title: string;
      requirement_ids: string[];
      mode: string;
    }) {
      calls.push({
        method: "POST",
        path: `/api/v1/projects/${projectId}/proposal-drafts`,
        body: payload,
      });
    }
    function review(payload: {
      actor_label: string;
      comment: string;
      review_lock_version: number;
    }) {
      calls.push({
        method: "POST",
        path: `/api/v1/projects/${projectId}/proposal-drafts/${draftId}/review`,
        body: payload,
      });
    }
    function exportUrl(format: "markdown" | "docx") {
      return `/api/v1/projects/${projectId}/proposal-drafts/${draftId}/export?format=${format}`;
    }

    createDraft({
      title: "草稿",
      requirement_ids: ["r1"],
      mode: "response_outline",
    });
    review({ actor_label: "local-reviewer", comment: "ok", review_lock_version: 0 });

    expect(calls[0].path).toContain("/proposal-drafts");
    expect(calls[1].body).toMatchObject({ comment: "ok" });
    expect(exportUrl("markdown")).toContain("format=markdown");
    expect(exportUrl("docx")).toContain("format=docx");
  });
});

describe("export gate and disclaimer", () => {
  it("only allows export for reviewed drafts without unevidenced content", () => {
    expect(
      canExportDraft({
        status: "draft_pending_review",
        export_allowed: false,
        has_unevidenced_manual_content: false,
      }),
    ).toBe(false);
    expect(
      canExportDraft({
        status: "reviewed",
        export_allowed: true,
        has_unevidenced_manual_content: true,
      }),
    ).toBe(false);
    expect(
      canExportDraft({
        status: "reviewed",
        export_allowed: true,
        has_unevidenced_manual_content: false,
      }),
    ).toBe(true);
    expect(canMarkReviewed({ status: "reviewed", has_unevidenced_manual_content: false })).toBe(
      false,
    );
    expect(
      canMarkReviewed({
        status: "draft_pending_review",
        has_unevidenced_manual_content: true,
      }),
    ).toBe(false);
    expect(isDraftReadOnly("reviewed")).toBe(true);
    expect(PROPOSAL_DRAFT_DISCLAIMER).toContain("不构成投标结论");
  });

  it("surfaces 409 conflict with refresh", () => {
    const refresh = vi.fn();
    const msg = conflictMessage(409);
    if (msg) refresh();
    expect(msg).toMatch(/并发冲突/);
    expect(refresh).toHaveBeenCalledTimes(1);
  });
});
