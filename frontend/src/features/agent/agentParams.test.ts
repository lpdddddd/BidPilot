import { describe, expect, it } from "vitest";
import {
  agentStatusLabel,
  buildAgentStartPayload,
  formatComplianceSummary,
  primaryDraftId,
} from "./agentParams";
import type { AgentRun } from "../../types/api";

describe("agentParams", () => {
  it("labels known statuses", () => {
    expect(agentStatusLabel("completed_with_warnings")).toContain("警告");
    expect(agentStatusLabel("blocked")).toBe("已阻断");
  });

  it("formats compliance summary", () => {
    expect(
      formatComplianceSummary({
        finding_count: 3,
        critical_count: 1,
        critical_qualification: true,
      }),
    ).toContain("严重");
    expect(formatComplianceSummary(null)).toBe("—");
  });

  it("picks primary draft id", () => {
    const run = {
      state: { draft_ids: ["a", "b"] },
      output_summary_json: { draft_ids: ["c"] },
    } as AgentRun;
    expect(primaryDraftId(run)).toBe("a");
    expect(primaryDraftId(null)).toBeNull();
  });

  it("builds start payload", () => {
    expect(buildAgentStartPayload("  hello  ").user_request).toBe("hello");
    expect(buildAgentStartPayload("").intent).toBe("bid_analysis_loop");
  });
});
