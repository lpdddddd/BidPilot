import { cleanup, renderHook, waitFor, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  MAX_SSE_RECONNECT_ATTEMPTS,
  POLL_INTERVAL_MS,
  SSE_BACKOFF_MS,
  SSE_RECOVERY_EVERY_N_POLLS,
  useAgentEventStream,
} from "./useAgentEventStream";

const getEvents = vi.fn();

vi.mock("../../api/agentRuns", () => ({
  getAgentEvents: (...args: unknown[]) => getEvents(...args),
  buildAgentEventsStreamUrl: (
    projectId: string,
    runId: string,
    options?: { afterSequence?: number; streamPath?: string | null },
  ) => {
    const path =
      options?.streamPath ||
      `/api/v1/projects/${projectId}/agent-runs/${runId}/events/stream`;
    const q =
      options?.afterSequence != null ? `?after_sequence=${options.afterSequence}` : "";
    return `${path}${q}`;
  },
}));

type Listener = (ev: MessageEvent) => void;

class MockEventSource {
  static instances: MockEventSource[] = [];
  static autoOpen = true;
  url: string;
  readyState = 0;
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  closed = false;
  private listeners = new Map<string, Set<Listener>>();

  constructor(url: string | URL) {
    this.url = String(url);
    MockEventSource.instances.push(this);
    if (MockEventSource.autoOpen) {
      queueMicrotask(() => {
        if (this.closed) return;
        this.readyState = 1;
        this.onopen?.(new Event("open"));
      });
    }
  }

  addEventListener(type: string, fn: EventListener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(fn as Listener);
  }

  removeEventListener(type: string, fn: EventListener) {
    this.listeners.get(type)?.delete(fn as Listener);
  }

  close() {
    this.closed = true;
    this.readyState = 2;
  }

  emit(type: string, data: unknown) {
    const ev = {
      data: typeof data === "string" ? data : JSON.stringify(data),
    } as MessageEvent;
    this.listeners.get(type)?.forEach((fn) => fn(ev));
    if (type === "message") this.onmessage?.(ev);
  }

  static reset() {
    for (const i of MockEventSource.instances) i.close();
    MockEventSource.instances = [];
    MockEventSource.autoOpen = true;
  }

  static latest() {
    return MockEventSource.instances[MockEventSource.instances.length - 1];
  }
}

function historyEvent(partial: Record<string, unknown>) {
  return {
    event_type: "node_started",
    sequence: 1,
    name: "initialize_run",
    node_name: "initialize_run",
    tool_name: null,
    status: "ok",
    safe_summary: "node initialize_run started",
    timestamp: new Date().toISOString(),
    duration_ms: null,
    agent_step_id: "step-1",
    tool_call_id: null,
    attempt: 1,
    ...partial,
  };
}

function ssePayload(partial: Record<string, unknown>) {
  return {
    run_id: "run-1",
    sequence: 1,
    event_type: "node_started",
    node_name: "initialize_run",
    tool_name: null,
    status: "ok",
    safe_summary: "ok",
    duration_ms: null,
    agent_step_id: "step-1",
    tool_call_id: null,
    attempt: 1,
    ...partial,
  };
}

describe("useAgentEventStream", () => {
  beforeEach(() => {
    vi.stubGlobal("EventSource", MockEventSource);
    MockEventSource.reset();
    getEvents.mockReset();
    getEvents.mockResolvedValue({ run_id: "run-1", items: [], total: 0 });
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    MockEventSource.reset();
    cleanup();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("connects initially after loading history", async () => {
    getEvents.mockResolvedValue({
      run_id: "run-1",
      total: 1,
      items: [historyEvent({ sequence: 1 })],
    });

    const { result } = renderHook(() =>
      useAgentEventStream({
        projectId: "proj-1",
        runId: "run-1",
        runStatus: "running",
      }),
    );

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    await waitFor(() => expect(result.current.connection).toBe("live"));
    expect(result.current.events.map((e) => e.sequence)).toEqual([1]);
    expect(MockEventSource.instances.length).toBe(1);
    expect(MockEventSource.latest()!.url).toContain("after_sequence=1");
  });

  it("reconnects with catch-up and dedupes events", async () => {
    getEvents.mockResolvedValue({
      run_id: "run-1",
      total: 1,
      items: [historyEvent({ sequence: 1 })],
    });

    const { result } = renderHook(() =>
      useAgentEventStream({
        projectId: "proj-1",
        runId: "run-1",
        runStatus: "running",
      }),
    );

    await waitFor(() => expect(result.current.connection).toBe("live"));
    const firstEs = MockEventSource.latest()!;

    await act(async () => {
      firstEs.emit("agent_event", ssePayload({ sequence: 2, event_type: "node_completed" }));
    });
    expect(result.current.events.map((e) => e.sequence)).toEqual([1, 2]);

    getEvents.mockResolvedValue({
      run_id: "run-1",
      total: 1,
      items: [
        historyEvent({
          sequence: 2,
          event_type: "node_completed",
          safe_summary: "catch-up dup",
        }),
        historyEvent({ sequence: 3, event_type: "node_started", node_name: "retrieve" }),
      ],
    });

    await act(async () => {
      firstEs.onerror?.(new Event("error"));
    });

    await waitFor(() => expect(result.current.connection).toBe("reconnecting"));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SSE_BACKOFF_MS[0]);
    });

    await waitFor(() => expect(MockEventSource.instances.length).toBe(2));
    await waitFor(() => expect(result.current.connection).toBe("live"));

    expect(result.current.events.map((e) => e.sequence)).toEqual([1, 2, 3]);
    expect(result.current.events.filter((e) => e.sequence === 2)).toHaveLength(1);
    expect(MockEventSource.latest()!.url).toContain("after_sequence=3");
  });

  it("enters polling after max reconnect attempts", async () => {
    getEvents.mockResolvedValue({ run_id: "run-1", items: [], total: 0 });

    const { result } = renderHook(() =>
      useAgentEventStream({
        projectId: "proj-1",
        runId: "run-1",
        runStatus: "running",
      }),
    );

    await waitFor(() => expect(MockEventSource.instances.length).toBe(1));

    for (let i = 0; i < MAX_SSE_RECONNECT_ATTEMPTS; i++) {
      const es = MockEventSource.latest()!;
      await act(async () => {
        es.onerror?.(new Event("error"));
      });
      await waitFor(() => expect(result.current.connection).toBe("reconnecting"));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(SSE_BACKOFF_MS[i] ?? SSE_BACKOFF_MS[2]!);
      });
      await waitFor(() =>
        expect(MockEventSource.instances.length).toBe(i + 2),
      );
    }

    const lastEs = MockEventSource.latest()!;
    await act(async () => {
      lastEs.onerror?.(new Event("error"));
    });

    await waitFor(() => expect(result.current.connection).toBe("polling"));
  });

  it("recovers SSE from polling every N polls", async () => {
    getEvents.mockResolvedValue({ run_id: "run-1", items: [], total: 0 });

    const { result } = renderHook(() =>
      useAgentEventStream({
        projectId: "proj-1",
        runId: "run-1",
        runStatus: "running",
      }),
    );

    await waitFor(() => expect(MockEventSource.instances.length).toBe(1));

    // Exhaust reconnect budget → polling
    for (let i = 0; i < MAX_SSE_RECONNECT_ATTEMPTS; i++) {
      await act(async () => {
        MockEventSource.latest()!.onerror?.(new Event("error"));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(SSE_BACKOFF_MS[i] ?? SSE_BACKOFF_MS[2]!);
      });
      await waitFor(() =>
        expect(MockEventSource.instances.length).toBeGreaterThan(i + 1),
      );
    }
    await act(async () => {
      MockEventSource.latest()!.onerror?.(new Event("error"));
    });
    await waitFor(() => expect(result.current.connection).toBe("polling"));

    const esCountAtPoll = MockEventSource.instances.length;

    // Initial poll tick already ran (count=1). Advance until recovery tick (count=5).
    const remaining = SSE_RECOVERY_EVERY_N_POLLS - 1;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(remaining * POLL_INTERVAL_MS);
    });

    await waitFor(() =>
      expect(MockEventSource.instances.length).toBeGreaterThan(esCountAtPoll),
    );
    await waitFor(() => expect(result.current.connection).toBe("live"));
  });

  it("clears timers on terminal status and unmount", async () => {
    getEvents.mockResolvedValue({
      run_id: "run-1",
      total: 1,
      items: [historyEvent({ sequence: 1 })],
    });

    const { result, unmount, rerender } = renderHook(
      (props: { status: string }) =>
        useAgentEventStream({
          projectId: "proj-1",
          runId: "run-1",
          runStatus: props.status,
        }),
      { initialProps: { status: "running" } },
    );

    await waitFor(() => expect(result.current.connection).toBe("live"));
    const es = MockEventSource.latest()!;
    expect(es.closed).toBe(false);

    rerender({ status: "completed" });
    await waitFor(() => expect(result.current.connection).toBe("completed"));
    expect(es.closed).toBe(true);

    // Fresh running stream then unmount
    getEvents.mockResolvedValue({ run_id: "run-1", items: [], total: 0 });
    const second = renderHook(() =>
      useAgentEventStream({
        projectId: "proj-1",
        runId: "run-2",
        runStatus: "running",
      }),
    );
    await waitFor(() => expect(MockEventSource.latest()).toBeTruthy());
    const liveEs = MockEventSource.latest()!;
    second.unmount();
    expect(liveEs.closed).toBe(true);

    unmount();
  });

  it("marks completed on done event", async () => {
    getEvents.mockResolvedValue({ run_id: "run-1", items: [], total: 0 });

    const { result } = renderHook(() =>
      useAgentEventStream({
        projectId: "proj-1",
        runId: "run-1",
        runStatus: "running",
      }),
    );

    await waitFor(() => expect(result.current.connection).toBe("live"));
    await act(async () => {
      MockEventSource.latest()!.emit("done", {});
    });
    expect(result.current.connection).toBe("completed");
    expect(MockEventSource.latest()!.closed).toBe(true);
  });
});
