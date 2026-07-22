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
