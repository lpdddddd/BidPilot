import type {
  ComplianceFinding,
  ComplianceFindingListParams,
  ComplianceRuleCategory,
  ComplianceSeverity,
} from "../../types/api";

export type ComplianceFilters = {
  severity?: ComplianceSeverity | "all";
  category?: ComplianceRuleCategory | "all";
};

export function createDefaultComplianceFilters(): ComplianceFilters {
  return { severity: "all", category: "all" };
}

export function buildComplianceFindingParams(
  filters: ComplianceFilters,
): ComplianceFindingListParams {
  const params: ComplianceFindingListParams = {};
  if (filters.severity && filters.severity !== "all") {
    params.severity = filters.severity;
  }
  if (filters.category && filters.category !== "all") {
    params.category = filters.category;
  }
  return params;
}

export function filterFindingsClientSide(
  findings: ComplianceFinding[],
  filters: ComplianceFilters,
): ComplianceFinding[] {
  return findings.filter((f) => {
    if (filters.severity && filters.severity !== "all" && f.severity !== filters.severity) {
      return false;
    }
    if (filters.category && filters.category !== "all" && f.category !== filters.category) {
      return false;
    }
    return true;
  });
}

export function documentCenterHref(
  projectId: string,
  location?: ComplianceFinding["source_location_json"] | null,
): string | null {
  const docId =
    location?.document_id ||
    (typeof location?.source_document_id === "string" ? location.source_document_id : null);
  if (!docId) return null;
  const params = new URLSearchParams({ tab: "documents", documentId: String(docId) });
  return `/projects/${projectId}?${params.toString()}`;
}

export function evidenceSnippet(finding: ComplianceFinding): string {
  const evidence = finding.evidence_json;
  if (!evidence) return "—";
  if (Array.isArray(evidence)) return "—";
  const quote = evidence.quote;
  if (typeof quote === "string" && quote.trim()) {
    return quote.length > 120 ? `${quote.slice(0, 120)}…` : quote;
  }
  return "—";
}

export function locationLabel(finding: ComplianceFinding): string {
  const loc = finding.source_location_json;
  if (!loc) return "—";
  const parts: string[] = [];
  if (loc.file_name) parts.push(String(loc.file_name));
  const page = loc.page_start ?? loc.source_page;
  if (page != null) parts.push(`p.${page}`);
  const section = loc.section ?? loc.source_section;
  if (section) parts.push(String(section));
  return parts.join(" · ") || "—";
}
