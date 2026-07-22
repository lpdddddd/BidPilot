import type {
  AgentEventsResponse,
  AgentResultResponse,
  AgentRun,
  AgentRunListResponse,
  AgentRunStartPayload,
} from "../types/api";
import { API_BASE_URL, http } from "./http";

/** Start/resume/retry return quickly (async). Keep timeout short — do not block for full graph. */
const START_TIMEOUT_MS = 30_000;

export async function startAgentRun(
  projectId: string,
  payload: AgentRunStartPayload = {},
  idempotencyKey?: string,
): Promise<AgentRun> {
  const { data } = await http.post<AgentRun>(
    `/api/v1/projects/${projectId}/agent-runs`,
    payload,
    {
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
      timeout: START_TIMEOUT_MS,
      // async by default — do not pass sync=true
    },
  );
  return data;
}

/** Project-scoped get; prefer over bare `/api/v1/agent-runs/{id}`. */
export async function getAgentRun(
  projectId: string,
  runId: string,
): Promise<AgentRun> {
  const { data } = await http.get<AgentRun>(
    `/api/v1/projects/${projectId}/agent-runs/${runId}`,
  );
  return data;
}

/** @deprecated Prefer getAgentRun(projectId, runId). */
export async function getProjectAgentRun(
  projectId: string,
  runId: string,
): Promise<AgentRun> {
  return getAgentRun(projectId, runId);
}

export async function getLatestAgentRun(
  projectId: string,
): Promise<AgentRun | null> {
  const { data } = await http.get<AgentRun | null>(
    `/api/v1/projects/${projectId}/agent-runs/latest`,
  );
  return data;
}

export async function listAgentRuns(
  projectId: string,
  limit = 20,
): Promise<AgentRunListResponse> {
  const { data } = await http.get<AgentRunListResponse>(
    `/api/v1/projects/${projectId}/agent-runs`,
    { params: { limit } },
  );
  return data;
}

export async function getAgentEvents(
  projectId: string,
  runId: string,
  afterSequence?: number,
): Promise<AgentEventsResponse> {
  const { data } = await http.get<AgentEventsResponse>(
    `/api/v1/projects/${projectId}/agent-runs/${runId}/events`,
    {
      params:
        afterSequence != null ? { after_sequence: afterSequence } : undefined,
    },
  );
  return data;
}

/** Build EventSource URL (API base + relative stream path). */
export function buildAgentEventsStreamUrl(
  projectId: string,
  runId: string,
  options?: {
    afterSequence?: number;
    streamPath?: string | null;
  },
): string {
  const path =
    options?.streamPath ||
    `/api/v1/projects/${projectId}/agent-runs/${runId}/events/stream`;
  const base = (API_BASE_URL || "").replace(/\/$/, "");
  const url = new URL(path.startsWith("http") ? path : `${base}${path}`, window.location.origin);
  if (options?.afterSequence != null) {
    url.searchParams.set("after_sequence", String(options.afterSequence));
  }
  // Prefer same-origin relative when no API base (Vite proxy).
  if (!API_BASE_URL) {
    return `${url.pathname}${url.search}`;
  }
  return url.toString();
}

export async function getAgentResult(
  projectId: string,
  runId: string,
): Promise<AgentResultResponse> {
  const { data } = await http.get<AgentResultResponse>(
    `/api/v1/projects/${projectId}/agent-runs/${runId}/result`,
  );
  return data;
}

export async function resumeAgentRun(
  projectId: string,
  runId: string,
): Promise<AgentRun> {
  const { data } = await http.post<AgentRun>(
    `/api/v1/projects/${projectId}/agent-runs/${runId}/resume`,
    undefined,
    {
      timeout: START_TIMEOUT_MS,
      // async by default — do not pass sync=true
    },
  );
  return data;
}

export async function retryAgentRun(
  projectId: string,
  runId: string,
): Promise<AgentRun> {
  const { data } = await http.post<AgentRun>(
    `/api/v1/projects/${projectId}/agent-runs/${runId}/retry`,
    undefined,
    {
      timeout: START_TIMEOUT_MS,
      // async by default — do not pass sync=true
    },
  );
  return data;
}
