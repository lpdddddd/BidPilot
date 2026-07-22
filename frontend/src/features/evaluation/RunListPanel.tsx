import { Button, Dropdown, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { MenuProps } from "antd";
import type { EvaluationRun, EvaluationRunStatus } from "../../types/api";
import {
  evaluationRunStatusLabel,
  evaluationTargetLabel,
  formatDurationMs,
  formatScore,
  isActiveEvaluationStatus,
  runProgress,
  shortHash,
} from "./evaluationParams";

const STATUS_COLOR: Record<string, string> = {
  queued: "default",
  running: "processing",
  completed: "success",
  partial: "gold",
  failed: "error",
  cancelled: "default",
};

export type RunListFilters = {
  status?: string;
  target_type?: string;
  suite_id?: string;
};

type Props = {
  runs: EvaluationRun[];
  total: number;
  loading: boolean;
  page: number;
  pageSize: number;
  filters: RunListFilters;
  onFiltersChange: (next: RunListFilters) => void;
  onPageChange: (page: number, pageSize: number) => void;
  onView: (runId: string) => void;
  onCancel: (runId: string) => void;
  onResume: (runId: string) => void;
  onExport: (runId: string, format: "json" | "csv" | "markdown") => void;
  actionBusyId?: string | null;
};

export default function RunListPanel({
  runs,
  total,
  loading,
  page,
  pageSize,
  filters,
  onFiltersChange,
  onPageChange,
  onView,
  onCancel,
  onResume,
  onExport,
  actionBusyId,
}: Props) {
  const columns: ColumnsType<EvaluationRun> = [
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (s: string) => (
        <Tag color={STATUS_COLOR[s] || "default"}>{evaluationRunStatusLabel(s)}</Tag>
      ),
    },
    {
      title: "Suite",
      key: "suite",
      width: 160,
      render: (_, row) => (
        <div>
          <div>{row.suite_name || shortHash(row.suite_id, 8)}</div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            v{row.suite_version || "—"}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: "Target",
      dataIndex: "target_type",
      width: 140,
      render: (t: string) => evaluationTargetLabel(t),
    },
    {
      title: "Profile",
      dataIndex: "evaluator_version",
      width: 140,
      ellipsis: true,
    },
    {
      title: "进度",
      key: "progress",
      width: 120,
      render: (_, row) => `${row.completed_cases}/${row.total_cases} (${runProgress(row)}%)`,
    },
    {
      title: "Score",
      dataIndex: "overall_score",
      width: 90,
      render: (v: number | null | undefined) => formatScore(v),
    },
    {
      title: "P/F/E",
      key: "pfe",
      width: 100,
      render: (_, row) => `${row.passed_cases}/${row.failed_cases}/${row.error_cases}`,
    },
    {
      title: "开始",
      dataIndex: "started_at",
      width: 170,
      render: (v: string | null | undefined) => v || "—",
    },
    {
      title: "耗时",
      dataIndex: "duration_ms",
      width: 90,
      render: (v: number | null | undefined) => formatDurationMs(v),
    },
    {
      title: "Hash",
      dataIndex: "dataset_hash",
      width: 100,
      render: (v: string) => shortHash(v),
    },
    {
      title: "操作",
      key: "actions",
      width: 220,
      fixed: "right",
      render: (_, row) => {
        const exportItems: MenuProps["items"] = [
          { key: "json", label: "导出 JSON", onClick: () => onExport(row.id, "json") },
          { key: "csv", label: "导出 CSV", onClick: () => onExport(row.id, "csv") },
          { key: "markdown", label: "导出 Markdown", onClick: () => onExport(row.id, "markdown") },
        ];
        const busy = actionBusyId === row.id;
        const canResume =
          row.status === "cancelled" ||
          row.status === "partial" ||
          row.status === "failed";
        return (
          <Space size={4} wrap>
            <Button
              type="link"
              size="small"
              data-testid={`eval-run-view-${row.id}`}
              onClick={() => onView(row.id)}
            >
              查看
            </Button>
            {isActiveEvaluationStatus(row.status) && (
              <Button
                type="link"
                size="small"
                danger
                loading={busy}
                data-testid={`eval-run-cancel-${row.id}`}
                onClick={() => onCancel(row.id)}
              >
                取消
              </Button>
            )}
            {canResume && (
              <Button
                type="link"
                size="small"
                loading={busy}
                data-testid={`eval-run-resume-${row.id}`}
                onClick={() => onResume(row.id)}
              >
                恢复
              </Button>
            )}
            <Dropdown menu={{ items: exportItems }} trigger={["click"]}>
              <Button type="link" size="small" data-testid={`eval-run-export-${row.id}`}>
                导出
              </Button>
            </Dropdown>
          </Space>
        );
      },
    },
  ];

  return (
    <div data-testid="eval-run-list" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Space wrap>
        <Select
          allowClear
          style={{ width: 160 }}
          placeholder="状态筛选"
          data-testid="eval-filter-status"
          value={filters.status}
          options={(Object.keys(STATUS_COLOR) as EvaluationRunStatus[]).map((s) => ({
            value: s,
            label: evaluationRunStatusLabel(s),
          }))}
          onChange={(status) => onFiltersChange({ ...filters, status })}
        />
        <Select
          allowClear
          style={{ width: 180 }}
          placeholder="Target 筛选"
          data-testid="eval-filter-target"
          value={filters.target_type}
          options={[
            "deterministic_fake",
            "rag",
            "extraction",
            "matching",
            "compliance",
            "drafting",
            "agent_pipeline",
          ].map((t) => ({ value: t, label: evaluationTargetLabel(t) }))}
          onChange={(target_type) => onFiltersChange({ ...filters, target_type })}
        />
      </Space>

      <Table
        rowKey="id"
        size="middle"
        loading={loading}
        columns={columns}
        dataSource={runs}
        scroll={{ x: 1400 }}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          onChange: onPageChange,
        }}
        locale={{ emptyText: "暂无评测运行" }}
      />
    </div>
  );
}
