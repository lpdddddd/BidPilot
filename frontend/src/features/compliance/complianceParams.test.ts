import { describe, expect, it } from "vitest";
import {
  buildComplianceFindingParams,
  createDefaultComplianceFilters,
  documentCenterHref,
  evidenceSnippet,
  filterFindingsClientSide,
  locationLabel,
} from "./complianceParams";
import type { ComplianceFinding } from "../../types/api";

describe("complianceParams", () => {
  it("builds finding query params from filters", () => {
    expect(buildComplianceFindingParams(createDefaultComplianceFilters())).toEqual({});
    expect(
      buildComplianceFindingParams({ severity: "error", category: "coverage" }),
    ).toEqual({ severity: "error", category: "coverage" });
  });

  it("filters findings client-side", () => {
    const items: ComplianceFinding[] = [
      {
        finding_id: "1",
        rule_id: "A001",
        rule_name: "a",
        category: "coverage",
        severity: "error",
        status: "fail",
        message: "m1",
      },
      {
        finding_id: "2",
        rule_id: "B001",
        rule_name: "b",
        category: "evidence",
        severity: "info",
        status: "pass",
        message: "m2",
      },
    ];
    expect(filterFindingsClientSide(items, { severity: "error" })).toHaveLength(1);
    expect(filterFindingsClientSide(items, { category: "evidence" })).toHaveLength(1);
  });

  it("builds document center jump and labels", () => {
    const finding: ComplianceFinding = {
      finding_id: "x",
      rule_id: "B001",
      rule_name: "g",
      category: "evidence",
      severity: "error",
      status: "fail",
      message: "bad",
      evidence_json: { quote: "原文片段ABC" },
      source_location_json: {
        document_id: "doc-1",
        file_name: "q.pdf",
        page_start: 3,
        section: "§2",
      },
    };
    expect(documentCenterHref("proj-1", finding.source_location_json)).toBe(
      "/projects/proj-1?tab=documents&documentId=doc-1",
    );
    expect(evidenceSnippet(finding)).toContain("原文片段");
    expect(locationLabel(finding)).toContain("q.pdf");
    expect(locationLabel(finding)).toContain("p.3");
  });
});
