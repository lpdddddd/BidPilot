import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgentLoopPanel from "./AgentLoopPanel";

const getLatest = vi.fn();
const getResult = vi.fn();
const startRun = vi.fn();
const resumeRun = vi.fn();
const getDraft = vi.fn();

vi.mock("../../api/agentRuns", () => ({
  getLatestAgentRun: (...args: unknown[]) => getLatest(...args),
  getAgentResult: (...args: unknown[]) => getResult(...args),
  startAgentRun: (...args: unknown[]) => startRun(...args),
  resumeAgentRun: (...args: unknown[]) => resumeRun(...args),
}));

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return {
    ...actual,
    getProposalDraft: (...args: unknown[]) => getDraft(...args),
  };
});

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <AgentLoopPanel projectId="proj-1" />
    </QueryClientProvider>,
  );
}

const baseRun = {
  id: "run-1",
  organization_id: "org-1",
  project_id: "proj-1",
  status: "completed" as const,
  current_node: "finalize",
  graph_version: "bidpilot-agent-1.0.0",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
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

describe("AgentLoopPanel", () => {
  beforeEach(() => {
    getLatest.mockReset();
    getResult.mockReset();
    startRun.mockReset();
    resumeRun.mockReset();
    getDraft.mockReset();
    getLatest.mockResolvedValue(null);
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

  it("shows empty state when no runs", async () => {
    getLatest.mockResolvedValue(null);
    renderPanel();
    expect(await screen.findByTestId("agent-empty")).toBeTruthy();
  });

  it("starts a run when clicking 开始闭环", async () => {
    const user = userEvent.setup();
    getLatest.mockResolvedValue(null);
    startRun.mockResolvedValue({ ...baseRun, status: "running" });
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
});
