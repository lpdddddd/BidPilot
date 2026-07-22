import type {
  EvaluationCapabilitiesResponse,
  EvaluationCaseResult,
  EvaluationCaseResultListParams,
  EvaluationCaseResultListResponse,
  EvaluationCompareResponse,
  EvaluationExportFormat,
  EvaluationRun,
  EvaluationRunCreatePayload,
  EvaluationRunListParams,
  EvaluationRunListResponse,
  EvaluationSuite,
  EvaluationSuiteListResponse,
} from "../types/api";
import { API_BASE_URL, http } from "./http";

const START_TIMEOUT_MS = 30_000;

export async function getEvaluationCapabilities(
  projectId: string,
): Promise<EvaluationCapabilitiesResponse> {
  const { data } = await http.get<EvaluationCapabilitiesResponse>(
    `/api/v1/projects/${projectId}/evaluation-capabilities`,
  );
  return data;
}

export async function listEvaluationSuites(
  projectId: string,
): Promise<EvaluationSuiteListResponse> {
  const { data } = await http.get<EvaluationSuiteListResponse>(
    `/api/v1/projects/${projectId}/evaluation-suites`,
  );
  return data;
}

export async function getEvaluationSuite(
  projectId: string,
  suiteId: string,
): Promise<EvaluationSuite> {
  const { data } = await http.get<EvaluationSuite>(
    `/api/v1/projects/${projectId}/evaluation-suites/${suiteId}`,
  );
  return data;
}

export async function createEvaluationRun(
  projectId: string,
  payload: EvaluationRunCreatePayload,
  idempotencyKey?: string,
): Promise<EvaluationRun> {
  const { data } = await http.post<EvaluationRun>(
    `/api/v1/projects/${projectId}/evaluation-runs`,
    payload,
    {
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
      timeout: START_TIMEOUT_MS,
    },
  );
  return data;
}

export async function listEvaluationRuns(
  projectId: string,
  params: EvaluationRunListParams = {},
): Promise<EvaluationRunListResponse> {
  const { data } = await http.get<EvaluationRunListResponse>(
    `/api/v1/projects/${projectId}/evaluation-runs`,
    { params },
  );
  return data;
}

export async function getEvaluationRun(
  projectId: string,
  runId: string,
): Promise<EvaluationRun> {
  const { data } = await http.get<EvaluationRun>(
    `/api/v1/projects/${projectId}/evaluation-runs/${runId}`,
  );
  return data;
}

export async function listEvaluationResults(
  projectId: string,
  runId: string,
  params: EvaluationCaseResultListParams = {},
): Promise<EvaluationCaseResultListResponse> {
  const { data } = await http.get<EvaluationCaseResultListResponse>(
    `/api/v1/projects/${projectId}/evaluation-runs/${runId}/results`,
    { params },
  );
  return data;
}

export async function getEvaluationResult(
  projectId: string,
  runId: string,
  resultId: string,
): Promise<EvaluationCaseResult> {
  const { data } = await http.get<EvaluationCaseResult>(
    `/api/v1/projects/${projectId}/evaluation-runs/${runId}/results/${resultId}`,
  );
  return data;
}

export async function cancelEvaluationRun(
  projectId: string,
  runId: string,
): Promise<EvaluationRun> {
  const { data } = await http.post<EvaluationRun>(
    `/api/v1/projects/${projectId}/evaluation-runs/${runId}/cancel`,
    {},
    { timeout: START_TIMEOUT_MS },
  );
  return data;
}

export async function resumeEvaluationRun(
  projectId: string,
  runId: string,
): Promise<EvaluationRun> {
  const { data } = await http.post<EvaluationRun>(
    `/api/v1/projects/${projectId}/evaluation-runs/${runId}/resume`,
    {},
    { timeout: START_TIMEOUT_MS },
  );
  return data;
}

export async function compareEvaluationRuns(
  projectId: string,
  left: string,
  right: string,
): Promise<EvaluationCompareResponse> {
  const { data } = await http.get<EvaluationCompareResponse>(
    `/api/v1/projects/${projectId}/evaluation-runs/compare`,
    { params: { left, right } },
  );
  return data;
}

/** Relative export path (Vite proxy / same-origin). */
export function evaluationExportUrl(
  projectId: string,
  runId: string,
  format: EvaluationExportFormat,
): string {
  return `/api/v1/projects/${projectId}/evaluation-runs/${runId}/export?format=${format}`;
}

/** Absolute/export URL when VITE_API_BASE_URL is set. */
export function buildEvaluationExportUrl(
  projectId: string,
  runId: string,
  format: EvaluationExportFormat,
): string {
  const path = evaluationExportUrl(projectId, runId, format);
  const base = (API_BASE_URL || "").replace(/\/$/, "");
  if (!base) return path;
  return `${base}${path}`;
}

export async function exportEvaluationRun(
  projectId: string,
  runId: string,
  format: EvaluationExportFormat,
): Promise<{ blob: Blob; filename: string }> {
  const { data, headers } = await http.get<Blob>(
    `/api/v1/projects/${projectId}/evaluation-runs/${runId}/export`,
    {
      params: { format },
      responseType: "blob",
      timeout: 60_000,
    },
  );
  const disposition = String(headers["content-disposition"] || "");
  const match = /filename\*?=(?:UTF-8'')?["']?([^"';]+)/i.exec(disposition);
  const filename =
    (match?.[1] && decodeURIComponent(match[1])) ||
    `evaluation-run-${runId}.${format === "markdown" ? "md" : format}`;
  return { blob: data, filename };
}
