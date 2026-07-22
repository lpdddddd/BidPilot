import { cleanup, render, screen, waitFor, act, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AgentLoopPanel from "./AgentLoopPanel";

const getLatest = vi.fn();
const getResult = vi.fn();
const startRun = vi.fn();
const resumeRun = vi.fn();
const retryRun = vi.fn();
const getDraft = vi.fn();
const getEvents = vi.fn();

vi.mock("../../api/agentRuns", () => ({
  getLatestAgentRun: (...args: unknown[]) => getLatest(...args),
  getAgentResult: (...args: unknown[]) => getResult(...args),
  startAgentRun: (...args: unknown[]) => startRun(...args),
  resumeAgentRun: (...args: unknown[]) => resumeRun(...args),
  retryAgentRun: (...args: unknown[]) => retryRun(...args),
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

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return {
    ...actual,
    getProposalDraft: (...args: unknown[]) => getDraft(...args),
  };
});

type Listener = (ev: MessageEvent) => void;

class MockEventSource {
  static instances: MockEventSource[] = [];
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
    queueMicrotask(() => {
      if (this.closed) return;
      this.readyState = 1;
      this.onopen?.(new Event("open"));
    });
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
  }
}

const clients: QueryClient[] = [];

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  const view = render(
    <QueryClientProvider client={client}>
      <AgentLoopPanel projectId="proj-1" />
    </QueryClientProvider>,
  );
  return { ...view, client };
}

const baseRun = {
  id: "run-1",
  organization_id: "org-1",
  project_id: "proj-1",
  status: "completed" as const,
  current_node: "finalize",
  graph_version: "bidpilot-agent-1.0.0",
  started_at: new Date().toISOString(),
  finished_at: new Date().toISOString(),
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  thread_id: "run-1",
  events_stream_path: "/api/v1/projects/proj-1/agent-runs/run-1/events/stream",
  state: {
    draft_ids: ["draft-1"],
    draft_findings: [
      {
        rule_id: "D002_forbidden_claims",
        severity: "error",
        status: "fail",
        message: "禁止承诺表述",
        remediation: "改为风险表述",
      },
      {
        rule_id: "D004_placeholders",
        severity: "warning",
        status: "fail",
        message: "占位符",
      },
      {
        rule_id: "D005_empty_or_short",
        severity: "info",
        status: "pass",
        message: "ok",
      },
    ],
    citations: [
      {
        document_id: "doc-1",
        document_title: "招标文件.pdf",
        page: 3,
        section: "3.1",
        chunk_id: "chunk-abc",
        conclusion_summary: "需具备一级资质",
      },
    ],
    warnings: ["draft finding D002"],
    errors: [],
    compliance_summary: { finding_count: 2, critical_count: 0 },
  },
};

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

describe("AgentLoopPanel", () => {
  beforeEach(() => {
    vi.stubGlobal("EventSource", MockEventSource);
    MockEventSource.reset();
    getLatest.mockReset();
    getResult.mockReset();
    startRun.mockReset();
    resumeRun.mockReset();
    retryRun.mockReset();
    getDraft.mockReset();
    getEvents.mockReset();
    getLatest.mockResolvedValue(null);
    getEvents.mockResolvedValue({ run_id: "run-1", items: [], total: 0 });
    getResult.mockResolvedValue({
      run: baseRun,
      citations: baseRun.state.citations,
      warnings: baseRun.state.warnings,
      errors: [],
      draft_ids: ["draft-1"],
      summary: {},
    });
    getDraft.mockResolvedValue({
      id: "draft-1",
      status: "draft_pending_review",
      current_version: {
        version_number: 2,
        content_markdown: "本单位具备相关资质，详见证明材料。",
        content_json: {},
      },
    });
  });

  afterEach(() => {
    MockEventSource.reset();
    cleanup();
    for (const c of clients) {
      c.clear();
      c.cancelQueries();
    }
    clients.length = 0;
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("shows empty state when no runs", async () => {
    getLatest.mockResolvedValue(null);
    renderPanel();
    expect(await screen.findByTestId("agent-empty")).toBeTruthy();
  });

  it("starts a run when clicking 开始闭环", async () => {
    const user = userEvent.setup();
    getLatest.mockResolvedValue(null);
    startRun.mockResolvedValue({ ...baseRun, status: "running", id: "run-new" });
    getEvents.mockResolvedValue({ run_id: "run-new", items: [], total: 0 });
    renderPanel();
    await user.click(await screen.findByTestId("agent-start"));
    await waitFor(() => expect(startRun).toHaveBeenCalled());
  });

  it("loads latest run and shows draft body + severity stats", async () => {
    getLatest.mockResolvedValue(baseRun);
    renderPanel();
    expect(await screen.findByTestId("agent-loop-panel")).toBeTruthy();
    expect((await screen.findByTestId("agent-status")).textContent).toContain("已完成");
    expect((await screen.findByTestId("agent-severity-counts")).textContent).toContain(
      "critical 0 · error 1 · warning 1 · info 1",
    );
    expect((await screen.findByTestId("agent-draft-body")).textContent).toContain(
      "本单位具备相关资质",
    );
    expect(screen.getByText("draft_pending_review")).toBeTruthy();
    expect(screen.getByText("v2")).toBeTruthy();
    expect(screen.queryByText(/#draft-/)).toBeNull();
  });

  it("renders clickable citations with document jump params", async () => {
    getLatest.mockResolvedValue(baseRun);
    renderPanel();
    const link = await screen.findByTestId("agent-citation-0");
    expect(link.getAttribute("href")).toContain("document_id=doc-1");
    expect(link.getAttribute("href")).toContain("page=3");
    expect(link.getAttribute("href")).toContain("chunk_id=chunk-abc");
    expect(link.textContent).toContain("招标文件.pdf");
    expect(screen.getByText("需具备一级资质")).toBeTruthy();
  });

  it("shows failed / blocked status labels and error summary", async () => {
    getLatest.mockResolvedValue({
      ...baseRun,
      status: "failed",
      error_summary: "match service exploded",
      state: { ...baseRun.state, errors: ["boom"] },
    });
    getResult.mockResolvedValue({
      run: {
        ...baseRun,
        status: "failed",
        error_summary: "match service exploded",
        state: { ...baseRun.state, errors: ["boom"] },
      },
      citations: [],
      warnings: [],
      errors: ["boom"],
      draft_ids: [],
      summary: {},
    });
    renderPanel();
    expect((await screen.findByTestId("agent-status")).textContent).toContain("失败");
    expect((await screen.findByTestId("agent-error-summary")).textContent).toContain(
      "match service exploded",
    );
    expect((await screen.findByTestId("agent-errors")).textContent).toContain("boom");
  });

  it("shows blocked status clearly", async () => {
    getLatest.mockResolvedValue({ ...baseRun, status: "blocked" });
    getResult.mockResolvedValue({
      run: { ...baseRun, status: "blocked" },
      citations: [],
      warnings: [],
      errors: [],
      draft_ids: ["draft-1"],
      summary: {},
    });
    renderPanel();
    expect((await screen.findByTestId("agent-status")).textContent).toContain("已阻断");
  });

  it("refresh reloads latest run", async () => {
    const user = userEvent.setup();
    getLatest.mockResolvedValue(baseRun);
    renderPanel();
    await screen.findByTestId("agent-run-summary");
    await user.click(await screen.findByTestId("agent-refresh"));
    await waitFor(() => expect(getLatest.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it("shows API error when latest load fails", async () => {
    getLatest.mockRejectedValue(new Error("network down"));
    renderPanel();
    expect((await screen.findByTestId("agent-latest-error")).textContent).toContain(
      "network down",
    );
  });

  it("shows start API error", async () => {
    const user = userEvent.setup();
    getLatest.mockResolvedValue(null);
    startRun.mockRejectedValue(new Error("start failed"));
    renderPanel();
    await user.click(await screen.findByTestId("agent-start"));
    expect((await screen.findByTestId("agent-start-error")).textContent).toContain(
      "start failed",
    );
  });

  it("shows status bar and history timeline for completed run", async () => {
    getLatest.mockResolvedValue(baseRun);
    getEvents.mockResolvedValue({
      run_id: "run-1",
      total: 2,
      items: [
        historyEvent({
          sequence: 1,
          event_type: "node_started",
          node_name: "initialize_run",
          agent_step_id: "step-1",
        }),
        historyEvent({
          sequence: 2,
          event_type: "node_completed",
          node_name: "initialize_run",
          agent_step_id: "step-1",
          duration_ms: 120,
          safe_summary: "node initialize_run completed",
        }),
      ],
    });
    renderPanel();
    expect(await screen.findByTestId("agent-run-status-bar")).toBeTruthy();
    expect((await screen.findByTestId("agent-connection")).textContent).toContain("已结束");
    expect(await screen.findByTestId("agent-event-1")).toBeTruthy();
    expect(await screen.findByTestId("agent-event-2")).toBeTruthy();
    expect((await screen.findByTestId("agent-event-duration-2")).textContent).toMatch(/120/);
    expect((await screen.findByTestId("agent-completed-steps")).textContent).toContain("1");
  });

  it("updates timeline from SSE and deduplicates by sequence", async () => {
    const running = { ...baseRun, status: "running" as const, finished_at: null };
    getLatest.mockResolvedValue(running);
    getResult.mockResolvedValue({
      run: running,
      citations: [],
      warnings: [],
      errors: [],
      draft_ids: [],
      summary: {},
    });
    getEvents.mockResolvedValue({
      run_id: "run-1",
      total: 1,
      items: [
        historyEvent({
          sequence: 1,
          event_type: "node_started",
          node_name: "retrieve_evidence",
          agent_step_id: "step-a",
        }),
      ],
    });
    renderPanel();
    await screen.findByTestId("agent-event-1");
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0));
    const es = MockEventSource.instances[MockEventSource.instances.length - 1]!;

    await act(async () => {
      es.emit("agent_event", {
        run_id: "run-1",
        sequence: 2,
        event_type: "tool_started",
        node_name: "retrieve_evidence",
        tool_name: "vector_search",
        status: "ok",
        safe_summary: "tool vector_search started",
        duration_ms: null,
        agent_step_id: "step-a",
        tool_call_id: "tc-1",
        attempt: 1,
      });
      es.emit("agent_event", {
        run_id: "run-1",
        sequence: 2,
        event_type: "tool_started",
        node_name: "retrieve_evidence",
        tool_name: "vector_search",
        status: "ok",
        safe_summary: "tool vector_search started (dup)",
        agent_step_id: "step-a",
        tool_call_id: "tc-1",
        attempt: 1,
      });
    });

    expect(await screen.findByTestId("agent-event-2")).toBeTruthy();
    expect(screen.getAllByTestId("agent-event-2").length).toBe(1);
    expect((await screen.findByTestId("agent-current-tool")).textContent).toContain(
      "vector_search",
    );
    expect(screen.getByTestId("agent-event-2").getAttribute("data-nested")).toBe("true");
  });

  it("falls back to polling when SSE errors", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const running = { ...baseRun, status: "running" as const, finished_at: null };
    getLatest.mockResolvedValue(running);
    getResult.mockResolvedValue({
      run: running,
      citations: [],
      warnings: [],
      errors: [],
      draft_ids: [],
      summary: {},
    });
    getEvents.mockResolvedValue({ run_id: "run-1", items: [], total: 0 });
    renderPanel();
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0));
    const es = MockEventSource.instances[0]!;

    getEvents.mockResolvedValue({
      run_id: "run-1",
      total: 1,
      items: [
        historyEvent({
          sequence: 5,
          event_type: "node_started",
          node_name: "extract_requirements",
          agent_step_id: "step-e",
        }),
      ],
    });

    await act(async () => {
      es.onerror?.(new Event("error"));
    });

    await waitFor(() => {
      expect(screen.getByTestId("agent-connection").textContent).toContain("轮询");
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });

    expect(await screen.findByTestId("agent-event-5")).toBeTruthy();
  });

  it("keeps timeline events after resume", async () => {
    const user = userEvent.setup();
    const waiting = {
      ...baseRun,
      status: "waiting_for_user" as const,
      finished_at: null,
    };
    getLatest.mockResolvedValue(waiting);
    getResult.mockResolvedValue({
      run: waiting,
      citations: [],
      warnings: [],
      errors: [],
      draft_ids: [],
      summary: {},
    });
    getEvents.mockResolvedValue({
      run_id: "run-1",
      total: 1,
      items: [
        historyEvent({
          sequence: 3,
          event_type: "node_completed",
          node_name: "run_compliance_check",
          agent_step_id: "step-c",
          duration_ms: 50,
        }),
      ],
    });
    resumeRun.mockResolvedValue({ ...waiting, status: "running" });
    renderPanel();
    expect(await screen.findByTestId("agent-event-3")).toBeTruthy();
    await user.click(await screen.findByTestId("agent-resume"));
    await waitFor(() => expect(resumeRun).toHaveBeenCalled());
    expect(screen.getByTestId("agent-event-3")).toBeTruthy();
  });

  it("pauses auto-scroll when user scrolls up and shows 回到最新", async () => {
    const running = { ...baseRun, status: "running" as const, finished_at: null };
    getLatest.mockResolvedValue(running);
    getResult.mockResolvedValue({
      run: running,
      citations: [],
      warnings: [],
      errors: [],
      draft_ids: [],
      summary: {},
    });
    getEvents.mockResolvedValue({
      run_id: "run-1",
      total: 1,
      items: [
        historyEvent({ sequence: 1, agent_step_id: "s1" }),
      ],
    });
    renderPanel();
    const timeline = await screen.findByTestId("agent-timeline");
    Object.defineProperty(timeline, "scrollHeight", { configurable: true, value: 800 });
    Object.defineProperty(timeline, "clientHeight", { configurable: true, value: 200 });
    Object.defineProperty(timeline, "scrollTop", {
      configurable: true,
      writable: true,
      value: 0,
    });
    fireEvent.scroll(timeline);
    expect(await screen.findByTestId("agent-scroll-latest")).toBeTruthy();
    expect(screen.getByTestId("agent-scroll-latest").textContent).toContain("回到最新");
  });
});
