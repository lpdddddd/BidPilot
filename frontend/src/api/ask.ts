import { API_BASE_URL, ApiError } from "./http";
import type {
  AskRequestPayload,
  AskResponse,
  AskStreamHandlers,
  LlmHealthResponse,
} from "../types/api";

export async function getLlmHealth(): Promise<LlmHealthResponse> {
  const res = await fetch(`${API_BASE_URL}/api/v1/health/llm`);
  if (!res.ok) {
    throw new ApiError("无法获取大模型状态", res.status);
  }
  return (await res.json()) as LlmHealthResponse;
}

export async function askProject(
  projectId: string,
  payload: AskRequestPayload,
  signal?: AbortSignal,
): Promise<AskResponse> {
  const res = await fetch(`${API_BASE_URL}/api/v1/projects/${projectId}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ ...payload, stream: false }),
    signal,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(
      typeof body.message === "string" ? body.message : "问答失败",
      res.status,
      body.detail,
    );
  }
  return body as AskResponse;
}

/**
 * SSE ask. Cancels via AbortSignal. Ignores events after abort.
 */
export async function askProjectStream(
  projectId: string,
  payload: AskRequestPayload,
  handlers: AskStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/v1/projects/${projectId}/ask`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({ ...payload, stream: true }),
    signal,
  });

  if (!res.ok) {
    let message = "问答失败";
    let detail: unknown;
    try {
      const body = await res.json();
      if (typeof body.message === "string") message = body.message;
      detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(message, res.status, detail);
  }

  if (!res.body) {
    throw new ApiError("浏览器不支持流式响应");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "message";

  const dispatch = (event: string, dataRaw: string) => {
    if (signal?.aborted) return;
    let data: unknown;
    try {
      data = JSON.parse(dataRaw);
    } catch {
      return;
    }
    if (event === "retrieval") {
      handlers.onRetrieval?.(data as Parameters<NonNullable<AskStreamHandlers["onRetrieval"]>>[0]);
    } else if (event === "delta") {
      const text = (data as { text?: string }).text ?? "";
      if (text) handlers.onDelta?.(text);
    } else if (event === "final") {
      const result = (data as { result: AskResponse }).result;
      handlers.onFinal?.(result);
    } else if (event === "error") {
      const err = data as { message?: string; detail?: unknown };
      handlers.onError?.({
        message: err.message || "问答失败",
        detail: err.detail,
      });
    }
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (signal?.aborted) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n");
      buffer = parts.pop() ?? "";
      let dataLines: string[] = [];
      for (const line of parts) {
        if (line.startsWith("event:")) {
          currentEvent = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).trim());
        } else if (line === "") {
          if (dataLines.length) {
            dispatch(currentEvent, dataLines.join("\n"));
            dataLines = [];
            currentEvent = "message";
          }
        }
      }
    }
    if (buffer.trim() && !signal?.aborted) {
      // flush trailing event without blank line
      const trailing = buffer.split("\n");
      let dataLines: string[] = [];
      for (const line of trailing) {
        if (line.startsWith("event:")) currentEvent = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length) dispatch(currentEvent, dataLines.join("\n"));
    }
  } finally {
    reader.releaseLock();
  }
}
