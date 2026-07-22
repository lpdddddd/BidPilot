import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
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
  StopOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getRequirementMatch,
  getRequirementMatchRun,
  listDocuments,
  listRequirementMatches,
  startRequirementMatching,
} from "../../api/client";
import type {
  EvidenceMatchStatus,
  MatchRun,
  MatchSummary,
  RequirementCategory,
  RiskLevel,
} from "../../types/api";

const MATCH_DOC_TYPES = [
  "company_profile",
  "qualification",
  "case",
  "personnel",
  "product",
] as const;

const DOC_TYPE_LABELS: Record<string, string> = {
  tender: "招标文件",
  announcement: "招标公告",
  amendment: "澄清/补遗",
  contract: "合同",
  company_profile: "企业资料",
  qualification: "资质文件",
  case: "业绩案例",
  personnel: "人员材料",
  product: "产品资料",
  other: "其他",
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

const MATCH_STATUS_LABELS: Record<EvidenceMatchStatus, string> = {
  supported: "材料支持",
  partially_supported: "部分支持",
  insufficient_evidence: "当前材料未找到充分证据",
  conflicting_evidence: "材料冲突",
  not_applicable: "不适用",
};

const CATEGORY_OPTIONS = Object.entries(CATEGORY_LABELS).map(([value, label]) => ({
  value,
  label,
}));

const RISK_OPTIONS = Object.entries(RISK_LABELS).map(([value, label]) => ({
  value,
  label,
}));

const MATCH_STATUS_OPTIONS = (Object.keys(MATCH_STATUS_LABELS) as EvidenceMatchStatus[]).map(
  (value) => ({
    value,
    label: MATCH_STATUS_LABELS[value],
  }),
);

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

function riskTag(level: RiskLevel) {
  const color =
    level === "critical"
      ? "error"
      : level === "high"
        ? "warning"
        : level === "medium"
          ? "processing"
          : "default";
  return (
    <Tag bordered={false} color={color}>
      {RISK_LABELS[level]}
    </Tag>
  );
}

function matchStatusTag(status: EvidenceMatchStatus) {
  const color =
    status === "supported"
      ? "success"
      : status === "partially_supported"
        ? "processing"
        : status === "conflicting_evidence"
          ? "error"
          : status === "insufficient_evidence"
            ? "warning"
            : "default";
  return (
    <Tag bordered={false} color={color}>
      {MATCH_STATUS_LABELS[status]}
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

function MatchProgress({
  run,
  onCancel,
  onRetry,
  retrying,
}: {
  run: MatchRun;
  onCancel: () => void;
  onRetry: () => void;
  retrying: boolean;
}) {
  const statusLabel =
    run.status === "queued" ? "排队中" : run.status === "running" ? "匹配中" : run.status;

  return (
    <div className="bp-req-progress">
      <div className="bp-req-progress-head">
        <h2 className="bp-section-title" style={{ marginBottom: 0 }}>
          材料匹配进行中
        </h2>
        <Tag bordered={false} color="processing">
          {statusLabel}
        </Tag>
      </div>
      <p className="bp-req-lead">
        正在将企业材料与已抽取需求逐条对照。下方为后端实时计数，不含模拟进度百分比。
      </p>
      <div className="bp-req-counters">
        <CounterRow label="已处理需求" value={run.processed_requirements} />
        <CounterRow label="需求总数" value={run.total_requirements} />
        <CounterRow label="材料支持" value={run.matched_count} />
        <CounterRow label="部分支持" value={run.partial_count} />
        <CounterRow label="证据不足" value={run.missing_evidence_count} />
        <CounterRow label="材料冲突" value={run.conflict_count} />
        <CounterRow label="失败条目" value={run.failed_requirement_count} />
      </div>
      {run.total_requirements > 0 && (
        <div className="bp-req-chunk-hint">
          需求进度：{run.processed_requirements} / {run.total_requirements}
        </div>
      )}
      <div className="bp-req-failed-actions" style={{ marginTop: 16 }}>
        <Button icon={<StopOutlined />} onClick={onCancel}>
          取消关注
        </Button>
        <Button icon={<ReloadOutlined />} loading={retrying} onClick={onRetry}>
          重新开始
        </Button>
      </div>
    </div>
  );
}

function MatchDetailPanel({
  projectId,
  matchId,
  onOpenSource,
}: {
  projectId: string;
  matchId: string;
  onOpenSource?: (documentId: string, chunkId?: string) => void;
}) {
  const detail = useQuery({
    queryKey: ["requirement-match", projectId, matchId],
    queryFn: () => getRequirementMatch(projectId, matchId),
  });

  if (detail.isLoading) {
    return <Skeleton active paragraph={{ rows: 8 }} />;
  }

  if (detail.isError || !detail.data) {
    return (
      <Alert
        type="error"
        showIcon
        message="匹配详情加载失败"
        description={(detail.error as Error)?.message || "未知错误"}
        action={
          <Button size="small" onClick={() => detail.refetch()}>
            重试
          </Button>
        }
      />
    );
  }

  const match = detail.data;
  const req = match.requirement;
  const reqMeta = req?.metadata_json ?? undefined;
  const matchMeta = match.metadata_json ?? undefined;
  const hasPotentialConflict =
    Boolean(req?.has_conflict) ||
    Boolean(reqMeta?.potential_conflict) ||
    Boolean(matchMeta?.requirement_potential_conflict);
  const conflictNote =
    (typeof matchMeta?.conflict_note === "string" && matchMeta.conflict_note) ||
    (typeof reqMeta?.conflict_note === "string" && reqMeta.conflict_note) ||
    null;

  return (
    <div className="bp-req-detail">
      <div className="bp-req-detail-title-row">
        <Typography.Title level={4} style={{ margin: 0, color: "var(--bp-text)" }}>
          {req?.title || "匹配详情"}
        </Typography.Title>
        {matchStatusTag(match.status)}
        {match.needs_review && (
          <Tag bordered={false} color="warning">
            待人工审核
          </Tag>
        )}
        {hasPotentialConflict && (
          <Tag bordered={false} color="error">
            需求潜在冲突
          </Tag>
        )}
      </div>

      <div className="bp-meta-grid" style={{ marginTop: 16 }}>
        <div className="bp-meta-item">
          <div className="bp-meta-label">匹配状态</div>
          <div className="bp-meta-value">{matchStatusTag(match.status)}</div>
        </div>
        <div className="bp-meta-item">
          <div className="bp-meta-label">自动风险</div>
          <div className="bp-meta-value">{riskTag(match.risk_level)}</div>
        </div>
        <div className="bp-meta-item">
          <div className="bp-meta-label">类别</div>
          <div className="bp-meta-value">
            {(() => {
              const cat = match.requirement_category ?? req?.category;
              return cat ? (CATEGORY_LABELS[cat] ?? cat) : "-";
            })()}
          </div>
        </div>
        <div className="bp-meta-item">
          <div className="bp-meta-label">强制性</div>
          <div className="bp-meta-value">
            {(match.requirement_mandatory ?? req?.mandatory) ? "强制" : "非强制"}
          </div>
        </div>
        <div className="bp-meta-item">
          <div className="bp-meta-label">企业来源</div>
          <div className="bp-meta-value">{match.primary_company_document_file_name || "-"}</div>
        </div>
        <div className="bp-meta-item">
          <div className="bp-meta-label">材料类型</div>
          <div className="bp-meta-value">
            {match.primary_company_document_type
              ? (DOC_TYPE_LABELS[match.primary_company_document_type] ??
                match.primary_company_document_type)
              : "-"}
          </div>
        </div>
      </div>

      {match.summary && (
        <>
          <h3 className="bp-req-subhead">匹配说明</h3>
          <div className="bp-req-quote-block">{match.summary}</div>
        </>
      )}

      {hasPotentialConflict && (
        <>
          <h3 className="bp-req-subhead">冲突提示</h3>
          <Alert
            type="warning"
            showIcon
            message="该招标需求存在潜在冲突，匹配结果仅供参考，请人工核对。"
            description={conflictNote || undefined}
          />
        </>
      )}

      <h3 className="bp-req-subhead">招标要求</h3>
      <div className="bp-req-quote-block">
        {req?.normalized_requirement || req?.title || "（无规范化表述）"}
      </div>

      <h3 className="bp-req-subhead">招标证据（{match.tender_evidence_links.length}）</h3>
      {match.tender_evidence_links.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无招标证据链接" />
      ) : (
        <div className="bp-req-evidence-list">
          {match.tender_evidence_links.map((link) => {
            const quote = evidenceQuote(link.notes, reqMeta);
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

      <h3 className="bp-req-subhead">企业材料证据</h3>
      {match.primary_company_quote && (
        <div className="bp-req-quote-block" style={{ marginBottom: 12 }}>
          {match.primary_company_quote}
        </div>
      )}
      {match.company_links.length === 0 && !match.primary_company_quote ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            match.status === "insufficient_evidence"
              ? "当前材料未找到充分证据"
              : "暂无企业材料引用"
          }
        />
      ) : (
        <div className="bp-req-evidence-list">
          {match.company_links.map((link) => (
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
                {link.role && (
                  <Tag bordered={false} color="default">
                    {link.role}
                  </Tag>
                )}
              </div>
              <div className="bp-req-evidence-meta">
                {link.section && <span>章节 {link.section}</span>}
                {link.clause_id && <span>条款 {link.clause_id}</span>}
                {link.chunk_index != null && <span>切片 #{link.chunk_index}</span>}
                <span>{pageRangeLabel(link.page_start, link.page_end)}</span>
              </div>
              {(link.quote || link.notes) && (
                <blockquote className="bp-req-evidence-quote">
                  {link.quote || link.notes}
                </blockquote>
              )}
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
          ))}
          {match.company_links.length === 0 &&
            match.primary_company_document_id &&
            onOpenSource && (
              <Button
                type="link"
                size="small"
                className="bp-req-open-source"
                onClick={() =>
                  onOpenSource(
                    match.primary_company_document_id!,
                    match.primary_company_chunk_id ?? undefined,
                  )
                }
              >
                在文档中心打开
              </Button>
            )}
        </div>
      )}
    </div>
  );
}

type Filters = {
  status?: EvidenceMatchStatus;
  category?: RequirementCategory;
  mandatory?: boolean;
  risk_level?: RiskLevel;
  needs_review?: boolean;
  source_document_id?: string;
};

export default function MatchingWorkspace({
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
  const [selectedDocTypes, setSelectedDocTypes] = useState<string[]>([...MATCH_DOC_TYPES]);

  const runQuery = useQuery({
    queryKey: ["requirement-match-run", projectId, runId],
    queryFn: () => getRequirementMatchRun(projectId, runId!),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 2000 : false;
    },
  });

  const run = runQuery.data;
  const isMatching = Boolean(
    runId && run && (run.status === "queued" || run.status === "running"),
  );
  const matchingFailed = Boolean(runId && run && run.status === "failed");
  const matchingSucceeded = Boolean(runId && run && run.status === "succeeded");
  const matchingCancelled = Boolean(runId && run && run.status === "cancelled");

  const listQuery = useQuery({
    queryKey: ["requirement-matches", projectId, filters, page, pageSize],
    queryFn: () =>
      listRequirementMatches(projectId, {
        ...filters,
        page,
        limit: pageSize,
      }),
    enabled: !isMatching,
  });

  const documentsQuery = useQuery({
    queryKey: ["documents", projectId],
    queryFn: () => listDocuments(projectId),
    enabled: !isMatching,
  });

  const sourceDocOptions = useMemo(() => {
    const docs = documentsQuery.data?.items ?? [];
    return docs
      .filter((d) =>
        ["tender", "announcement", "amendment", "contract"].includes(d.document_type),
      )
      .map((d) => ({
        value: d.id,
        label: d.file_name,
      }));
  }, [documentsQuery.data]);

  const statsQueries = useQueries({
    queries: [
      {
        queryKey: ["requirement-matches-stat", projectId, "total"],
        queryFn: () => listRequirementMatches(projectId, { limit: 1, page: 1 }),
        enabled: !isMatching,
      },
      {
        queryKey: ["requirement-matches-stat", projectId, "supported"],
        queryFn: () =>
          listRequirementMatches(projectId, { status: "supported", limit: 1, page: 1 }),
        enabled: !isMatching,
      },
      {
        queryKey: ["requirement-matches-stat", projectId, "partial"],
        queryFn: () =>
          listRequirementMatches(projectId, {
            status: "partially_supported",
            limit: 1,
            page: 1,
          }),
        enabled: !isMatching,
      },
      {
        queryKey: ["requirement-matches-stat", projectId, "insufficient"],
        queryFn: () =>
          listRequirementMatches(projectId, {
            status: "insufficient_evidence",
            limit: 1,
            page: 1,
          }),
        enabled: !isMatching,
      },
      {
        queryKey: ["requirement-matches-stat", projectId, "high"],
        queryFn: () =>
          listRequirementMatches(projectId, { risk_level: "high", limit: 1, page: 1 }),
        enabled: !isMatching,
      },
      {
        queryKey: ["requirement-matches-stat", projectId, "critical"],
        queryFn: () =>
          listRequirementMatches(projectId, { risk_level: "critical", limit: 1, page: 1 }),
        enabled: !isMatching,
      },
      {
        queryKey: ["requirement-matches-stat", projectId, "needs_review"],
        queryFn: () =>
          listRequirementMatches(projectId, { needs_review: true, limit: 1, page: 1 }),
        enabled: !isMatching,
      },
    ],
  });

  const stats = useMemo(
    () => ({
      total: statsQueries[0]?.data?.total ?? 0,
      supported: statsQueries[1]?.data?.total ?? 0,
      partial: statsQueries[2]?.data?.total ?? 0,
      insufficient: statsQueries[3]?.data?.total ?? 0,
      highRisk: (statsQueries[4]?.data?.total ?? 0) + (statsQueries[5]?.data?.total ?? 0),
      needsReview: statsQueries[6]?.data?.total ?? 0,
    }),
    [statsQueries],
  );

  const invalidateMatches = () => {
    void queryClient.invalidateQueries({ queryKey: ["requirement-matches", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["requirement-matches-stat", projectId] });
  };

  useEffect(() => {
    if (!matchingSucceeded && !matchingCancelled) return;
    void queryClient.invalidateQueries({ queryKey: ["requirement-matches", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["requirement-matches-stat", projectId] });
  }, [matchingSucceeded, matchingCancelled, projectId, queryClient]);

  const startMutation = useMutation({
    mutationFn: (opts: { force: boolean; documentTypes?: string[] }) =>
      startRequirementMatching(projectId, {
        document_types: opts.documentTypes ?? selectedDocTypes,
        force: opts.force,
      }),
    onSuccess: (data) => {
      setRunId(data.id);
      setSelectedId(null);
      void queryClient.setQueryData(["requirement-match-run", projectId, data.id], data);
    },
  });

  const confirmForceMatch = () => {
    Modal.confirm({
      title: "强制重新匹配？",
      icon: <ExclamationCircleOutlined />,
      content:
        "将按当前所选材料类型重新对照需求清单。已有自动匹配结果可能被覆盖；不会提交投标或给出中标判断。",
      okText: "开始强制匹配",
      cancelText: "取消",
      onOk: () =>
        startMutation.mutateAsync({
          force: true,
          documentTypes: selectedDocTypes.length ? selectedDocTypes : [...MATCH_DOC_TYPES],
        }),
    });
  };

  const columns: ColumnsType<MatchSummary> = [
    {
      title: "需求",
      key: "requirement",
      ellipsis: true,
      render: (_: unknown, row) => (
        <button type="button" className="bp-req-title-btn" onClick={() => setSelectedId(row.id)}>
          {row.requirement?.title || row.requirement_id}
        </button>
      ),
    },
    {
      title: "匹配状态",
      dataIndex: "status",
      key: "status",
      width: 160,
      render: (v: EvidenceMatchStatus) => matchStatusTag(v),
    },
    {
      title: "风险",
      dataIndex: "risk_level",
      key: "risk_level",
      width: 80,
      render: (v: RiskLevel) => riskTag(v),
    },
    {
      title: "企业来源",
      dataIndex: "primary_company_document_file_name",
      key: "company_source",
      ellipsis: true,
      width: 160,
      render: (v: string | null | undefined) => v || "-",
    },
    {
      title: "审核",
      dataIndex: "needs_review",
      key: "needs_review",
      width: 110,
      render: (v: boolean) =>
        v ? (
          <Tag bordered={false} color="warning">
            待人工审核
          </Tag>
        ) : (
          <span className="bp-text-faint">已标记</span>
        ),
    },
    {
      title: "类别",
      key: "category",
      width: 100,
      render: (_: unknown, row) => {
        const cat = row.requirement?.category;
        return cat ? (CATEGORY_LABELS[cat] ?? cat) : "-";
      },
    },
  ];

  const showEmpty =
    !isMatching &&
    !matchingFailed &&
    !listQuery.isLoading &&
    (listQuery.data?.total ?? 0) === 0 &&
    Object.keys(filters).length === 0 &&
    !matchingSucceeded;

  if (isMatching && run) {
    return (
      <MatchProgress
        run={run}
        onCancel={() => setRunId(null)}
        onRetry={() =>
          startMutation.mutate({
            force: false,
            documentTypes: selectedDocTypes.length ? selectedDocTypes : [...MATCH_DOC_TYPES],
          })
        }
        retrying={startMutation.isPending}
      />
    );
  }

  if (matchingFailed && run) {
    return (
      <div className="bp-req-failed">
        <Alert
          type="error"
          showIcon
          message="材料匹配失败"
          description={run.error_summary || "匹配任务失败，未返回详细原因。"}
        />
        <div className="bp-req-failed-actions">
          <Button
            type="primary"
            icon={<ReloadOutlined />}
            loading={startMutation.isPending}
            onClick={() =>
              startMutation.mutate({
                force: false,
                documentTypes: selectedDocTypes.length ? selectedDocTypes : [...MATCH_DOC_TYPES],
              })
            }
          >
            重试匹配
          </Button>
          <Button icon={<StopOutlined />} onClick={() => setRunId(null)}>
            取消
          </Button>
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
        message="匹配结果加载失败"
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
              <div className="bp-pending-capability-title">尚未进行材料匹配</div>
              <div className="bp-pending-capability-desc">
                将企业侧材料与已抽取的招标需求逐条对照，标注证据支持程度。证据不足仅表示「当前材料未找到充分证据」，不代表企业不符合。不会自动裁决、估算中标率或提交投标。
              </div>
            </div>
          }
        >
          <div className="bp-match-doc-types">
            <div className="bp-match-doc-types-label">参与匹配的企业材料类型</div>
            <Checkbox.Group
              value={selectedDocTypes}
              onChange={(values) => setSelectedDocTypes(values as string[])}
              options={MATCH_DOC_TYPES.map((t) => ({
                value: t,
                label: DOC_TYPE_LABELS[t] ?? t,
              }))}
            />
          </div>
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            loading={startMutation.isPending}
            disabled={selectedDocTypes.length === 0}
            onClick={() =>
              startMutation.mutate({
                force: false,
                documentTypes: selectedDocTypes,
              })
            }
          >
            开始匹配
          </Button>
        </Empty>
        {startMutation.isError && (
          <Alert
            style={{ marginTop: 16, textAlign: "left" }}
            type="error"
            showIcon
            message="启动匹配失败"
            description={(startMutation.error as Error).message}
          />
        )}
      </div>
    );
  }

  return (
    <div className="bp-req-workspace bp-match-workspace">
      <div className="bp-req-toolbar">
        <div>
          <h2 className="bp-section-title" style={{ marginBottom: 4 }}>
            材料匹配
          </h2>
          <p className="bp-req-lead" style={{ marginBottom: 0 }}>
            对照企业材料与招标需求。待人工审核项明确标注；证据不足不表示「企业不符合」。
          </p>
        </div>
        <Space wrap>
          <Select
            mode="multiple"
            allowClear
            placeholder="材料类型"
            style={{ minWidth: 200, maxWidth: 320 }}
            options={MATCH_DOC_TYPES.map((t) => ({
              value: t,
              label: DOC_TYPE_LABELS[t] ?? t,
            }))}
            value={selectedDocTypes}
            onChange={(v) => setSelectedDocTypes(v)}
            maxTagCount="responsive"
          />
          <Button
            icon={<ThunderboltOutlined />}
            loading={startMutation.isPending}
            onClick={confirmForceMatch}
          >
            强制重新匹配
          </Button>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => {
              invalidateMatches();
              void listQuery.refetch();
            }}
          >
            刷新
          </Button>
        </Space>
      </div>

      <div className="bp-req-stats bp-match-stats">
        <StatCard label="全部" value={stats.total} />
        <StatCard label="材料支持" value={stats.supported} />
        <StatCard label="部分支持" value={stats.partial} />
        <StatCard
          label="证据不足"
          value={stats.insufficient}
          hint="当前材料未找到充分证据"
        />
        <StatCard label="高/极高风险" value={stats.highRisk} />
        <StatCard label="待人工审核" value={stats.needsReview} hint="非自动终裁" />
      </div>

      <div className="bp-req-filters">
        <Select
          allowClear
          placeholder="匹配状态"
          style={{ minWidth: 180 }}
          options={MATCH_STATUS_OPTIONS}
          value={filters.status}
          onChange={(v) => {
            setPage(1);
            setFilters((f) => ({ ...f, status: v }));
          }}
        />
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
          placeholder="审核"
          style={{ minWidth: 130 }}
          options={[
            { value: "true", label: "待人工审核" },
            { value: "false", label: "非待审核" },
          ]}
          value={
            filters.needs_review === undefined
              ? undefined
              : filters.needs_review
                ? "true"
                : "false"
          }
          onChange={(v) => {
            setPage(1);
            setFilters((f) => ({
              ...f,
              needs_review: v === undefined ? undefined : v === "true",
            }));
          }}
        />
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="来源文档"
          style={{ minWidth: 160 }}
          options={sourceDocOptions}
          value={filters.source_document_id}
          onChange={(v) => {
            setPage(1);
            setFilters((f) => ({ ...f, source_document_id: v }));
          }}
        />
      </div>

      <div className="bp-req-table-wrap">
        <Table<MatchSummary>
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
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前筛选条件下无匹配结果" />
            ),
          }}
        />
      </div>

      <Drawer
        title="匹配详情"
        placement="right"
        width={Math.min(560, typeof window !== "undefined" ? window.innerWidth - 24 : 560)}
        open={Boolean(selectedId)}
        onClose={() => setSelectedId(null)}
        destroyOnClose
      >
        {selectedId && (
          <MatchDetailPanel
            projectId={projectId}
            matchId={selectedId}
            onOpenSource={onOpenSource}
          />
        )}
      </Drawer>
    </div>
  );
}
