import { Link } from "react-router-dom";
import {
  Alert,
  Button,
  Descriptions,
  Empty,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import type {
  EvaluationCaseResult,
  EvaluationMetricResult,
  EvaluationRun,
} from "../../types/api";
import ScoreBar from "./ScoreBar";
import {
  agentRunHref,
  errorRate,
  evaluationRunStatusLabel,
  evaluationTargetLabel,
  formatDurationMs,
  formatPercent,
  formatScore,
  hardGateFailureCount,
  hardGateLabels,
  isActiveEvaluationStatus,
  metricDisplayValue,
  passRate,
  referenceCoverage,
  runProgress,
  safeJsonPreview,
  shortHash,
  taskFamilyScores,
  validateEvaluationCitation,
} from "./evaluationParams";

type CaseFilters = {
  status?: string;
  task_family?: string;
  hard_gate?: boolean;
};

type Props = {
  projectId: string;
  run: EvaluationRun | undefined;
  runLoading: boolean;
  runError: string | null;
  isPolling: boolean;
  results: EvaluationCaseResult[];
  resultsTotal: number;
  resultsLoading: boolean;
  caseFilters: CaseFilters;
  onCaseFiltersChange: (next: CaseFilters) => void;
  onOpenCase: (resultId: string) => void;
  onBack: () => void;
  onCancel: () => void;
  onResume: () => void;
  onExport: (format: "json" | "csv" | "markdown") => void;
  selectedCase: EvaluationCaseResult | null;
  selectedCaseLoading: boolean;
  selectedCaseError: string | null;
  onCloseCase: () => void;
};

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bp-panel" style={{ padding: 14, minWidth: 120 }}>
      <Typography.Text type="secondary">{label}</Typography.Text>
      <div style={{ fontSize: 20, fontWeight: 600, marginTop: 4 }}>{value}</div>
    </div>
  );
}

export default function RunDetailPanel({
  projectId,
  run,
  runLoading,
  runError,
  isPolling,
  results,
  resultsTotal,
  resultsLoading,
  caseFilters,
  onCaseFiltersChange,
  onOpenCase,
  onBack,
  onCancel,
  onResume,
  onExport,
  selectedCase,
  selectedCaseLoading,
  selectedCaseError,
  onCloseCase,
}: Props) {
  if (runLoading && !run) {
    return (
      <div style={{ padding: 48, textAlign: "center" }} data-testid="eval-run-detail-loading">
        <Spin />
      </div>
    );
  }

  if (runError && !run) {
    return (
      <Alert
        type="error"
        showIcon
        message="无法加载评测 Run"
        description={runError}
        data-testid="eval-run-detail-error"
        action={
          <Button size="small" onClick={onBack}>
            返回列表
          </Button>
        }
      />
    );
  }

  if (!run) {
    return <Empty description="未选择 Run" />;
  }

  const familyScores = taskFamilyScores(run);
  const metricAvgs = run.summary_json?.metric_averages ?? {};
  const canResume =
    run.status === "cancelled" || run.status === "partial" || run.status === "failed";

  const caseColumns: ColumnsType<EvaluationCaseResult> = [
    {
      title: "Case",
      dataIndex: "case_key",
      render: (v: string, row) => (
        <Button type="link" data-testid={`eval-case-open-${row.id}`} onClick={() => onOpenCase(row.id)}>
          {v}
        </Button>
      ),
    },
    { title: "Family", dataIndex: "task_family", width: 120 },
    { title: "Split", dataIndex: "split", width: 100 },
    {
      title: "状态",
      dataIndex: "status",
      width: 100,
      render: (s: string) => <Tag>{s}</Tag>,
    },
    {
      title: "Score",
      dataIndex: "score",
      width: 90,
      render: (v: number | null | undefined) => formatScore(v),
    },
    {
      title: "Hard Gate",
      key: "hg",
      width: 120,
      render: (_, row) => {
        const labels = hardGateLabels(row.hard_gate_failures);
        return labels.length ? (
          <Typography.Text type="danger" data-testid={`eval-case-hg-${row.id}`}>
            {labels.join(", ")}
          </Typography.Text>
        ) : (
          "—"
        );
      },
    },
    {
      title: "Reference",
      dataIndex: "reference_kind",
      width: 140,
      ellipsis: true,
    },
  ];

  const metricColumns: ColumnsType<EvaluationMetricResult> = [
    { title: "指标", dataIndex: "metric_name" },
    {
      title: "值",
      key: "value",
      width: 100,
      render: (_, m) => (
        <span data-testid={`eval-metric-value-${m.metric_name}`}>
          {metricDisplayValue(m)}
        </span>
      ),
    },
    {
      title: "阈值",
      dataIndex: "threshold",
      width: 90,
      render: (v: number | null | undefined) => (v == null ? "—" : formatScore(v)),
    },
    {
      title: "通过",
      dataIndex: "passed",
      width: 80,
      render: (v: boolean | null | undefined, m) =>
        !m.applicable ? "N/A" : v == null ? "—" : v ? "是" : "否",
    },
    {
      title: "适用",
      dataIndex: "applicable",
      width: 80,
      render: (v: boolean) => (v ? "是" : "N/A"),
    },
    {
      title: "Reference Kind",
      dataIndex: "reference_kind",
      width: 160,
    },
    {
      title: "证据摘要",
      dataIndex: "evidence_summary",
      ellipsis: true,
      render: (v: string | null | undefined) => v || "—",
    },
  ];

  return (
    <div data-testid="eval-run-detail" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Space wrap>
        <Button onClick={onBack} data-testid="eval-run-back">
          返回列表
        </Button>
        {isActiveEvaluationStatus(run.status) && (
          <Button danger onClick={onCancel} data-testid="eval-run-detail-cancel">
            取消
          </Button>
        )}
        {canResume && (
          <Button onClick={onResume} data-testid="eval-run-detail-resume">
            恢复
          </Button>
        )}
        <Button onClick={() => onExport("json")} data-testid="eval-run-detail-export-json">
          导出 JSON
        </Button>
        <Button onClick={() => onExport("csv")} data-testid="eval-run-detail-export-csv">
          导出 CSV
        </Button>
        <Button onClick={() => onExport("markdown")} data-testid="eval-run-detail-export-md">
          导出 Markdown
        </Button>
        {isPolling && (
          <Tag color="processing" data-testid="eval-polling-indicator">
            轮询中…
          </Tag>
        )}
      </Space>

      <Space wrap>
        <Tag color="blue">{evaluationRunStatusLabel(String(run.status))}</Tag>
        <Typography.Text>
          进度 {run.completed_cases}/{run.total_cases} ({runProgress(run)}%)
        </Typography.Text>
        <Typography.Text type="secondary">
          {evaluationTargetLabel(String(run.target_type))}
        </Typography.Text>
      </Space>

      {run.safe_error_summary && (
        <Alert type="warning" showIcon message={run.safe_error_summary} data-testid="eval-run-safe-error" />
      )}

      <Space wrap size="middle">
        <Stat label="Overall" value={formatScore(run.overall_score ?? run.summary_json?.overall_score)} />
        <Stat label="Pass Rate" value={formatPercent(passRate(run))} />
        <Stat label="Error Rate" value={formatPercent(errorRate(run))} />
        <Stat label="Reference 覆盖" value={formatPercent(referenceCoverage(run))} />
        <Stat
          label="Hard Gate 失败"
          value={String(hardGateFailureCount(run))}
        />
      </Space>

      <Descriptions size="small" column={2} bordered data-testid="eval-run-meta">
        <Descriptions.Item label="Evaluator">{run.evaluator_version}</Descriptions.Item>
        <Descriptions.Item label="Dataset Hash">{shortHash(run.dataset_hash, 16)}</Descriptions.Item>
        <Descriptions.Item label="Source Commit">
          {run.source_commit_sha ? shortHash(run.source_commit_sha, 10) : "—"}
        </Descriptions.Item>
        <Descriptions.Item label="Seed">{run.seed}</Descriptions.Item>
        <Descriptions.Item label="开始">{run.started_at || "—"}</Descriptions.Item>
        <Descriptions.Item label="结束">{run.finished_at || "—"}</Descriptions.Item>
        <Descriptions.Item label="耗时">{formatDurationMs(run.duration_ms)}</Descriptions.Item>
        <Descriptions.Item label="Suite">
          {(run.suite_name || run.suite_id) + (run.suite_version ? ` · v${run.suite_version}` : "")}
        </Descriptions.Item>
      </Descriptions>

      {run.target_config_snapshot && (
        <div className="bp-panel-quiet" data-testid="eval-run-config">
          <div className="bp-section-title">运行配置（安全快照）</div>
          <pre className="bp-code-block">{safeJsonPreview(run.target_config_snapshot)}</pre>
        </div>
      )}

      {Object.keys(familyScores).length > 0 && (
        <div className="bp-panel-quiet" data-testid="eval-run-families">
          <div className="bp-section-title">Task Family 分数</div>
          {Object.entries(familyScores).map(([k, v]) => (
            <ScoreBar key={k} label={k} value={v} />
          ))}
        </div>
      )}

      {Object.keys(metricAvgs).length > 0 && (
        <div className="bp-panel-quiet" data-testid="eval-run-metrics-dist">
          <div className="bp-section-title">Metric 分布</div>
          {Object.entries(metricAvgs).map(([k, v]) => (
            <ScoreBar key={k} label={k} value={v} />
          ))}
        </div>
      )}

      <div>
        <div className="bp-section-title">Case 结果</div>
        <Space wrap style={{ marginBottom: 8 }}>
          <Select
            allowClear
            style={{ width: 140 }}
            placeholder="状态"
            data-testid="eval-case-filter-status"
            value={caseFilters.status}
            options={["passed", "failed", "error", "pending", "running", "skipped", "cancelled"].map(
              (s) => ({ value: s, label: s }),
            )}
            onChange={(status) => onCaseFiltersChange({ ...caseFilters, status })}
          />
          <Select
            allowClear
            style={{ width: 160 }}
            placeholder="Task family"
            data-testid="eval-case-filter-family"
            value={caseFilters.task_family}
            options={Array.from(new Set(results.map((r) => r.task_family))).map((f) => ({
              value: f,
              label: f,
            }))}
            onChange={(task_family) => onCaseFiltersChange({ ...caseFilters, task_family })}
          />
          <Select
            allowClear
            style={{ width: 140 }}
            placeholder="Hard gate"
            data-testid="eval-case-filter-hg"
            value={
              caseFilters.hard_gate === true
                ? "yes"
                : caseFilters.hard_gate === false
                  ? "no"
                  : undefined
            }
            options={[
              { value: "yes", label: "仅 hard gate" },
              { value: "no", label: "无 hard gate" },
            ]}
            onChange={(v) =>
              onCaseFiltersChange({
                ...caseFilters,
                hard_gate: v === "yes" ? true : v === "no" ? false : undefined,
              })
            }
          />
        </Space>
        <Table
          rowKey="id"
          size="small"
          loading={resultsLoading}
          columns={caseColumns}
          dataSource={results}
          pagination={{ pageSize: 20, total: resultsTotal }}
          locale={{ emptyText: "无 case 结果" }}
        />
      </div>

      {selectedCaseLoading && (
        <div style={{ padding: 24, textAlign: "center" }}>
          <Spin />
        </div>
      )}
      {selectedCaseError && (
        <Alert type="error" showIcon message={selectedCaseError} data-testid="eval-case-error" />
      )}
      {selectedCase && (
        <div className="bp-panel" data-testid="eval-case-detail">
          <Space style={{ marginBottom: 12 }}>
            <Typography.Title level={5} style={{ margin: 0 }}>
              Case · {selectedCase.case_key}
            </Typography.Title>
            <Button size="small" onClick={onCloseCase} data-testid="eval-case-close">
              关闭
            </Button>
          </Space>

          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="Task Family">{selectedCase.task_family}</Descriptions.Item>
            <Descriptions.Item label="Split">{selectedCase.split}</Descriptions.Item>
            <Descriptions.Item label="状态">{selectedCase.status}</Descriptions.Item>
            <Descriptions.Item label="Score">{formatScore(selectedCase.score)}</Descriptions.Item>
            <Descriptions.Item label="Reference Kind">{selectedCase.reference_kind}</Descriptions.Item>
            <Descriptions.Item label="Reference 说明">
              {selectedCase.reference_summary?.source_description ||
                selectedCase.reference_summary?.label_source ||
                "—"}
            </Descriptions.Item>
          </Descriptions>

          {hardGateLabels(selectedCase.hard_gate_failures).length > 0 && (
            <Alert
              style={{ marginTop: 12 }}
              type="error"
              showIcon
              data-testid="eval-case-hard-gates"
              message={`Hard Gate: ${hardGateLabels(selectedCase.hard_gate_failures).join(", ")}`}
            />
          )}

          <div style={{ marginTop: 12 }} data-testid="eval-case-input">
            <div className="bp-section-title">Input 摘要</div>
            <pre className="bp-code-block">{safeJsonPreview(selectedCase.input_snapshot)}</pre>
          </div>

          <div style={{ marginTop: 12 }} data-testid="eval-case-output">
            <div className="bp-section-title">系统输出（安全快照）</div>
            <pre className="bp-code-block">{safeJsonPreview(selectedCase.response_snapshot)}</pre>
          </div>

          <div style={{ marginTop: 12 }}>
            <div className="bp-section-title">指标</div>
            <Table
              rowKey={(r) => `${r.metric_name}:${r.metric_version}`}
              size="small"
              pagination={false}
              columns={metricColumns}
              dataSource={selectedCase.metric_results ?? []}
              locale={{ emptyText: "无指标" }}
            />
          </div>

          <div style={{ marginTop: 12 }} data-testid="eval-case-citations">
            <div className="bp-section-title">Citation 定位</div>
            {(selectedCase.citations ?? []).length === 0 ? (
              <Typography.Text type="secondary">无 citation</Typography.Text>
            ) : (
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {(selectedCase.citations ?? []).map((c, idx) => {
                  const v = validateEvaluationCitation(projectId, c);
                  return (
                    <li key={idx} data-testid={`eval-citation-${idx}`}>
                      {v.valid && v.href ? (
                        <Link to={v.href}>{v.label}</Link>
                      ) : (
                        <Typography.Text type="danger" className="bp-citation-invalid">
                          {v.label} — {v.error}
                        </Typography.Text>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {selectedCase.agent_run_id && (
            <div style={{ marginTop: 12 }}>
              <Link
                to={agentRunHref(projectId, selectedCase.agent_run_id)}
                data-testid="eval-agent-run-link"
              >
                打开关联 AgentRun 时间线
              </Link>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
