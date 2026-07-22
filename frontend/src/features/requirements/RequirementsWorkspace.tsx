import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Drawer,
  Empty,
  Modal,
  Select,
  Skeleton,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  ExclamationCircleOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getRequirement,
  getRequirementExtractionRun,
  listRequirements,
  startRequirementExtraction,
} from "../../api/client";
import type {
  ExtractionRun,
  RequirementCategory,
  RequirementSummary,
  ReviewStatus,
  RiskLevel,
} from "../../types/api";

const EXTRACTION_DOC_TYPES = ["tender", "announcement", "amendment", "contract"] as const;

const DOC_TYPE_LABELS: Record<string, string> = {
  tender: "招标文件",
  announcement: "招标公告",
  amendment: "澄清/补遗",
  contract: "合同",
};

const CATEGORY_LABELS: Record<RequirementCategory, string> = {
  project_info: "项目信息",
  qualification: "资质要求",
  commercial: "商务要求",
  technical: "技术要求",
  scoring: "评分办法",
  material: "投标材料",
  deadline: "时间节点",
  mandatory: "实质性要求",
  invalid_bid: "废标条款",
  contract: "合同条款",
};

const RISK_LABELS: Record<RiskLevel, string> = {
  low: "低",
  medium: "中",
  high: "高",
  critical: "极高",
};

const REVIEW_LABELS: Record<ReviewStatus, string> = {
  reviewed: "已复核",
  auto_checked: "自动检查",
  unreviewed: "待复核",
};

const CATEGORY_OPTIONS = Object.entries(CATEGORY_LABELS).map(([value, label]) => ({
  value,
  label,
}));

const RISK_OPTIONS = Object.entries(RISK_LABELS).map(([value, label]) => ({
  value,
  label,
}));

const REVIEW_OPTIONS = Object.entries(REVIEW_LABELS).map(([value, label]) => ({
  value,
  label,
}));

function formatScore(score: string | number | null | undefined): string {
  if (score == null || score === "") return "-";
  const n = typeof score === "number" ? score : Number(score);
  if (Number.isNaN(n)) return String(score);
  return n.toLocaleString("zh-CN");
}

function pageRangeLabel(start?: number | null, end?: number | null): string {
  if (start == null && end == null) return "无可靠页码";
  if (start != null && end != null && start !== end) return `第 ${start}-${end} 页`;
  return `第 ${start ?? end} 页`;
}

function evidenceQuote(
  notes: string | null | undefined,
  metadata: Record<string, unknown> | null | undefined,
): string | null {
  if (notes && notes.trim()) return notes.trim();
  const quote = metadata?.evidence_quote;
  if (typeof quote === "string" && quote.trim()) return quote.trim();
  return null;
}

function reviewTag(status: ReviewStatus) {
  if (status === "unreviewed") {
    return (
      <Tag bordered={false} color="warning">
        待复核
      </Tag>
    );
  }
  if (status === "auto_checked") {
    return (
      <Tag bordered={false} color="default">
        自动检查
      </Tag>
    );
  }
  return (
    <Tag bordered={false} color="success">
      已复核
    </Tag>
  );
}

function riskTag(level: RiskLevel) {
  const color =
    level === "critical" ? "error" : level === "high" ? "warning" : level === "medium" ? "processing" : "default";
  return (
    <Tag bordered={false} color={color}>
      {RISK_LABELS[level]}
    </Tag>
  );
}

function StatCard({ label, value, hint }: { label: string; value: number | string; hint?: string }) {
  return (
    <div className="bp-req-stat">
      <div className="bp-req-stat-label">{label}</div>
      <div className="bp-req-stat-value">{value}</div>
      {hint && <div className="bp-req-stat-hint">{hint}</div>}
    </div>
  );
}

function CounterRow({ label, value }: { label: string; value: number }) {
  return (
    <div className="bp-req-counter-row">
      <span className="bp-req-counter-label">{label}</span>
      <span className="bp-req-counter-value">{value.toLocaleString("zh-CN")}</span>
    </div>
  );
}

function ExtractionProgress({ run }: { run: ExtractionRun }) {
  const statusLabel =
    run.status === "queued" ? "排队中" : run.status === "running" ? "抽取中" : run.status;

  return (
    <div className="bp-req-progress">
      <div className="bp-req-progress-head">
        <h2 className="bp-section-title" style={{ marginBottom: 0 }}>
          需求抽取进行中
        </h2>
        <Tag bordered={false} color="processing">
          {statusLabel}
        </Tag>
      </div>
      <p className="bp-req-lead">
        正在从招标类文档切片中抽取可追溯需求条目。下方为后端实时计数，不含模拟进度百分比。
      </p>
      <div className="bp-req-counters">
        <CounterRow
          label="已处理切片"
          value={run.processed_chunks}
        />
        <CounterRow label="切片总数" value={run.total_chunks} />
        <CounterRow label="候选条目" value={run.candidate_count} />
        <CounterRow label="新建" value={run.created_count} />
        <CounterRow label="合并" value={run.merged_count} />
        <CounterRow label="冲突" value={run.conflict_count} />
        <CounterRow label="失败切片" value={run.failed_chunk_count} />
      </div>
      {run.total_chunks > 0 && (
        <div className="bp-req-chunk-hint">
          切片进度：{run.processed_chunks} / {run.total_chunks}
        </div>
      )}
    </div>
  );
}

function RequirementDetailPanel({
  projectId,
  requirementId,
  onOpenSource,
}: {
  projectId: string;
  requirementId: string;
  onOpenSource?: (documentId: string, chunkId?: string) => void;
}) {
  const detail = useQuery({
    queryKey: ["requirement", projectId, requirementId],
    queryFn: () => getRequirement(projectId, requirementId),
  });

  if (detail.isLoading) {
    return <Skeleton active paragraph={{ rows: 8 }} />;
  }

  if (detail.isError || !detail.data) {
    return (
      <Alert
        type="error"
        showIcon
        message="需求详情加载失败"
        description={(detail.error as Error)?.message || "未知错误"}
        action={
          <Button size="small" onClick={() => detail.refetch()}>
            重试
          </Button>
        }
      />
    );
  }

  const req = detail.data;
  const meta = req.metadata_json ?? undefined;

  return (
    <div className="bp-req-detail">
      <div className="bp-req-detail-title-row">
        <Typography.Title level={4} style={{ margin: 0, color: "var(--bp-text)" }}>
          {req.title}
        </Typography.Title>
        {req.has_conflict && (
          <Tag bordered={false} color="error">
            需人工确认
          </Tag>
        )}
        {reviewTag(req.review_status)}
      </div>

      <div className="bp-meta-grid" style={{ marginTop: 16 }}>
        <div className="bp-meta-item">
          <div className="bp-meta-label">类别</div>
          <div className="bp-meta-value">{CATEGORY_LABELS[req.category] ?? req.category}</div>
        </div>
        <div className="bp-meta-item">
          <div className="bp-meta-label">强制性</div>
          <div className="bp-meta-value">{req.mandatory ? "强制" : "非强制"}</div>
        </div>
        <div className="bp-meta-item">
          <div className="bp-meta-label">风险</div>
          <div className="bp-meta-value">{riskTag(req.risk_level)}</div>
        </div>
        <div className="bp-meta-item">
          <div className="bp-meta-label">分值</div>
          <div className="bp-meta-value">{formatScore(req.score)}</div>
        </div>
        <div className="bp-meta-item">
          <div className="bp-meta-label">质量等级</div>
          <div className="bp-meta-value">{req.quality_level}</div>
        </div>
        <div className="bp-meta-item">
          <div className="bp-meta-label">来源文件</div>
          <div className="bp-meta-value">{req.source_document_file_name || "-"}</div>
        </div>
        <div className="bp-meta-item">
          <div className="bp-meta-label">章节</div>
          <div className="bp-meta-value">{req.source_section || "-"}</div>
        </div>
        <div className="bp-meta-item">
          <div className="bp-meta-label">条款</div>
          <div className="bp-meta-value">{req.source_clause_id || "-"}</div>
        </div>
      </div>

      <h3 className="bp-req-subhead">规范化表述</h3>
      <div className="bp-req-quote-block">
        {req.normalized_requirement || "（无规范化表述）"}
      </div>

      {typeof meta?.conflict_note === "string" && meta.conflict_note && (
        <>
          <h3 className="bp-req-subhead">冲突说明</h3>
          <Alert type="warning" showIcon message={meta.conflict_note} />
        </>
      )}

      <h3 className="bp-req-subhead">证据引用（{req.evidence_links.length}）</h3>
      {req.evidence_links.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无证据链接" />
      ) : (
        <div className="bp-req-evidence-list">
          {req.evidence_links.map((link) => {
            const quote = evidenceQuote(link.notes, meta);
            return (
              <article key={link.id} className="bp-req-evidence-card">
                <div className="bp-req-evidence-head">
                  <span className="bp-req-evidence-file">
                    {link.document_file_name || "未知文件"}
                  </span>
                  {link.document_type && (
                    <Tag bordered={false}>
                      {DOC_TYPE_LABELS[link.document_type] ?? link.document_type}
                    </Tag>
                  )}
                </div>
                <div className="bp-req-evidence-meta">
                  {link.section && <span>章节 {link.section}</span>}
                  {link.clause_id && <span>条款 {link.clause_id}</span>}
                  <span>{pageRangeLabel(link.page_start, link.page_end)}</span>
                </div>
                {quote && <blockquote className="bp-req-evidence-quote">{quote}</blockquote>}
                {onOpenSource && link.document_id && (
                  <Button
                    type="link"
                    size="small"
                    className="bp-req-open-source"
                    onClick={() => onOpenSource(link.document_id!, link.chunk_id ?? undefined)}
                  >
                    在文档中心打开
                  </Button>
                )}
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}

type Filters = {
  category?: RequirementCategory;
  mandatory?: boolean;
  risk_level?: RiskLevel;
  review_status?: ReviewStatus;
  has_conflict?: boolean;
};

export default function RequirementsWorkspace({
  projectId,
  onOpenSource,
}: {
  projectId: string;
  onOpenSource?: (documentId: string, chunkId?: string) => void;
}) {
  const queryClient = useQueryClient();
  const [runId, setRunId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>({});
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const runQuery = useQuery({
    queryKey: ["requirement-extraction-run", projectId, runId],
    queryFn: () => getRequirementExtractionRun(projectId, runId!),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 2000 : false;
    },
  });

  const run = runQuery.data;
  const isExtracting = Boolean(
    runId && run && (run.status === "queued" || run.status === "running"),
  );
  const extractionFailed = Boolean(runId && run && run.status === "failed");
  const extractionSucceeded = Boolean(runId && run && run.status === "succeeded");

  const listQuery = useQuery({
    queryKey: ["requirements", projectId, filters, page, pageSize],
    queryFn: () =>
      listRequirements(projectId, {
        ...filters,
        page,
        limit: pageSize,
      }),
    enabled: !isExtracting,
  });

  const statsQueries = useQueries({
    queries: [
      {
        queryKey: ["requirements-stat", projectId, "total"],
        queryFn: () => listRequirements(projectId, { limit: 1, page: 1 }),
        enabled: !isExtracting,
      },
      {
        queryKey: ["requirements-stat", projectId, "mandatory"],
        queryFn: () => listRequirements(projectId, { mandatory: true, limit: 1, page: 1 }),
        enabled: !isExtracting,
      },
      {
        queryKey: ["requirements-stat", projectId, "high"],
        queryFn: () => listRequirements(projectId, { risk_level: "high", limit: 1, page: 1 }),
        enabled: !isExtracting,
      },
      {
        queryKey: ["requirements-stat", projectId, "critical"],
        queryFn: () => listRequirements(projectId, { risk_level: "critical", limit: 1, page: 1 }),
        enabled: !isExtracting,
      },
      {
        queryKey: ["requirements-stat", projectId, "conflict"],
        queryFn: () => listRequirements(projectId, { has_conflict: true, limit: 1, page: 1 }),
        enabled: !isExtracting,
      },
      {
        queryKey: ["requirements-stat", projectId, "unreviewed"],
        queryFn: () =>
          listRequirements(projectId, { review_status: "unreviewed", limit: 1, page: 1 }),
        enabled: !isExtracting,
      },
    ],
  });

  const stats = useMemo(
    () => ({
      total: statsQueries[0]?.data?.total ?? 0,
      mandatory: statsQueries[1]?.data?.total ?? 0,
      highRisk: (statsQueries[2]?.data?.total ?? 0) + (statsQueries[3]?.data?.total ?? 0),
      conflicts: statsQueries[4]?.data?.total ?? 0,
      unreviewed: statsQueries[5]?.data?.total ?? 0,
    }),
    [statsQueries],
  );

  const invalidateRequirements = () => {
    void queryClient.invalidateQueries({ queryKey: ["requirements", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["requirements-stat", projectId] });
  };

  useEffect(() => {
    if (!extractionSucceeded) return;
    void queryClient.invalidateQueries({ queryKey: ["requirements", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["requirements-stat", projectId] });
  }, [extractionSucceeded, projectId, queryClient]);

  const startMutation = useMutation({
    mutationFn: (force: boolean) =>
      startRequirementExtraction(projectId, {
        document_types: [...EXTRACTION_DOC_TYPES],
        force,
      }),
    onSuccess: (data) => {
      setRunId(data.id);
      setSelectedId(null);
      void queryClient.setQueryData(["requirement-extraction-run", projectId, data.id], data);
    },
  });

  const confirmForceExtract = () => {
    Modal.confirm({
      title: "强制重新抽取？",
      icon: <ExclamationCircleOutlined />,
      content:
        "将仅替换自动抽取产生的需求记录；手工录入或导入的条目不会被删除。确认后开始新一轮抽取。",
      okText: "开始强制抽取",
      cancelText: "取消",
      onOk: () => startMutation.mutateAsync(true),
    });
  };

  const columns: ColumnsType<RequirementSummary> = [
    {
      title: "类别",
      dataIndex: "category",
      key: "category",
      width: 110,
      render: (value: RequirementCategory) => CATEGORY_LABELS[value] ?? value,
    },
    {
      title: "标题",
      dataIndex: "title",
      key: "title",
      ellipsis: true,
      render: (title: string, row) => (
        <button type="button" className="bp-req-title-btn" onClick={() => setSelectedId(row.id)}>
          {title}
        </button>
      ),
    },
    {
      title: "强制",
      dataIndex: "mandatory",
      key: "mandatory",
      width: 72,
      render: (v: boolean) =>
        v ? (
          <Tag bordered={false} color="error">
            强制
          </Tag>
        ) : (
          <span className="bp-text-faint">否</span>
        ),
    },
    {
      title: "风险",
      dataIndex: "risk_level",
      key: "risk_level",
      width: 80,
      render: (v: RiskLevel) => riskTag(v),
    },
    {
      title: "复核状态",
      dataIndex: "review_status",
      key: "review_status",
      width: 100,
      render: (v: ReviewStatus) => reviewTag(v),
    },
    {
      title: "冲突",
      dataIndex: "has_conflict",
      key: "has_conflict",
      width: 110,
      render: (v: boolean) =>
        v ? (
          <Tag bordered={false} color="error">
            需人工确认
          </Tag>
        ) : (
          "-"
        ),
    },
    {
      title: "来源",
      dataIndex: "source_document_file_name",
      key: "source",
      ellipsis: true,
      width: 160,
      render: (v: string | null | undefined) => v || "-",
    },
    {
      title: "证据",
      dataIndex: "evidence_count",
      key: "evidence_count",
      width: 64,
      align: "right",
    },
  ];

  const showEmpty =
    !isExtracting &&
    !extractionFailed &&
    !listQuery.isLoading &&
    (listQuery.data?.total ?? 0) === 0 &&
    Object.keys(filters).length === 0 &&
    !extractionSucceeded;

  if (isExtracting && run) {
    return <ExtractionProgress run={run} />;
  }

  if (extractionFailed && run) {
    return (
      <div className="bp-req-failed">
        <Alert
          type="error"
          showIcon
          message="需求抽取失败"
          description={run.error_summary || "抽取任务失败，未返回详细原因。"}
        />
        <div className="bp-req-failed-actions">
          <Button
            type="primary"
            icon={<ReloadOutlined />}
            loading={startMutation.isPending}
            onClick={() => startMutation.mutate(false)}
          >
            重试抽取
          </Button>
          <Button onClick={() => setRunId(null)}>返回清单</Button>
        </div>
      </div>
    );
  }

  if (listQuery.isLoading && !listQuery.data) {
    return (
      <div className="bp-panel">
        <Skeleton active paragraph={{ rows: 8 }} />
      </div>
    );
  }

  if (listQuery.isError && !listQuery.data) {
    return (
      <Alert
        type="error"
        showIcon
        message="需求清单加载失败"
        description={(listQuery.error as Error).message}
        action={
          <Button size="small" onClick={() => listQuery.refetch()}>
            重试
          </Button>
        }
      />
    );
  }

  if (showEmpty) {
    return (
      <div className="bp-req-empty">
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <div>
              <div className="bp-pending-capability-title">尚未抽取需求清单</div>
              <div className="bp-pending-capability-desc">
                从招标类文档中抽取可追溯的需求条目（含证据定位）。默认范围：
                {EXTRACTION_DOC_TYPES.map((t) => DOC_TYPE_LABELS[t]).join("、")}
                。公司资质/业绩等材料不在抽取范围内。
              </div>
            </div>
          }
        >
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            loading={startMutation.isPending}
            onClick={() => startMutation.mutate(false)}
          >
            开始抽取
          </Button>
        </Empty>
        {startMutation.isError && (
          <Alert
            style={{ marginTop: 16, textAlign: "left" }}
            type="error"
            showIcon
            message="启动抽取失败"
            description={(startMutation.error as Error).message}
          />
        )}
      </div>
    );
  }

  return (
    <div className="bp-req-workspace">
      <div className="bp-req-toolbar">
        <div>
          <h2 className="bp-section-title" style={{ marginBottom: 4 }}>
            需求清单
          </h2>
          <p className="bp-req-lead" style={{ marginBottom: 0 }}>
            可追溯招标需求条目。待复核项明确标注，冲突项需人工确认，不会显示为「已确认」。
          </p>
        </div>
        <Space wrap>
          <Button
            icon={<ThunderboltOutlined />}
            loading={startMutation.isPending}
            onClick={confirmForceExtract}
          >
            强制重新抽取
          </Button>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => {
              invalidateRequirements();
              void listQuery.refetch();
            }}
          >
            刷新
          </Button>
        </Space>
      </div>

      <div className="bp-req-stats">
        <StatCard label="全部" value={stats.total} />
        <StatCard label="强制性" value={stats.mandatory} />
        <StatCard label="高/极高风险" value={stats.highRisk} />
        <StatCard label="需人工确认" value={stats.conflicts} hint="存在潜在冲突" />
        <StatCard label="待复核" value={stats.unreviewed} hint="非已确认" />
      </div>

      <div className="bp-req-filters">
        <Select
          allowClear
          placeholder="类别"
          style={{ minWidth: 140 }}
          options={CATEGORY_OPTIONS}
          value={filters.category}
          onChange={(v) => {
            setPage(1);
            setFilters((f) => ({ ...f, category: v }));
          }}
        />
        <Select
          allowClear
          placeholder="强制性"
          style={{ minWidth: 110 }}
          options={[
            { value: "true", label: "强制" },
            { value: "false", label: "非强制" },
          ]}
          value={
            filters.mandatory === undefined ? undefined : filters.mandatory ? "true" : "false"
          }
          onChange={(v) => {
            setPage(1);
            setFilters((f) => ({
              ...f,
              mandatory: v === undefined ? undefined : v === "true",
            }));
          }}
        />
        <Select
          allowClear
          placeholder="风险等级"
          style={{ minWidth: 110 }}
          options={RISK_OPTIONS}
          value={filters.risk_level}
          onChange={(v) => {
            setPage(1);
            setFilters((f) => ({ ...f, risk_level: v }));
          }}
        />
        <Select
          allowClear
          placeholder="复核状态"
          style={{ minWidth: 120 }}
          options={REVIEW_OPTIONS}
          value={filters.review_status}
          onChange={(v) => {
            setPage(1);
            setFilters((f) => ({ ...f, review_status: v }));
          }}
        />
        <Select
          allowClear
          placeholder="冲突"
          style={{ minWidth: 130 }}
          options={[
            { value: "true", label: "需人工确认" },
            { value: "false", label: "无冲突" },
          ]}
          value={
            filters.has_conflict === undefined
              ? undefined
              : filters.has_conflict
                ? "true"
                : "false"
          }
          onChange={(v) => {
            setPage(1);
            setFilters((f) => ({
              ...f,
              has_conflict: v === undefined ? undefined : v === "true",
            }));
          }}
        />
      </div>

      <div className="bp-req-table-wrap">
        <Table<RequirementSummary>
          rowKey="id"
          size="middle"
          columns={columns}
          dataSource={listQuery.data?.items ?? []}
          loading={listQuery.isFetching}
          scroll={{ x: 900 }}
          pagination={{
            current: page,
            pageSize,
            total: listQuery.data?.total ?? 0,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p, ps) => {
              setPage(p);
              setPageSize(ps);
            },
          }}
          onRow={(row) => ({
            onClick: () => setSelectedId(row.id),
            style: { cursor: "pointer" },
          })}
          locale={{
            emptyText: (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前筛选条件下无需求" />
            ),
          }}
        />
      </div>

      <Drawer
        title="需求详情"
        placement="right"
        width={Math.min(560, typeof window !== "undefined" ? window.innerWidth - 24 : 560)}
        open={Boolean(selectedId)}
        onClose={() => setSelectedId(null)}
        destroyOnClose
      >
        {selectedId && (
          <RequirementDetailPanel
            projectId={projectId}
            requirementId={selectedId}
            onOpenSource={onOpenSource}
          />
        )}
      </Drawer>
    </div>
  );
}
