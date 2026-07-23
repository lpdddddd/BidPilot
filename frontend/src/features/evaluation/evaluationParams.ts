/** Pure helpers for Evaluation Center (labels, filters, safe display, citations). */

import type {
  EvaluationCapability,
  EvaluationCaseResult,
  EvaluationCitation,
  EvaluationRun,
  EvaluationRunStatus,
  EvaluationTargetType,
} from "../../types/api";

export const EVAL_POLL_INTERVAL_MS = 2000;

export const EVALUATION_TABS = [
  "overview",
  "new",
  "runs",
  "compare",
] as const;

export type EvaluationTab = (typeof EVALUATION_TABS)[number];

export const EVAL_RUN_STATUS_LABELS: Record<EvaluationRunStatus, string> = {
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  partial: "部分完成",
  failed: "失败",
  cancelled: "已取消",
};

export const EVAL_TARGET_LABELS: Record<EvaluationTargetType, string> = {
  deterministic_fake: "确定性假目标 (CI)",
  rag: "RAG 检索问答",
  extraction: "需求抽取",
  matching: "供应商匹配",
  compliance: "合规检查",
  drafting: "响应草稿",
  agent_pipeline: "Agent 全流程",
};

/** Keys that must never be rendered in the Evaluation Center UI. */
export const SENSITIVE_DISPLAY_KEYS = new Set([
  "prompt",
  "full_prompt",
  "system_prompt",
  "user_prompt",
  "cot",
  "chain_of_thought",
  "chain_of_thought_text",
  "reasoning",
  "api_key",
  "apikey",
  "authorization",
  "token",
  "secret",
  "password",
  "tool_params",
  "raw_tool_params",
  "tool_arguments",
  "reference_output",
  "expected_output",
  "gold_answer",
]);

const TERMINAL_STATUSES = new Set<string>([
  "completed",
  "partial",
  "failed",
  "cancelled",
]);

export function isTerminalEvaluationStatus(status: string | null | undefined): boolean {
  if (!status) return false;
  return TERMINAL_STATUSES.has(status);
}

export function isActiveEvaluationStatus(status: string | null | undefined): boolean {
  return status === "queued" || status === "running";
}

export function evaluationRunStatusLabel(status: string): string {
  return EVAL_RUN_STATUS_LABELS[status as EvaluationRunStatus] ?? status;
}

export function evaluationTargetLabel(target: string): string {
  return EVAL_TARGET_LABELS[target as EvaluationTargetType] ?? target;
}

export function shortHash(hash: string | null | undefined, len = 8): string {
  if (!hash) return "—";
  return hash.length <= len ? hash : `${hash.slice(0, len)}…`;
}

export function formatScore(value: number | null | undefined, digits = 3): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return "—";
  const pct = value <= 1 && value >= 0 ? value * 100 : value;
  return `${pct.toFixed(digits)}%`;
}

export function formatDurationMs(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms)) return "—";
  if (ms < 1000) return `${ms} ms`;
  const sec = ms / 1000;
  if (sec < 60) return `${sec.toFixed(1)} s`;
  const min = Math.floor(sec / 60);
  const rem = Math.round(sec % 60);
  return `${min}m ${rem}s`;
}

export function runProgress(run: Pick<EvaluationRun, "completed_cases" | "total_cases">): number {
  if (!run.total_cases) return 0;
  return Math.min(100, Math.round((run.completed_cases / run.total_cases) * 100));
}

export function passRate(run: EvaluationRun): number | null {
  if (run.summary_json?.pass_rate != null) return run.summary_json.pass_rate;
  if (!run.total_cases) return null;
  return run.passed_cases / run.total_cases;
}

export function errorRate(run: EvaluationRun): number | null {
  if (run.summary_json?.error_rate != null) return run.summary_json.error_rate;
  if (!run.total_cases) return null;
  return run.error_cases / run.total_cases;
}

export function referenceCoverage(run: EvaluationRun): number | null {
  return run.summary_json?.direct_reference_coverage ?? null;
}

export function taskFamilyScores(
  run: EvaluationRun,
): Record<string, number | null> {
  return run.summary_json?.task_family_scores ?? {};
}

export function hardGateFailureCount(run: EvaluationRun): number {
  if (run.summary_json?.hard_gate_failure_count != null) {
    return run.summary_json.hard_gate_failure_count;
  }
  const list = run.summary_json?.hard_gate_failures;
  return Array.isArray(list) ? list.length : 0;
}

export function parseEvaluationTab(raw: string | null): EvaluationTab {
  if (raw && (EVALUATION_TABS as readonly string[]).includes(raw)) {
    return raw as EvaluationTab;
  }
  return "overview";
}

export function capabilityOptionLabel(cap: EvaluationCapability): string {
  const base = cap.label || evaluationTargetLabel(String(cap.target_type));
  if (cap.available) return base;
  const reason = cap.reason?.trim() || "当前不可用";
  return `${base}（不可用：${reason}）`;
}

/** Strip sensitive keys from an object for safe JSON display. */
export function sanitizeForDisplay(
  value: unknown,
  depth = 0,
): unknown {
  if (depth > 6) return "[…]";
  if (value == null) return value;
  if (Array.isArray(value)) {
    return value.slice(0, 50).map((v) => sanitizeForDisplay(v, depth + 1));
  }
  if (typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      const lower = k.toLowerCase();
      if (
        SENSITIVE_DISPLAY_KEYS.has(lower) ||
        lower.includes("prompt") ||
        lower.includes("secret") ||
        lower.includes("chain_of_thought") ||
        lower === "cot"
      ) {
        // Omit entirely — never surface CoT / prompts / secrets in the UI.
        continue;
      }
      out[k] = sanitizeForDisplay(v, depth + 1);
    }
    return out;
  }
  if (typeof value === "string" && value.length > 2000) {
    return `${value.slice(0, 2000)}…`;
  }
  return value;
}

export function safeJsonPreview(value: unknown): string {
  try {
    return JSON.stringify(sanitizeForDisplay(value), null, 2);
  } catch {
    return "—";
  }
}

export function containsForbiddenLeak(text: string): boolean {
  const lower = text.toLowerCase();
  return (
    lower.includes("chain of thought") ||
    lower.includes("chain_of_thought") ||
    /\bapi[_-]?key\b/.test(lower) ||
    lower.includes("authorization:") ||
    lower.includes("\"prompt\"")
  );
}

export type CitationValidation = {
  valid: boolean;
  href: string | null;
  label: string;
  error: string | null;
};

/**
 * Validate citation deep-link against the current project.
 * Invalid when missing doc, cross-project, or server marked invalid.
 */
export function validateEvaluationCitation(
  projectId: string,
  citation: EvaluationCitation,
): CitationValidation {
  const labelParts: string[] = [];
  const title =
    citation.document_title || citation.file_name || citation.document_id || "文档";
  labelParts.push(String(title));
  const page = citation.page ?? citation.page_start;
  if (page != null) labelParts.push(`p.${page}`);
  if (citation.section) labelParts.push(String(citation.section));
  if (citation.chunk_id) {
    labelParts.push(`chunk ${String(citation.chunk_id).slice(0, 8)}`);
  }
  const label = labelParts.join(" · ");

  if (citation.valid === false) {
    const reason =
      citation.invalid_reason ||
      citation.validation_error ||
      "引用校验失败：文档、页码或 chunk 不匹配";
    return {
      valid: false,
      href: null,
      label,
      error: reason.includes("chunk") ? "chunk 不存在或无权访问" : reason,
    };
  }

  if (citation.project_id && citation.project_id !== projectId) {
    return {
      valid: false,
      href: null,
      label,
      error: "引用校验失败：跨项目引用",
    };
  }

  const docId = citation.document_id;
  if (!docId) {
    return {
      valid: false,
      href: null,
      label,
      error: "引用校验失败：缺少 document_id",
    };
  }

  // Prefer server-built deep link when present.
  if (citation.detail_url) {
    return { valid: true, href: citation.detail_url, label, error: null };
  }

  const params = new URLSearchParams({
    tab: "documents",
    document_id: String(docId),
    documentId: String(docId),
  });
  if (page != null) params.set("page", String(page));
  if (citation.chunk_id) params.set("chunk_id", String(citation.chunk_id));
  const href = `/projects/${projectId}?${params.toString()}`;

  return { valid: true, href, label, error: null };
}

export function agentRunHref(projectId: string, agentRunId: string): string {
  return `/projects/${projectId}?tab=agent-loop&agentRunId=${encodeURIComponent(agentRunId)}`;
}

export function hardGateLabels(
  failures: EvaluationCaseResult["hard_gate_failures"],
): string[] {
  if (!failures?.length) return [];
  return failures.map((f) => {
    if (typeof f === "string") return f;
    if (f && typeof f === "object") {
      const name = (f as Record<string, unknown>).name ?? (f as Record<string, unknown>).gate;
      return name != null ? String(name) : JSON.stringify(f);
    }
    return String(f);
  });
}

export function metricDisplayValue(metric: {
  applicable: boolean;
  value?: number | null;
  reference_kind?: string;
}): string {
  if (!metric.applicable || metric.reference_kind === "not_applicable") {
    return "N/A";
  }
  if (metric.reference_kind === "metric_error") {
    return "错误";
  }
  if (metric.value == null) return "—";
  return formatScore(metric.value);
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  a.style.display = "none";
  document.body.appendChild(a);
  // Prefer programmatic download attribute; avoid full navigation in jsdom.
  a.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
  a.remove();
  URL.revokeObjectURL(url);
}

export function compareMismatchWarnings(warnings: string[] | undefined): {
  datasetHashMismatch: boolean;
  evaluatorVersionMismatch: boolean;
  messages: string[];
} {
  const messages = warnings ?? [];
  const joined = messages.join(" ").toLowerCase();
  return {
    datasetHashMismatch:
      joined.includes("dataset") && joined.includes("hash"),
    evaluatorVersionMismatch:
      joined.includes("evaluator") && joined.includes("version"),
    messages,
  };
}
