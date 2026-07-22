import { describe, expect, it } from "vitest";
import {
  agentStatusLabel,
  buildAgentStartPayload,
  citationLabel,
  countFindingSeverities,
  documentCitationHref,
  formatComplianceSummary,
  formatSeverityCounts,
  primaryDraftId,
} from "./agentParams";
import type { AgentRun } from "../../types/api";

describe("agentParams", () => {
  it("labels known statuses", () => {
    expect(agentStatusLabel("completed_with_warnings")).toContain("警告");
    expect(agentStatusLabel("blocked")).toBe("已阻断");
    expect(agentStatusLabel("running")).toBe("运行中");
    expect(agentStatusLabel("failed")).toBe("失败");
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

  it("counts severities and formats", () => {
    const counts = countFindingSeverities([
      { severity: "critical" },
      { severity: "error" },
      { severity: "warning" },
      { severity: "info" },
      { severity: "info" },
    ]);
    expect(counts).toEqual({ critical: 1, error: 1, warning: 1, info: 2 });
    expect(formatSeverityCounts(counts)).toContain("critical 1");
  });

  it("builds document citation href with query params", () => {
    const href = documentCitationHref("proj-1", {
      document_id: "doc-1",
      page: 2,
      chunk_id: "c-9",
    });
    expect(href).toContain("/projects/proj-1?");
    expect(href).toContain("document_id=doc-1");
    expect(href).toContain("page=2");
    expect(href).toContain("chunk_id=c-9");
    expect(documentCitationHref("proj-1", {})).toBeNull();
  });

  it("labels citations", () => {
    expect(
      citationLabel({
        document_title: "资质.pdf",
        page_start: 1,
        section: "A",
        chunk_id: "abcdef12xxxx",
      }),
    ).toContain("资质.pdf");
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
