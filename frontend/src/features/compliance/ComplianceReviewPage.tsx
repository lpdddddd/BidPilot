import { useMemo, useState } from "react";
import {
  Alert,
  Button,
  Empty,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { ReloadOutlined, PlayCircleOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import {
  getLatestCompliance,
  listProjects,
  startComplianceRun,
} from "../../api/client";
import { usePageTitle } from "../../components/usePageTitle";
import type { ComplianceFinding, ComplianceReport } from "../../types/api";
import {
  createDefaultComplianceFilters,
  documentCenterHref,
  evidenceSnippet,
  filterFindingsClientSide,
  locationLabel,
  type ComplianceFilters,
} from "./complianceParams";

const SEVERITY_COLOR: Record<string, string> = {
  info: "default",
  warning: "gold",
  error: "orange",
  critical: "red",
};

export default function ComplianceReviewPage() {
  usePageTitle("智能审查");
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const projectId = searchParams.get("projectId") || "";
  const [filters, setFilters] = useState<ComplianceFilters>(createDefaultComplianceFilters());

  const projectsQuery = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
    retry: 0,
  });

  const latestQuery = useQuery({
    queryKey: ["compliance-latest", projectId],
    queryFn: () => getLatestCompliance(projectId),
    enabled: Boolean(projectId),
    retry: 0,
  });

  const runMutation = useMutation({
    mutationFn: () =>
      startComplianceRun(projectId, {}, crypto.randomUUID?.() ?? `run-${Date.now()}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["compliance-latest", projectId] });
    },
  });

  const report: ComplianceReport | null | undefined = latestQuery.data;
  const findings = useMemo(
    () => filterFindingsClientSide(report?.findings ?? [], filters),
    [report, filters],
  );

  const columns: ColumnsType<ComplianceFinding> = [
    {
      title: "严重度",
      dataIndex: "severity",
      width: 90,
      render: (v: string) => <Tag color={SEVERITY_COLOR[v] || "default"}>{v}</Tag>,
    },
    {
      title: "规则",
      key: "rule",
      width: 180,
      render: (_, row) => (
        <div>
          <div>{row.rule_name}</div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {row.rule_id}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 80,
    },
    {
      title: "说明",
      dataIndex: "message",
      ellipsis: true,
    },
    {
      title: "整改建议",
      dataIndex: "remediation",
      ellipsis: true,
      render: (v: string | null | undefined) => v || "—",
    },
    {
      title: "要求",
      dataIndex: "requirement_id",
      width: 120,
      render: (v: string | null | undefined) =>
        v ? (
          <Typography.Text copyable={{ text: v }} style={{ fontSize: 12 }}>
            {v.slice(0, 8)}…
          </Typography.Text>
        ) : (
          "—"
        ),
    },
    {
      title: "证据片段",
      key: "evidence",
      width: 180,
      render: (_, row) => evidenceSnippet(row),
    },
    {
      title: "位置",
      key: "loc",
      width: 160,
      render: (_, row) => {
        const label = locationLabel(row);
        const href = projectId ? documentCenterHref(projectId, row.source_location_json) : null;
        if (href) {
          return <Link to={href}>{label}</Link>;
        }
        return label;
      },
    },
  ];

  return (
    <div className="bp-review-queue">
      <header className="bp-gallery-head">
        <div>
          <h1 className="bp-page-title">审查</h1>
          <p className="bp-page-subtitle">
            风险处理队列：基于项目真实要求、匹配与草稿数据检查；不足时标记未知，不编造结论。
          </p>
        </div>
      </header>

      <Space wrap>
        <Select
          style={{ minWidth: 280 }}
          placeholder="选择项目"
          loading={projectsQuery.isLoading}
          value={projectId || undefined}
          options={(projectsQuery.data?.items ?? []).map((p) => ({
            value: p.id,
            label: `${p.project_name} (${p.project_code})`,
          }))}
          onChange={(id: string) => {
            const next = new URLSearchParams(searchParams);
            next.set("projectId", id);
            setSearchParams(next);
          }}
          showSearch
          optionFilterProp="label"
        />
        <Button
          type="primary"
          icon={<PlayCircleOutlined />}
          disabled={!projectId}
          loading={runMutation.isPending}
          onClick={() => runMutation.mutate()}
        >
          {report ? "重新检查" : "开始检查"}
        </Button>
        <Button
          icon={<ReloadOutlined />}
          disabled={!projectId}
          loading={latestQuery.isFetching}
          onClick={() => void latestQuery.refetch()}
        >
          刷新
        </Button>
      </Space>

      {projectsQuery.isError && (
        <Alert type="error" showIcon message="无法加载项目列表" />
      )}
      {!projectId && (
        <Empty description="请选择项目以查看或运行合规检查" />
      )}
      {projectId && latestQuery.isLoading && (
        <div style={{ padding: 48, textAlign: "center" }}>
          <Spin />
        </div>
      )}
      {projectId && latestQuery.isError && (
        <Alert type="error" showIcon message="加载最近合规报告失败" />
      )}
      {projectId && runMutation.isError && (
        <Alert type="error" showIcon message="合规检查执行失败" />
      )}
      {projectId && !latestQuery.isLoading && !report && !runMutation.isPending && (
        <Empty description="尚无合规检查记录，点击「开始检查」运行规则引擎" />
      )}

      {report && (
        <>
          <div className="bp-review-summary">
            <div className="bp-review-stat">
              <span>检查项</span>
              <strong>{report.total_checks}</strong>
            </div>
            <div className="bp-review-stat">
              <span>通过</span>
              <strong>{report.passed_checks}</strong>
            </div>
            <div className="bp-review-stat">
              <span>发现项</span>
              <strong>{report.finding_count}</strong>
            </div>
            <div className="bp-review-stat">
              <span>高风险</span>
              <strong>{(report.severity_counts.critical ?? 0) + (report.severity_counts.error ?? 0)}</strong>
            </div>
          </div>

          <Space wrap>
            <Typography.Text type="secondary">
              状态：{report.run.status}
            </Typography.Text>
            <Typography.Text type="secondary">
              开始：{report.run.started_at || "—"}
            </Typography.Text>
            <Typography.Text type="secondary">
              结束：{report.run.finished_at || "—"}
            </Typography.Text>
          </Space>

          <Space wrap>
            <Select
              style={{ width: 160 }}
              value={filters.severity ?? "all"}
              options={[
                { value: "all", label: "全部严重度" },
                { value: "critical", label: "critical" },
                { value: "error", label: "error" },
                { value: "warning", label: "warning" },
                { value: "info", label: "info" },
              ]}
              onChange={(severity) => setFilters((f) => ({ ...f, severity: severity }))}
            />
            <Select
              style={{ width: 200 }}
              value={filters.category ?? "all"}
              options={[
                { value: "all", label: "全部分类" },
                { value: "coverage", label: "coverage" },
                { value: "evidence", label: "evidence" },
                { value: "qualification_risk", label: "qualification_risk" },
                { value: "draft_safety", label: "draft_safety" },
                { value: "consistency", label: "consistency" },
                { value: "engine", label: "engine" },
              ]}
              onChange={(category) => setFilters((f) => ({ ...f, category }))}
            />
          </Space>

          <Table
            rowKey={(r) => r.finding_id}
            columns={columns}
            dataSource={findings}
            pagination={{ pageSize: 20, showSizeChanger: true }}
            locale={{ emptyText: "当前筛选下无发现项" }}
            size="middle"
          />
        </>
      )}
    </div>
  );
}
