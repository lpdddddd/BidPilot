import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { useEvaluationRunPoll, EVAL_POLL_INTERVAL_MS } from "./useEvaluationRunPoll";

const getRun = vi.fn();

vi.mock("../../api/evaluation", () => ({
  getEvaluationRun: (...args: unknown[]) => getRun(...args),
}));

function createWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return function wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

async function settleQueries() {
  await act(async () => {
    // React Query schedules updates via timers; advance + flush microtasks.
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("useEvaluationRunPoll", () => {
  beforeEach(() => {
    getRun.mockReset();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("polls while running and stops on terminal status", async () => {
    getRun
      .mockResolvedValueOnce({
        id: "run-1",
        project_id: "proj-1",
        status: "running",
        completed_cases: 1,
        total_cases: 3,
      })
      .mockResolvedValueOnce({
        id: "run-1",
        project_id: "proj-1",
        status: "running",
        completed_cases: 2,
        total_cases: 3,
      })
      .mockResolvedValue({
        id: "run-1",
        project_id: "proj-1",
        status: "completed",
        completed_cases: 3,
        total_cases: 3,
      });

    const { result } = renderHook(
      () => useEvaluationRunPoll({ projectId: "proj-1", runId: "run-1" }),
      { wrapper: createWrapper() },
    );

    await settleQueries();
    expect(getRun).toHaveBeenCalledTimes(1);
    expect(result.current.data?.status).toBe("running");
    expect(result.current.isPolling).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(EVAL_POLL_INTERVAL_MS);
    });
    await settleQueries();
    expect(getRun.mock.calls.length).toBeGreaterThanOrEqual(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(EVAL_POLL_INTERVAL_MS);
    });
    await settleQueries();
    expect(result.current.data?.status).toBe("completed");
    expect(result.current.isPolling).toBe(false);

    const callsAfterTerminal = getRun.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(EVAL_POLL_INTERVAL_MS * 3);
    });
    await settleQueries();
    expect(getRun.mock.calls.length).toBe(callsAfterTerminal);
  });

  it("cleans up polling on unmount", async () => {
    getRun.mockResolvedValue({
      id: "run-1",
      project_id: "proj-1",
      status: "running",
      completed_cases: 0,
      total_cases: 5,
    });

    const { unmount } = renderHook(
      () => useEvaluationRunPoll({ projectId: "proj-1", runId: "run-1" }),
      { wrapper: createWrapper() },
    );

    await settleQueries();
    expect(getRun).toHaveBeenCalled();
    const before = getRun.mock.calls.length;
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(EVAL_POLL_INTERVAL_MS * 4);
    });
    expect(getRun.mock.calls.length).toBe(before);
  });
});
