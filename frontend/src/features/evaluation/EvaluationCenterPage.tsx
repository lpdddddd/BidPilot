import { useMemo, useState } from "react";
import { Alert, Select, Space, Tabs, Typography, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { listProjects } from "../../api/client";
import {
  cancelEvaluationRun,
  compareEvaluationRuns,
  createEvaluationRun,
  exportEvaluationRun,
  getEvaluationCapabilities,
  getEvaluationResult,
  listEvaluationResults,
  listEvaluationRuns,
  listEvaluationSuites,
  resumeEvaluationRun,
} from "../../api/evaluation";
import { ApiError } from "../../api/http";
import { usePageTitle } from "../../components/usePageTitle";
import type { EvaluationRunCreatePayload } from "../../types/api";
import ComparePanel from "./ComparePanel";
import NewEvaluationForm from "./NewEvaluationForm";
import OverviewPanel from "./OverviewPanel";
import RunDetailPanel from "./RunDetailPanel";
import RunListPanel, { type RunListFilters } from "./RunListPanel";
import {
  downloadBlob,
  parseEvaluationTab,
  type EvaluationTab,
} from "./evaluationParams";
import { useEvaluationRunPoll } from "./useEvaluationRunPoll";

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.status === 404) {
      return err.message || "资源不存在或无权访问（跨项目）";
    }
    return err.message || fallback;
  }
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

export default function EvaluationCenterPage() {
  usePageTitle("评测中心");
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const [msgApi, contextHolder] = message.useMessage();

  const projectId = searchParams.get("projectId") || "";
  const tab = parseEvaluationTab(searchParams.get("tab"));
  const runId = searchParams.get("runId");
  const resultId = searchParams.get("resultId");

  const [listFilters, setListFilters] = useState<RunListFilters>({});
  const [listPage, setListPage] = useState(1);
  const [listPageSize, setListPageSize] = useState(20);
  const [caseFilters, setCaseFilters] = useState<{
    status?: string;
    task_family?: string;
    hard_gate?: boolean;
  }>({});
  const [compareLeft, setCompareLeft] = useState<string | undefined>();
  const [compareRight, setCompareRight] = useState<string | undefined>();
  const [compareResult, setCompareResult] = useState<
    Awaited<ReturnType<typeof compareEvaluationRuns>> | undefined
  >();
  const [compareError, setCompareError] = useState<string | null>(null);
  const [actionBusyId, setActionBusyId] = useState<string | null>(null);

  const patchParams = (patch: Record<string, string | null | undefined>) => {
    const next = new URLSearchParams(searchParams);
    for (const [k, v] of Object.entries(patch)) {
      if (v == null || v === "") next.delete(k);
      else next.set(k, v);
    }
    setSearchParams(next);
  };

  const setTab = (next: EvaluationTab) => {
    patchParams({
      tab: next === "overview" ? null : next,
      runId: next === "runs" ? runId : null,
      resultId: next === "runs" ? resultId : null,
    });
  };

  const projectsQuery = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
    retry: 0,
  });

  const capsQuery = useQuery({
    queryKey: ["evaluation-capabilities", projectId],
    queryFn: () => getEvaluationCapabilities(projectId),
    enabled: Boolean(projectId),
    retry: 0,
  });

  const suitesQuery = useQuery({
    queryKey: ["evaluation-suites", projectId],
    queryFn: () => listEvaluationSuites(projectId),
    enabled: Boolean(projectId),
    retry: 0,
  });

  const runsQuery = useQuery({
    queryKey: [
      "evaluation-runs",
      projectId,
      listFilters,
      listPage,
      listPageSize,
    ],
    queryFn: () =>
      listEvaluationRuns(projectId, {
        ...listFilters,
        limit: listPageSize,
        offset: (listPage - 1) * listPageSize,
      }),
    enabled: Boolean(projectId),
    retry: 0,
  });

  const overviewRunsQuery = useQuery({
    queryKey: ["evaluation-runs-overview", projectId],
    queryFn: () => listEvaluationRuns(projectId, { limit: 10, offset: 0 }),
    enabled: Boolean(projectId) && tab === "overview",
    retry: 0,
  });

  const runPoll = useEvaluationRunPoll({
    projectId,
    runId,
    enabled: Boolean(projectId && runId && tab === "runs"),
  });

  const resultsQuery = useQuery({
    queryKey: ["evaluation-results", projectId, runId, caseFilters],
    queryFn: () =>
      listEvaluationResults(projectId, runId!, {
        status: caseFilters.status,
        task_family: caseFilters.task_family,
        hard_gate: caseFilters.hard_gate,
        limit: 100,
        offset: 0,
      }),
    enabled: Boolean(projectId && runId && tab === "runs"),
    retry: 0,
  });

  const caseQuery = useQuery({
    queryKey: ["evaluation-result", projectId, runId, resultId],
    queryFn: () => getEvaluationResult(projectId, runId!, resultId!),
    enabled: Boolean(projectId && runId && resultId && tab === "runs"),
    retry: 0,
  });

  const createMutation = useMutation({
    mutationFn: ({
      payload,
      key,
    }: {
      payload: EvaluationRunCreatePayload;
      key: string;
    }) => createEvaluationRun(projectId, payload, key),
    onSuccess: (run) => {
      void queryClient.invalidateQueries({ queryKey: ["evaluation-runs"] });
      void queryClient.invalidateQueries({ queryKey: ["evaluation-runs-overview"] });
      patchParams({ tab: "runs", runId: run.id, resultId: null });
      msgApi.success("评测已创建");
    },
  });

  const compareMutation = useMutation({
    mutationFn: () => compareEvaluationRuns(projectId, compareLeft!, compareRight!),
    onSuccess: (data) => {
      setCompareResult(data);
      setCompareError(null);
    },
    onError: (err) => {
      setCompareResult(undefined);
      setCompareError(errorMessage(err, "对比失败"));
    },
  });

  const overviewError = useMemo(() => {
    if (!projectId) return null;
    if (capsQuery.isError) return errorMessage(capsQuery.error, "加载能力失败");
    if (suitesQuery.isError) return errorMessage(suitesQuery.error, "加载套件失败");
    if (overviewRunsQuery.isError) {
      return errorMessage(overviewRunsQuery.error, "加载运行列表失败");
    }
    return null;
  }, [
    projectId,
    capsQuery.isError,
    capsQuery.error,
    suitesQuery.isError,
    suitesQuery.error,
    overviewRunsQuery.isError,
    overviewRunsQuery.error,
  ]);

  const handleCancel = async (id: string) => {
    setActionBusyId(id);
    try {
      await cancelEvaluationRun(projectId, id);
      msgApi.success("已请求取消");
      void queryClient.invalidateQueries({ queryKey: ["evaluation-runs"] });
      void queryClient.invalidateQueries({ queryKey: ["evaluation-run", projectId, id] });
    } catch (err) {
      msgApi.error(errorMessage(err, "取消失败"));
    } finally {
      setActionBusyId(null);
    }
  };

  const handleResume = async (id: string) => {
    setActionBusyId(id);
    try {
      await resumeEvaluationRun(projectId, id);
      msgApi.success("已恢复评测");
      void queryClient.invalidateQueries({ queryKey: ["evaluation-runs"] });
      void queryClient.invalidateQueries({ queryKey: ["evaluation-run", projectId, id] });
    } catch (err) {
      msgApi.error(errorMessage(err, "恢复失败"));
    } finally {
      setActionBusyId(null);
    }
  };

  const handleExport = async (id: string, format: "json" | "csv" | "markdown") => {
    try {
      const { blob, filename } = await exportEvaluationRun(projectId, id, format);
      downloadBlob(blob, filename);
      msgApi.success(`已导出 ${format}`);
    } catch (err) {
      msgApi.error(errorMessage(err, "导出失败"));
    }
  };

  const openRun = (id: string) => {
    patchParams({ tab: "runs", runId: id, resultId: null });
  };

  return (
    <div data-testid="evaluation-center">
      {contextHolder}
      <div className="bp-page-header">
        <div className="bp-page-header-row">
          <div>
            <h1 className="bp-page-title">评测中心</h1>
            <p className="bp-page-subtitle">
              对 RAG、抽取、匹配、合规、草稿与 Agent 流程进行可复现自动评测；不展示 CoT、完整 prompt 或密钥。
            </p>
          </div>
          <Select
            style={{ minWidth: 280 }}
            placeholder="选择项目"
            data-testid="eval-project-select"
            loading={projectsQuery.isLoading}
            value={projectId || undefined}
            options={(projectsQuery.data?.items ?? []).map((p) => ({
              value: p.id,
              label: `${p.project_name} (${p.project_code})`,
            }))}
            onChange={(id: string) => {
              setSearchParams({ projectId: id });
              setCompareResult(undefined);
              setListPage(1);
            }}
            showSearch
            optionFilterProp="label"
          />
        </div>
      </div>

      {projectsQuery.isError && (
        <Alert type="error" showIcon message="无法加载项目列表" style={{ marginBottom: 16 }} />
      )}

      {!projectId ? (
        <div className="bp-empty-block" data-testid="eval-need-project">
          <div className="bp-empty-title">请选择项目</div>
          <div className="bp-empty-desc">评测中心为项目级能力，选择项目后查看概览、创建与对比评测。</div>
        </div>
      ) : (
        <div className="bp-panel">
          {runId && tab === "runs" ? (
            <RunDetailPanel
              projectId={projectId}
              run={runPoll.data}
              runLoading={runPoll.isLoading}
              runError={
                runPoll.isError ? errorMessage(runPoll.error, "加载 Run 失败") : null
              }
              isPolling={runPoll.isPolling}
              results={resultsQuery.data?.items ?? []}
              resultsTotal={resultsQuery.data?.total ?? 0}
              resultsLoading={resultsQuery.isLoading}
              caseFilters={caseFilters}
              onCaseFiltersChange={setCaseFilters}
              onOpenCase={(id) => patchParams({ resultId: id })}
              onBack={() => patchParams({ runId: null, resultId: null })}
              onCancel={() => void handleCancel(runId)}
              onResume={() => void handleResume(runId)}
              onExport={(fmt) => void handleExport(runId, fmt)}
              selectedCase={caseQuery.data ?? null}
              selectedCaseLoading={caseQuery.isLoading}
              selectedCaseError={
                caseQuery.isError ? errorMessage(caseQuery.error, "加载 Case 失败") : null
              }
              onCloseCase={() => patchParams({ resultId: null })}
            />
          ) : (
            <Tabs
              activeKey={tab}
              onChange={(k) => setTab(parseEvaluationTab(k))}
              items={[
                {
                  key: "overview",
                  label: "概览",
                  children: (
                    <OverviewPanel
                      loading={
                        capsQuery.isLoading ||
                        suitesQuery.isLoading ||
                        overviewRunsQuery.isLoading
                      }
                      error={overviewError}
                      capabilities={capsQuery.data}
                      suites={suitesQuery.data?.items ?? []}
                      runs={overviewRunsQuery.data?.items ?? []}
                      onOpenRun={openRun}
                    />
                  ),
                },
                {
                  key: "new",
                  label: "新建评测",
                  children: (
                    <NewEvaluationForm
                      suites={suitesQuery.data?.items ?? []}
                      capabilities={capsQuery.data}
                      submitting={createMutation.isPending}
                      error={
                        createMutation.isError
                          ? errorMessage(createMutation.error, "创建失败")
                          : null
                      }
                      onSubmit={(payload, key) => {
                        if (createMutation.isPending) return;
                        createMutation.mutate({ payload, key });
                      }}
                    />
                  ),
                },
                {
                  key: "runs",
                  label: "运行列表",
                  children: (
                    <>
                      {runsQuery.isError && (
                        <Alert
                          type="error"
                          showIcon
                          style={{ marginBottom: 12 }}
                          message="加载运行列表失败"
                          description={errorMessage(runsQuery.error, "请稍后重试")}
                          data-testid="eval-runs-error"
                        />
                      )}
                      <RunListPanel
                        runs={runsQuery.data?.items ?? []}
                        total={runsQuery.data?.total ?? 0}
                        loading={runsQuery.isLoading}
                        page={listPage}
                        pageSize={listPageSize}
                        filters={listFilters}
                        onFiltersChange={(f) => {
                          setListFilters(f);
                          setListPage(1);
                        }}
                        onPageChange={(p, ps) => {
                          setListPage(p);
                          setListPageSize(ps);
                        }}
                        onView={openRun}
                        onCancel={(id) => void handleCancel(id)}
                        onResume={(id) => void handleResume(id)}
                        onExport={(id, fmt) => void handleExport(id, fmt)}
                        actionBusyId={actionBusyId}
                      />
                    </>
                  ),
                },
                {
                  key: "compare",
                  label: "Run 对比",
                  children: (
                    <ComparePanel
                      runs={runsQuery.data?.items ?? overviewRunsQuery.data?.items ?? []}
                      leftId={compareLeft}
                      rightId={compareRight}
                      onSelect={(l, r) => {
                        setCompareLeft(l);
                        setCompareRight(r);
                      }}
                      comparing={compareMutation.isPending}
                      result={compareResult}
                      error={compareError}
                      onCompare={() => {
                        if (!compareLeft || !compareRight) return;
                        compareMutation.mutate();
                      }}
                    />
                  ),
                },
              ]}
            />
          )}
        </div>
      )}

      {projectId && (capsQuery.isFetching || suitesQuery.isFetching) && tab === "new" && (
        <Typography.Text type="secondary" style={{ display: "block", marginTop: 8 }}>
          <Space>加载套件与能力…</Space>
        </Typography.Text>
      )}
    </div>
  );
}
