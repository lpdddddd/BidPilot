import type {
  AgentEventsResponse,
  AgentResultResponse,
  AgentRun,
  AgentRunListResponse,
  AgentRunStartPayload,
} from "../types/api";
import { http } from "./http";

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
      timeout: 180000,
    },
  );
  return data;
}

export async function getAgentRun(runId: string): Promise<AgentRun> {
  const { data } = await http.get<AgentRun>(`/api/v1/agent-runs/${runId}`);
  return data;
}

export async function getProjectAgentRun(
  projectId: string,
  runId: string,
): Promise<AgentRun> {
  const { data } = await http.get<AgentRun>(
    `/api/v1/projects/${projectId}/agent-runs/${runId}`,
  );
  return data;
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
): Promise<AgentEventsResponse> {
  const { data } = await http.get<AgentEventsResponse>(
    `/api/v1/projects/${projectId}/agent-runs/${runId}/events`,
  );
  return data;
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
  );
  return data;
}

export async function retryAgentRun(
  projectId: string,
  runId: string,
): Promise<AgentRun> {
  const { data } = await http.post<AgentRun>(
    `/api/v1/projects/${projectId}/agent-runs/${runId}/retry`,
  );
  return data;
}
