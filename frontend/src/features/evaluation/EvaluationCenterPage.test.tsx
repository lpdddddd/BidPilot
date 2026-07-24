import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/http";
import EvaluationCenterPage from "./EvaluationCenterPage";
import WorkbenchLayout from "../../layouts/WorkbenchLayout";

const listProjects = vi.fn();
const getCapabilities = vi.fn();
const listSuites = vi.fn();
const listRuns = vi.fn();
const getRun = vi.fn();
const listResults = vi.fn();
const getResult = vi.fn();
const createRun = vi.fn();
const cancelRun = vi.fn();
const resumeRun = vi.fn();
const compareRuns = vi.fn();
const exportRun = vi.fn();

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return {
    ...actual,
    listProjects: (...args: unknown[]) => listProjects(...args),
  };
});

vi.mock("../../api/evaluation", () => ({
  getEvaluationCapabilities: (...args: unknown[]) => getCapabilities(...args),
  listEvaluationSuites: (...args: unknown[]) => listSuites(...args),
  listEvaluationRuns: (...args: unknown[]) => listRuns(...args),
  getEvaluationRun: (...args: unknown[]) => getRun(...args),
  listEvaluationResults: (...args: unknown[]) => listResults(...args),
  getEvaluationResult: (...args: unknown[]) => getResult(...args),
  createEvaluationRun: (...args: unknown[]) => createRun(...args),
  cancelEvaluationRun: (...args: unknown[]) => cancelRun(...args),
  resumeEvaluationRun: (...args: unknown[]) => resumeRun(...args),
  compareEvaluationRuns: (...args: unknown[]) => compareRuns(...args),
  exportEvaluationRun: (...args: unknown[]) => exportRun(...args),
}));

vi.mock("../../components/BackendStatus", () => ({
  default: () => <span>status</span>,
}));

const PROJECT = {
  id: "proj-1",
  organization_id: "org",
  project_code: "P1",
  project_name: "示范项目",
  status: "active",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const SUITE = {
  id: "suite-1",
  name: "reference_dataset",
  version: "1.0.0",
  dataset_hash: "abcdeffedcba1234",
  evaluator_profile_version: "bidpilot-eval-1.0.0",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const CAPS = {
  items: [
    { target_type: "deterministic_fake", available: true, label: "确定性假目标" },
    {
      target_type: "rag",
      available: false,
      reason: "Embedding/retrieval stack not available",
      reason_code: "project_dependency_missing",
      label: "RAG",
    },
    {
      target_type: "extraction",
      available: false,
      reason: "extraction case-level evaluation adapter is not wired to formal service",
      reason_code: "service_not_wired",
      label: "需求抽取",
    },
  ],
  evaluator_version: "bidpilot-eval-1.0.0",
  profiles: [{ id: "default", version: "bidpilot-eval-1.0.0", name: "默认" }],
  dataset: {
    name: "reference_dataset",
    version: "1.0.0",
    dataset_hash: "abcdeffedcba1234",
    direct_reference_coverage: 0.8,
  },
  task_families: ["rag", "extraction"],
  splits: ["train", "validation", "test"],
};

function makeRun(overrides: Record<string, unknown> = {}) {
  return {
    id: "run-1",
    project_id: "proj-1",
    suite_id: "suite-1",
    suite_name: "reference_dataset",
    suite_version: "1.0.0",
    status: "completed",
    target_type: "deterministic_fake",
    dataset_hash: "abcdeffedcba1234",
    evaluator_version: "bidpilot-eval-1.0.0",
    seed: 42,
    total_cases: 10,
    completed_cases: 10,
    passed_cases: 8,
    failed_cases: 1,
    error_cases: 1,
    overall_score: 0.82,
    started_at: "2026-07-01T00:00:00Z",
    finished_at: "2026-07-01T00:01:00Z",
    duration_ms: 60000,
    summary_json: {
      overall_score: 0.82,
      pass_rate: 0.8,
      error_rate: 0.1,
      direct_reference_coverage: 0.75,
      task_family_scores: { rag: 0.9, extraction: 0.7 },
      metric_averages: { hit_at_k: 0.85 },
      hard_gate_failure_count: 1,
      hard_gate_failures: ["hard_gate_unlocatable_citation"],
    },
    target_config_snapshot: { temperature: 0, model_name: "fake" },
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:01:00Z",
    ...overrides,
  };
}

function makeCase(overrides: Record<string, unknown> = {}) {
  return {
    id: "result-1",
    evaluation_run_id: "run-1",
    case_key: "case-rag-001",
    case_content_hash: "hash1",
    task_family: "rag",
    split: "validation",
    status: "failed",
    score: 0.2,
    passed: false,
    reference_kind: "auto_reference",
    hard_gate_failures: ["hard_gate_unlocatable_citation"],
    input_snapshot: { question: "资质要求？" },
    response_snapshot: {
      answer: "需一级资质",
      prompt: "SHOULD_NOT_SHOW",
      chain_of_thought: "hidden",
    },
    reference_summary: {
      reference_kind: "auto_reference",
      source_description: "auto reference from dataset",
      label_source: "auto_reference",
    },
    metric_results: [
      {
        metric_name: "hit_at_k",
        metric_version: "1.0.0",
        value: 0.0,
        applicable: true,
        weight: 1,
        threshold: 0.5,
        passed: false,
        reference_kind: "auto_reference",
        evidence_summary: "no hit",
      },
      {
        metric_name: "optional_metric",
        metric_version: "1.0.0",
        value: null,
        applicable: false,
        weight: 0,
        threshold: null,
        passed: null,
        reference_kind: "not_applicable",
      },
    ],
    citations: [
      {
        document_id: "doc-1",
        page: 2,
        chunk_id: "chunk-ok",
        valid: true,
      },
      {
        document_id: "missing",
        valid: false,
        validation_error: "chunk 不存在",
      },
    ],
    agent_run_id: "agent-1",
    ...overrides,
  };
}

function renderAt(path: string, withNav = false) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const page = (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        {withNav ? (
          <WorkbenchLayout>
            <Routes>
              <Route path="/evaluation" element={<EvaluationCenterPage />} />
            </Routes>
          </WorkbenchLayout>
        ) : (
          <Routes>
            <Route path="/evaluation" element={<EvaluationCenterPage />} />
          </Routes>
        )}
      </MemoryRouter>
    </QueryClientProvider>
  );
  return render(page);
}

describe("EvaluationCenterPage", () => {
  beforeEach(() => {
    listProjects.mockReset();
    getCapabilities.mockReset();
    listSuites.mockReset();
    listRuns.mockReset();
    getRun.mockReset();
    listResults.mockReset();
    getResult.mockReset();
    createRun.mockReset();
    cancelRun.mockReset();
    resumeRun.mockReset();
    compareRuns.mockReset();
    exportRun.mockReset();

    listProjects.mockResolvedValue({ items: [PROJECT], total: 1 });
    getCapabilities.mockResolvedValue(CAPS);
    listSuites.mockResolvedValue({ items: [SUITE], total: 1 });
    listRuns.mockResolvedValue({ items: [makeRun()], total: 1 });
    getRun.mockResolvedValue(makeRun());
    listResults.mockResolvedValue({ items: [makeCase()], total: 1 });
    getResult.mockResolvedValue(makeCase());
    createRun.mockResolvedValue(makeRun({ id: "run-new", status: "queued" }));
    cancelRun.mockResolvedValue(makeRun({ status: "cancelled" }));
    resumeRun.mockResolvedValue(makeRun({ status: "running" }));
    exportRun.mockResolvedValue({
      blob: new Blob(["ok"]),
      filename: "export.json",
    });
    compareRuns.mockResolvedValue({
      left: makeRun({
        id: "run-1",
        dataset_hash: "aaa",
        target_config_snapshot: {
          model_id: "qwen3-8b-base",
          served_model_name: "bidpilot-qwen3-8b",
          model_type: "base",
        },
      }),
      right: makeRun({
        id: "run-2",
        dataset_hash: "bbb",
        evaluator_version: "other",
        target_config_snapshot: {
          model_id: "qwen3-8b-lora-course",
          served_model_name: "bidpilot-qwen3-8b-course-lora",
          model_type: "lora",
        },
      }),
      warnings: ["dataset hash mismatch", "evaluator version mismatch"],
      overall_score_delta: -0.05,
      pass_rate_delta: -0.1,
      task_family_deltas: { rag: -0.1 },
      metric_deltas: { hit_at_k: -0.2 },
      improved_cases: [],
      regressed_cases: [{ case_key: "case-rag-001", left_score: 0.9, right_score: 0.2, delta: -0.7 }],
      unchanged_cases: [],
      left_only_cases: ["only-left"],
      right_only_cases: [],
    });

    // URL.createObjectURL for export
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:mock"),
      revokeObjectURL: vi.fn(),
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("1. shows route and nav label 评估中心", async () => {
    const user = userEvent.setup();
    renderAt("/evaluation", true);
    expect(await screen.findByTestId("evaluation-center")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "更多" }));
    expect(await screen.findByRole("link", { name: "评估中心" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "评估中心" })).toBeTruthy();
  });

  it("2. overview empty state", async () => {
    listRuns.mockResolvedValue({ items: [], total: 0 });
    getCapabilities.mockResolvedValue({
      ...CAPS,
      dataset: null,
    });
    listSuites.mockResolvedValue({ items: [], total: 0 });
    renderAt("/evaluation?projectId=proj-1");
    expect(await screen.findByTestId("eval-overview-empty")).toBeTruthy();
  });

  it("3. overview success state", async () => {
    renderAt("/evaluation?projectId=proj-1");
    expect(await screen.findByTestId("eval-overview")).toBeTruthy();
    expect((await screen.findByTestId("eval-stat-score")).textContent).toContain("0.820");
    expect(await screen.findByTestId("eval-family-scores")).toBeTruthy();
    expect(await screen.findByTestId("eval-trend")).toBeTruthy();
    expect((await screen.findByTestId("eval-available-targets")).textContent).toContain(
      "确定性假目标",
    );
  });

  it("4+5+6. create run, unavailable disabled, double-click guard", async () => {
    const user = userEvent.setup();
    renderAt("/evaluation?projectId=proj-1&tab=new");
    expect(await screen.findByTestId("eval-new-form")).toBeTruthy();

    // Open target select and assert unavailable option disabled
    const targetSelect = screen.getByTestId("eval-target-select");
    await user.click(within(targetSelect).getByRole("combobox"));
    const unavailable = await screen.findByText(/RAG（不可用：检索依赖未就绪）/);
    expect(unavailable.textContent || "").toMatch(/不可用|暂未开放|未就绪/);
    expect(unavailable.textContent || "").not.toMatch(/service_not_wired|project_dependency_missing/);
    const optionEl = unavailable.closest(".ant-select-item") || unavailable.parentElement;
    expect(optionEl?.className || "").toMatch(/disabled|ant-select-item-option-disabled/);

    const unwired = await screen.findByText(/需求抽取（不可用：当前版本暂未开放）/);
    expect(unwired.textContent || "").toMatch(/不可用|暂未开放/);
    expect(unwired.textContent || "").not.toContain("service_not_wired");

    // Pick available target
    await user.click(await screen.findByText(/确定性假目标/));

    const btn = await screen.findByTestId("eval-start-btn");
    await user.dblClick(btn);
    await waitFor(() => expect(createRun).toHaveBeenCalledTimes(1));
    expect(createRun.mock.calls[0][0]).toBe("proj-1");
    expect(createRun.mock.calls[0][1].target).toBe("deterministic_fake");
    await waitFor(() => expect(screen.getByTestId("eval-run-detail")).toBeTruthy());
  });

  it("7. run list filter", async () => {
    const user = userEvent.setup();
    renderAt("/evaluation?projectId=proj-1&tab=runs");
    expect(await screen.findByTestId("eval-run-list")).toBeTruthy();
    const statusFilter = screen.getByTestId("eval-filter-status");
    await user.click(within(statusFilter).getByRole("combobox"));
    await user.click(await screen.findByText("失败"));
    await waitFor(() =>
      expect(listRuns).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ status: "failed" }),
      ),
    );
  });

  it("11+12+13+15+16+24. run detail metrics, N/A, hard gate, citations, no prompt/CoT", async () => {
    const user = userEvent.setup();
    renderAt("/evaluation?projectId=proj-1&tab=runs&runId=run-1");
    expect(await screen.findByTestId("eval-run-detail")).toBeTruthy();
    expect(screen.getByTestId("eval-run-families")).toBeTruthy();
    expect(screen.getByText("Hard Gate 失败")).toBeTruthy();

    await user.click(await screen.findByTestId("eval-case-open-result-1"));
    expect(await screen.findByTestId("eval-case-detail")).toBeTruthy();
    expect(screen.getByTestId("eval-case-hard-gates").textContent).toContain(
      "hard_gate_unlocatable_citation",
    );
    expect(screen.getByTestId("eval-metric-value-optional_metric").textContent).toContain("N/A");

    const citations = screen.getByTestId("eval-case-citations");
    const link = within(citations).getByRole("link");
    expect(link.getAttribute("href") || "").toContain("chunk_id=chunk-ok");
    expect(citations.textContent).toMatch(/chunk 不存在|引用校验失败/);
    expect(citations.querySelector(".bp-citation-invalid")).toBeTruthy();

    const output = screen.getByTestId("eval-case-output").textContent || "";
    expect(output).toContain("需一级资质");
    expect(output).not.toContain("SHOULD_NOT_SHOW");
    expect(output).not.toContain("chain_of_thought");
    expect(output).not.toContain('"prompt"');
    expect(screen.getByTestId("eval-agent-run-link")).toBeTruthy();
  });

  it("17. cancel run", async () => {
    const user = userEvent.setup();
    listRuns.mockResolvedValue({
      items: [makeRun({ status: "running", completed_cases: 2 })],
      total: 1,
    });
    renderAt("/evaluation?projectId=proj-1&tab=runs");
    await screen.findByTestId("eval-run-list");
    await user.click(await screen.findByTestId("eval-run-cancel-run-1"));
    await waitFor(() => expect(cancelRun).toHaveBeenCalledWith("proj-1", "run-1"));
  });

  it("18. resume run", async () => {
    const user = userEvent.setup();
    listRuns.mockResolvedValue({
      items: [makeRun({ status: "cancelled", completed_cases: 2 })],
      total: 1,
    });
    renderAt("/evaluation?projectId=proj-1&tab=runs");
    await screen.findByTestId("eval-run-list");
    await user.click(await screen.findByTestId("eval-run-resume-run-1"));
    await waitFor(() => expect(resumeRun).toHaveBeenCalledWith("proj-1", "run-1"));
  });

  it("19+20. compare with dataset hash mismatch warning", async () => {
    const user = userEvent.setup();
    listRuns.mockResolvedValue({
      items: [
        makeRun({ id: "run-1" }),
        makeRun({ id: "run-2", overall_score: 0.7 }),
      ],
      total: 2,
    });
    renderAt("/evaluation?projectId=proj-1&tab=compare");
    expect(await screen.findByTestId("eval-compare")).toBeTruthy();

    await user.click(within(screen.getByTestId("eval-compare-left")).getByRole("combobox"));
    await user.click(await screen.findByText(/run-1|abcdef12|确定性/i).catch(() => screen.findAllByText(/确定性假目标/).then((els) => els[0])));
    // More reliable: click first option texts containing score
    const leftOptions = await screen.findAllByText(/确定性假目标/);
    await user.click(leftOptions[0]);

    await user.click(within(screen.getByTestId("eval-compare-right")).getByRole("combobox"));
    const rightOptions = await screen.findAllByText(/确定性假目标/);
    await user.click(rightOptions[rightOptions.length - 1]);

    await user.click(await screen.findByTestId("eval-compare-btn"));
    await waitFor(() => expect(compareRuns).toHaveBeenCalled());
    expect(await screen.findByTestId("eval-compare-mismatch-warning")).toBeTruthy();
    expect(screen.getByTestId("eval-compare-result")).toBeTruthy();
    expect(screen.getByTestId("eval-compare-left-model").textContent).toMatch(
      /bidpilot-qwen3-8b|qwen3-8b-base/,
    );
    expect(screen.getByTestId("eval-compare-right-model").textContent).toMatch(
      /bidpilot-qwen3-8b-course-lora|qwen3-8b-lora-course/,
    );
  });

  it("21. export", async () => {
    const user = userEvent.setup();
    renderAt("/evaluation?projectId=proj-1&tab=runs&runId=run-1");
    await screen.findByTestId("eval-run-detail");
    await user.click(screen.getByTestId("eval-run-detail-export-json"));
    await waitFor(() => expect(exportRun).toHaveBeenCalledWith("proj-1", "run-1", "json"));
  });

  it("22. cross-project error", async () => {
    getRun.mockRejectedValue(new ApiError("不存在", 404));
    renderAt("/evaluation?projectId=proj-1&tab=runs&runId=other-run");
    const err = await screen.findByTestId("eval-run-detail-error");
    expect(err.textContent || "").toMatch(/不存在|跨项目|无权/);
  });

  it("23. API failure shows safe error on overview", async () => {
    getCapabilities.mockRejectedValue(new ApiError("后端暂时不可用", 503));
    renderAt("/evaluation?projectId=proj-1");
    const err = await screen.findByTestId("eval-overview-error");
    expect(err.textContent || "").toContain("后端暂时不可用");
    expect(screen.queryByTestId("eval-stat-score")).toBeNull();
  });
});
