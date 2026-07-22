/** Helpers for Agent 闭环 panel (params / display). */

import type { AgentRun, AgentRunStatus } from "../../types/api";

export const AGENT_STATUS_LABELS: Record<AgentRunStatus, string> = {
  pending: "待执行",
  running: "运行中",
  waiting_for_user: "已中断待恢复",
  blocked: "已阻断",
  completed: "已完成",
  completed_with_warnings: "完成（有警告）",
  failed: "失败",
  cancelled: "已取消",
};

export type SeverityCounts = {
  critical: number;
  error: number;
  warning: number;
  info: number;
};

export type AgentCitation = {
  document_id?: string | null;
  document_title?: string | null;
  file_name?: string | null;
  page?: number | null;
  page_start?: number | null;
  section?: string | null;
  chunk_id?: string | null;
  conclusion_summary?: string | null;
  summary?: string | null;
};

export function agentStatusLabel(status: AgentRunStatus | string): string {
  return AGENT_STATUS_LABELS[status as AgentRunStatus] ?? status;
}

export function formatComplianceSummary(
  summary: Record<string, unknown> | null | undefined,
): string {
  if (!summary || typeof summary !== "object") return "—";
  const findings = summary.finding_count ?? summary.critical_count;
  const critical = summary.critical_count;
  const parts: string[] = [];
  if (findings != null) parts.push(`发现 ${String(findings)}`);
  if (critical != null) parts.push(`严重 ${String(critical)}`);
  if (summary.critical_qualification) parts.push("资格风险");
  return parts.join(" · ") || "—";
}

export function countFindingSeverities(
  findings: Array<Record<string, unknown>> | null | undefined,
): SeverityCounts {
  const counts: SeverityCounts = { critical: 0, error: 0, warning: 0, info: 0 };
  for (const f of findings || []) {
    const sev = String(f.severity || "info").toLowerCase();
    if (sev === "critical") counts.critical += 1;
    else if (sev === "error") counts.error += 1;
    else if (sev === "warning") counts.warning += 1;
    else counts.info += 1;
  }
  return counts;
}

export function formatSeverityCounts(counts: SeverityCounts): string {
  return `critical ${counts.critical} · error ${counts.error} · warning ${counts.warning} · info ${counts.info}`;
}

export function primaryDraftId(run: AgentRun | null | undefined): string | null {
  const fromState = run?.state?.draft_ids;
  if (Array.isArray(fromState) && fromState.length > 0) return String(fromState[0]);
  const fromSummary = run?.output_summary_json?.draft_ids;
  if (Array.isArray(fromSummary) && fromSummary.length > 0) {
    return String(fromSummary[0]);
  }
  return null;
}

export function buildAgentStartPayload(userRequest: string): {
  user_request: string;
  intent: string;
} {
  return {
    user_request: userRequest.trim() || "执行招投标分析闭环",
    intent: "bid_analysis_loop",
  };
}

/** Document center jump — document_id / page / chunk_id query params. */
export function documentCitationHref(
  projectId: string,
  citation: AgentCitation,
): string | null {
  const docId = citation.document_id;
  if (!docId) return null;
  const params = new URLSearchParams({
    tab: "documents",
    document_id: String(docId),
    documentId: String(docId),
  });
  const page = citation.page ?? citation.page_start;
  if (page != null) params.set("page", String(page));
  if (citation.chunk_id) params.set("chunk_id", String(citation.chunk_id));
  return `/projects/${projectId}?${params.toString()}`;
}

export function citationLabel(citation: AgentCitation): string {
  const title =
    citation.document_title || citation.file_name || citation.document_id || "文档";
  const parts: string[] = [String(title)];
  const page = citation.page ?? citation.page_start;
  if (page != null) parts.push(`p.${page}`);
  if (citation.section) parts.push(String(citation.section));
  if (citation.chunk_id) parts.push(`chunk ${String(citation.chunk_id).slice(0, 8)}`);
  return parts.join(" · ");
}
