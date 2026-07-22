import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Drawer,
  Empty,
  Input,
  Modal,
  Select,
  Skeleton,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
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
  cancelRequirementMatchRun,
  getRequirementMatch,
  getRequirementMatchReviewQueue,
  getRequirementMatchRun,
  listDocuments,
  listRequirementMatchReviews,
  listRequirementMatches,
  reopenRequirementMatch,
  reviewRequirementMatch,
  startRequirementMatching,
} from "../../api/client";
import { ApiError } from "../../api/http";
import type {
  CompanyEvidenceLink,
  EvidenceMatchStatus,
  MatchReview,
  MatchReviewAction,
  MatchReviewStatus,
  MatchRun,
  MatchSummary,
  RequirementCategory,
  RiskLevel,
} from "../../types/api";
import ReviewQueuePanel from "./ReviewQueuePanel";
import {
  isConflictStatus,
  validateReviewComment,
} from "./reviewQueueParams";

const ACTOR_LABEL_KEY = "bidpilot.matchReview.actorLabel";
const DEFAULT_ACTOR_LABEL = "local-reviewer";

function loadActorLabel(): string {
  try {
    return localStorage.getItem(ACTOR_LABEL_KEY) || DEFAULT_ACTOR_LABEL;
  } catch {
    return DEFAULT_ACTOR_LABEL;
  }
}

function saveActorLabel(label: string) {
  try {
    localStorage.setItem(ACTOR_LABEL_KEY, label);
  } catch {
    /* ignore */
  }
}

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
  not_applicable: "明确不适用，待人工审核",
};

const NOT_APPLICABLE_BASIS_LABELS: Record<string, string> = {
  requirement_scope_exclusion: "招标要求适用范围排除",
  project_scope_exclusion: "企业/项目范围排除",
};

const CONFLICT_DIMENSION_LABELS: Record<string, string> = {
  qualification_level: "资质等级冲突",
  certificate_validity: "证书有效性冲突",
  effective_period: "有效期冲突",
  quantity: "数量冲突",
  coverage_scope: "覆盖范围冲突",
  technical_parameter: "技术参数冲突",
  affirmative_negation: "肯定/否定冲突",
};

const COMPANY_LINK_ROLE_LABELS: Record<string, string> = {
  company_support: "支持侧证据",
  company_conflict: "冲突侧证据",
  company_scope_exclusion: "当前范围排除证据",
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

const REVIEW_STATUS_LABELS: Record<MatchReviewStatus, string> = {
  pending: "待人工审核",
  confirmed: "已人工确认",
  rejected: "已人工驳回",
  needs_more_material: "待补充材料",
};

const REVIEW_ACTION_LABELS: Record<MatchReviewAction, string> = {
  confirm: "确认可采纳",
  reject: "驳回",
  needs_more_material: "需补充材料",
  reopen: "重新开启审核",
};

const REVIEW_STATUS_OPTIONS = (Object.keys(REVIEW_STATUS_LABELS) as MatchReviewStatus[]).map(
  (value) => ({
    value,
    label: REVIEW_STATUS_LABELS[value],
  }),
);

function reviewStatusTag(status?: MatchReviewStatus) {
  const value = status ?? "pending";
  const color =
    value === "confirmed"
      ? "success"
      : value === "rejected"
        ? "error"
        : value === "needs_more_material"
          ? "warning"
          : "processing";
  return (
    <Tag bordered={false} color={color}>
      {REVIEW_STATUS_LABELS[value]}
    </Tag>
  );
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
  cancelling,
  retrying,
}: {
  run: MatchRun;
  onCancel: () => void;
  onRetry: () => void;
  cancelling: boolean;
  retrying: boolean;
}) {
  const statusLabel =
    run.status === "queued"
      ? "排队中"
      : run.status === "running"
        ? "匹配中"
        : run.status === "cancelled"
          ? "已取消"
          : run.status;

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
        <CounterRow
          label="已跳过已审核"
          value={run.skipped_reviewed_requirement_count ?? 0}
        />
        <CounterRow
          label="受保护需求"
          value={run.protected_requirement_count ?? 0}
        />
      </div>
      {run.total_requirements > 0 && (
        <div className="bp-req-chunk-hint">
          需求进度：{run.processed_requirements} / {run.total_requirements}
        </div>
      )}
      <div className="bp-req-failed-actions" style={{ marginTop: 16 }}>
        <Button icon={<StopOutlined />} loading={cancelling} onClick={onCancel}>
          取消匹配
        </Button>
        <Button icon={<ReloadOutlined />} loading={retrying} onClick={onRetry}>
          重新开始匹配
        </Button>
      </div>
    </div>
  );
}

function MatchDetailPanel({
  projectId,
  matchId,
  actorLabel,
  onActorLabelChange,
  onOpenSource,
  onReviewed,
}: {
  projectId: string;
  matchId: string;
  actorLabel: string;
  onActorLabelChange: (v: string) => void;
  onOpenSource?: (documentId: string, chunkId?: string) => void;
  onReviewed?: () => void;
}) {
  const queryClient = useQueryClient();
  const [commentModal, setCommentModal] = useState<{
    action: Exclude<MatchReviewAction, "confirm">;
  } | null>(null);
  const [commentDraft, setCommentDraft] = useState("");

  const detail = useQuery({
    queryKey: ["requirement-match", projectId, matchId],
    queryFn: () => getRequirementMatch(projectId, matchId),
  });

  const reviewsQuery = useQuery({
    queryKey: ["requirement-match-reviews", projectId, matchId],
    queryFn: () => listRequirementMatchReviews(projectId, matchId),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["requirement-match", projectId, matchId] });
    void queryClient.invalidateQueries({
      queryKey: ["requirement-match-reviews", projectId, matchId],
    });
    onReviewed?.();
  };

  const reviewMutation = useMutation({
    mutationFn: async (opts: {
      action: Exclude<MatchReviewAction, "reopen">;
      comment?: string;
    }) => {
      const lock = detail.data?.review_lock_version ?? 0;
      return reviewRequirementMatch(
        projectId,
        matchId,
        {
          action: opts.action,
          actor_label: actorLabel.trim() || DEFAULT_ACTOR_LABEL,
          comment: opts.comment,
          review_lock_version: lock,
        },
        crypto.randomUUID(),
      );
    },
    onSuccess: () => {
      saveActorLabel(actorLabel.trim() || DEFAULT_ACTOR_LABEL);
      setCommentModal(null);
      setCommentDraft("");
      message.success("审核已记录");
      invalidate();
    },
    onError: (err: unknown) => {
      const status = err instanceof ApiError ? err.status : undefined;
      if (isConflictStatus(status)) {
        message.error("审核冲突（可能已终态或版本过期），请刷新后重试");
        invalidate();
        return;
      }
      message.error(err instanceof Error ? err.message : "审核失败");
    },
  });

  const reopenMutation = useMutation({
    mutationFn: async (comment: string) => {
      const lock = detail.data?.review_lock_version ?? 0;
      return reopenRequirementMatch(
        projectId,
        matchId,
        {
          actor_label: actorLabel.trim() || DEFAULT_ACTOR_LABEL,
          comment,
          review_lock_version: lock,
        },
        crypto.randomUUID(),
      );
    },
    onSuccess: () => {
      saveActorLabel(actorLabel.trim() || DEFAULT_ACTOR_LABEL);
      setCommentModal(null);
      setCommentDraft("");
      message.success("已重新打开审核");
      invalidate();
    },
    onError: (err: unknown) => {
      const status = err instanceof ApiError ? err.status : undefined;
      if (isConflictStatus(status)) {
        message.error("无法重新打开（版本冲突或状态不允许），请刷新后重试");
        invalidate();
        return;
      }
      message.error(err instanceof Error ? err.message : "重新打开失败");
    },
  });

  const busy = reviewMutation.isPending || reopenMutation.isPending;

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
  const reviewStatus = match.review_status ?? "pending";
  const isPending = reviewStatus === "pending";
  const isTerminal = !isPending;
  const history: MatchReview[] =
    reviewsQuery.data?.items ?? match.recent_reviews ?? [];

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
  const naBasis =
    typeof matchMeta?.not_applicable_basis === "string"
      ? matchMeta.not_applicable_basis
      : null;
  const reqScopeQuote =
    typeof matchMeta?.requirement_scope_quote === "string"
      ? matchMeta.requirement_scope_quote
      : typeof matchMeta?.not_applicable_evidence_quote === "string"
        ? matchMeta.not_applicable_evidence_quote
        : null;
  const reqScopeLocation =
    matchMeta?.requirement_scope_location &&
    typeof matchMeta.requirement_scope_location === "object"
      ? (matchMeta.requirement_scope_location as Record<string, unknown>)
      : matchMeta?.not_applicable_location &&
          typeof matchMeta.not_applicable_location === "object"
        ? (matchMeta.not_applicable_location as Record<string, unknown>)
        : null;
  const currentScopeQuote =
    typeof matchMeta?.current_scope_quote === "string"
      ? matchMeta.current_scope_quote
      : null;
  const currentScopeLocation =
    matchMeta?.current_scope_location &&
    typeof matchMeta.current_scope_location === "object"
      ? (matchMeta.current_scope_location as Record<string, unknown>)
      : null;
  const naNote =
    typeof matchMeta?.not_applicable_note === "string"
      ? matchMeta.not_applicable_note
      : null;
  const conflictDimension =
    typeof matchMeta?.conflict_dimension === "string"
      ? matchMeta.conflict_dimension
      : null;
  const conflictSubject =
    typeof matchMeta?.conflict_subject === "string" ? matchMeta.conflict_subject : null;
  const primaryClaim =
    typeof matchMeta?.primary_claim_value === "string"
      ? matchMeta.primary_claim_value
      : null;
  const conflictingClaim =
    typeof matchMeta?.conflicting_claim_value === "string"
      ? matchMeta.conflicting_claim_value
      : null;

  const supportLinks = match.company_links.filter((l) => l.role === "company_support");
  const conflictLinks = match.company_links.filter((l) => l.role === "company_conflict");
  const scopeExclusionLinks = match.company_links.filter(
    (l) => l.role === "company_scope_exclusion",
  );
  const otherLinks = match.company_links.filter(
    (l) =>
      l.role !== "company_support" &&
      l.role !== "company_conflict" &&
      l.role !== "company_scope_exclusion",
  );

  const renderScopeLocation = (loc: Record<string, unknown> | null) => {
    if (!loc) return null;
    return (
      <>
        <div className="bp-meta-item">
          <div className="bp-meta-label">来源文件</div>
          <div className="bp-meta-value">
            {typeof loc.file_name === "string" ? loc.file_name : "-"}
          </div>
        </div>
        <div className="bp-meta-item">
          <div className="bp-meta-label">定位</div>
          <div className="bp-meta-value">
            {[
              typeof loc.section === "string" ? `章节 ${loc.section}` : null,
              typeof loc.clause_id === "string" ? `条款 ${loc.clause_id}` : null,
              pageRangeLabel(
                typeof loc.page_start === "number" ? loc.page_start : null,
                typeof loc.page_end === "number" ? loc.page_end : null,
              ),
            ]
              .filter(Boolean)
              .join(" · ") || "-"}
          </div>
        </div>
      </>
    );
  };

  const renderCompanyLink = (link: CompanyEvidenceLink) => (
    <article key={link.id} className="bp-req-evidence-card">
      <div className="bp-req-evidence-head">
        <span className="bp-req-evidence-file">{link.document_file_name || "未知文件"}</span>
        {link.document_type && (
          <Tag bordered={false}>{DOC_TYPE_LABELS[link.document_type] ?? link.document_type}</Tag>
        )}
        {link.role && (
          <Tag bordered={false} color={link.role === "company_conflict" ? "error" : "default"}>
            {COMPANY_LINK_ROLE_LABELS[link.role] ?? link.role}
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
        <blockquote className="bp-req-evidence-quote">{link.quote || link.notes}</blockquote>
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
  );

  return (
    <div className="bp-req-detail">
      <div className="bp-req-detail-title-row">
        <Typography.Title level={4} style={{ margin: 0, color: "var(--bp-text)" }}>
          {req?.title || "匹配详情"}
        </Typography.Title>
        {matchStatusTag(match.status)}
        {reviewStatusTag(reviewStatus)}
        {match.is_review_protected && (
          <Tag bordered={false} color="purple">
            审核保护
          </Tag>
        )}
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
          <div className="bp-meta-label">审核状态</div>
          <div className="bp-meta-value">{reviewStatusTag(reviewStatus)}</div>
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
        <div className="bp-meta-item">
          <div className="bp-meta-label">审核人</div>
          <div className="bp-meta-value">{match.reviewed_by || "-"}</div>
        </div>
      </div>

      {match.summary && (
        <>
          <h3 className="bp-req-subhead">匹配说明</h3>
          <div className="bp-req-quote-block">{match.summary}</div>
        </>
      )}

      {match.status === "not_applicable" && (
        <>
          <h3 className="bp-req-subhead">不适用依据（待人工审核）</h3>
          <div className="bp-meta-grid">
            <div className="bp-meta-item">
              <div className="bp-meta-label">依据类型</div>
              <div className="bp-meta-value">
                {naBasis ? (NOT_APPLICABLE_BASIS_LABELS[naBasis] ?? naBasis) : "-"}
              </div>
            </div>
          </div>
          {naNote && <div className="bp-req-quote-block" style={{ marginTop: 12 }}>{naNote}</div>}

          <h3 className="bp-req-subhead">招标要求范围限定</h3>
          <div className="bp-meta-grid">{renderScopeLocation(reqScopeLocation)}</div>
          {reqScopeQuote && (
            <div className="bp-req-quote-block" style={{ marginTop: 12 }}>{reqScopeQuote}</div>
          )}
          {onOpenSource &&
            typeof reqScopeLocation?.document_id === "string" &&
            reqScopeLocation.document_id && (
              <Button
                type="link"
                size="small"
                className="bp-req-open-source"
                onClick={() =>
                  onOpenSource(
                    reqScopeLocation.document_id as string,
                    typeof reqScopeLocation.chunk_id === "string"
                      ? reqScopeLocation.chunk_id
                      : undefined,
                  )
                }
              >
                在文档中心打开招标范围证据
              </Button>
            )}

          <h3 className="bp-req-subhead">当前对象范围</h3>
          <div className="bp-meta-grid">{renderScopeLocation(currentScopeLocation)}</div>
          {currentScopeQuote && (
            <div className="bp-req-quote-block" style={{ marginTop: 12 }}>{currentScopeQuote}</div>
          )}
          {scopeExclusionLinks.length > 0 && (
            <div className="bp-req-evidence-list" style={{ marginTop: 12 }}>
              {scopeExclusionLinks.map(renderCompanyLink)}
            </div>
          )}
          {onOpenSource &&
            scopeExclusionLinks.length === 0 &&
            typeof currentScopeLocation?.document_id === "string" &&
            currentScopeLocation.document_id && (
              <Button
                type="link"
                size="small"
                className="bp-req-open-source"
                onClick={() =>
                  onOpenSource(
                    currentScopeLocation.document_id as string,
                    typeof currentScopeLocation.chunk_id === "string"
                      ? currentScopeLocation.chunk_id
                      : undefined,
                  )
                }
              >
                在文档中心打开当前范围证据
              </Button>
            )}
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

      {match.status === "conflicting_evidence" ? (
        <>
          {(conflictDimension || conflictSubject || primaryClaim || conflictingClaim) && (
            <>
              <h3 className="bp-req-subhead">冲突证明</h3>
              <div className="bp-meta-grid">
                {conflictDimension && (
                  <div className="bp-meta-item">
                    <div className="bp-meta-label">冲突维度</div>
                    <div className="bp-meta-value">
                      {CONFLICT_DIMENSION_LABELS[conflictDimension] ?? conflictDimension}
                    </div>
                  </div>
                )}
                {conflictSubject && (
                  <div className="bp-meta-item">
                    <div className="bp-meta-label">冲突主体</div>
                    <div className="bp-meta-value">{conflictSubject}</div>
                  </div>
                )}
                {primaryClaim && (
                  <div className="bp-meta-item">
                    <div className="bp-meta-label">支持侧主张</div>
                    <div className="bp-meta-value">{primaryClaim}</div>
                  </div>
                )}
                {conflictingClaim && (
                  <div className="bp-meta-item">
                    <div className="bp-meta-label">冲突侧主张</div>
                    <div className="bp-meta-value">{conflictingClaim}</div>
                  </div>
                )}
              </div>
            </>
          )}
          <h3 className="bp-req-subhead">冲突支持侧证据（{supportLinks.length}）</h3>
          {supportLinks.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无支持侧企业证据" />
          ) : (
            <div className="bp-req-evidence-list">{supportLinks.map(renderCompanyLink)}</div>
          )}
          <h3 className="bp-req-subhead">冲突对立侧证据（{conflictLinks.length}）</h3>
          {conflictLinks.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无冲突侧企业证据" />
          ) : (
            <div className="bp-req-evidence-list">{conflictLinks.map(renderCompanyLink)}</div>
          )}
          {conflictNote && (
            <>
              <h3 className="bp-req-subhead">冲突说明</h3>
              <div className="bp-req-quote-block">{conflictNote}</div>
            </>
          )}
          {otherLinks.length > 0 && (
            <>
              <h3 className="bp-req-subhead">其他企业证据</h3>
              <div className="bp-req-evidence-list">{otherLinks.map(renderCompanyLink)}</div>
            </>
          )}
        </>
      ) : (
        <>
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
                  : match.status === "not_applicable"
                    ? "不适用依据见上方招标/范围证据"
                    : "暂无企业材料引用"
              }
            />
          ) : (
            <div className="bp-req-evidence-list">
              {match.company_links.map(renderCompanyLink)}
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
        </>
      )}

      <h3 className="bp-req-subhead">人工审核</h3>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="审核操作员未经验证"
        description="当前仅记录本地操作者标签（unverified_local_operator），不构成正式身份认证。"
      />
      <div style={{ marginBottom: 12 }}>
        <div className="bp-meta-label" style={{ marginBottom: 4 }}>
          操作者标签（actor_label）
        </div>
        <Input
          value={actorLabel}
          onChange={(e) => onActorLabelChange(e.target.value)}
          placeholder={DEFAULT_ACTOR_LABEL}
          maxLength={64}
          disabled={busy}
        />
      </div>
      <Space wrap style={{ marginBottom: 16 }}>
        {isPending && (
          <>
            <Button
              type="primary"
              disabled={busy || !actorLabel.trim()}
              loading={reviewMutation.isPending && reviewMutation.variables?.action === "confirm"}
              onClick={() => reviewMutation.mutate({ action: "confirm" })}
            >
              确认
            </Button>
            <Button
              danger
              disabled={busy || !actorLabel.trim()}
              onClick={() => {
                setCommentDraft("");
                setCommentModal({ action: "reject" });
              }}
            >
              驳回
            </Button>
            <Button
              disabled={busy || !actorLabel.trim()}
              onClick={() => {
                setCommentDraft("");
                setCommentModal({ action: "needs_more_material" });
              }}
            >
              需补充材料
            </Button>
          </>
        )}
        {isTerminal && (
          <Button
            disabled={busy || !actorLabel.trim()}
            onClick={() => {
              setCommentDraft("");
              setCommentModal({ action: "reopen" });
            }}
          >
            重新打开
          </Button>
        )}
      </Space>

      <h3 className="bp-req-subhead">审核历史</h3>
      {history.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无审核记录" />
      ) : (
        <div className="bp-req-evidence-list">
          {history.map((rev) => (
            <article key={rev.id} className="bp-req-evidence-card">
              <div className="bp-req-evidence-head">
                <strong>{REVIEW_ACTION_LABELS[rev.action]}</strong>
                <span className="bp-text-faint">
                  {rev.from_review_status} → {rev.to_review_status}
                </span>
              </div>
              <div className="bp-req-evidence-meta">
                <span>{rev.actor_label}</span>
                <span>{rev.actor_authn}</span>
                <span>{new Date(rev.created_at).toLocaleString("zh-CN")}</span>
              </div>
              {rev.comment && (
                <blockquote className="bp-req-evidence-quote">{rev.comment}</blockquote>
              )}
            </article>
          ))}
        </div>
      )}

      <Modal
        title={
          commentModal
            ? `${REVIEW_ACTION_LABELS[commentModal.action]}（需填写原因）`
            : "审核备注"
        }
        open={Boolean(commentModal)}
        onCancel={() => {
          if (busy) return;
          setCommentModal(null);
          setCommentDraft("");
        }}
        onOk={() => {
          if (!commentModal) return;
          if (busy) return;
          const err = validateReviewComment(commentModal.action, commentDraft);
          if (err) {
            message.warning(err);
            return;
          }
          const cleaned = commentDraft.trim().replace(/\s+/g, " ");
          if (commentModal.action === "reopen") {
            reopenMutation.mutate(cleaned);
            return;
          }
          reviewMutation.mutate({ action: commentModal.action, comment: cleaned });
        }}
        confirmLoading={busy}
        okButtonProps={{ disabled: busy }}
        cancelButtonProps={{ disabled: busy }}
        okText="提交"
        cancelText="取消"
        destroyOnClose
      >
        <Input.TextArea
          rows={4}
          maxLength={2000}
          value={commentDraft}
          disabled={busy}
          onChange={(e) => setCommentDraft(e.target.value)}
          placeholder="请说明原因（必填）"
        />
      </Modal>
    </div>
  );
}

type Filters = {
  status?: EvidenceMatchStatus;
  category?: RequirementCategory;
  mandatory?: boolean;
  risk_level?: RiskLevel;
  needs_review?: boolean;
  review_status?: MatchReviewStatus;
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
  const [actorLabel, setActorLabel] = useState(loadActorLabel);
  const [workspaceTab, setWorkspaceTab] = useState<"matches" | "review-queue">("matches");

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

  const reviewQueueQuery = useQuery({
    queryKey: ["requirement-match-review-queue", projectId],
    queryFn: () => getRequirementMatchReviewQueue(projectId, { limit: 1, page: 1 }),
    enabled: !isMatching,
  });

  const stats = useMemo(
    () => ({
      total: statsQueries[0]?.data?.total ?? 0,
      supported: statsQueries[1]?.data?.total ?? 0,
      partial: statsQueries[2]?.data?.total ?? 0,
      insufficient: statsQueries[3]?.data?.total ?? 0,
      highRisk: (statsQueries[4]?.data?.total ?? 0) + (statsQueries[5]?.data?.total ?? 0),
      needsReview: statsQueries[6]?.data?.total ?? 0,
      pendingReview: reviewQueueQuery.data?.counts.pending ?? 0,
      confirmed: reviewQueueQuery.data?.counts.confirmed ?? 0,
      rejected: reviewQueueQuery.data?.counts.rejected ?? 0,
      needsMaterial: reviewQueueQuery.data?.counts.needs_more_material ?? 0,
    }),
    [statsQueries, reviewQueueQuery.data],
  );

  const invalidateMatches = () => {
    void queryClient.invalidateQueries({ queryKey: ["requirement-matches", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["requirement-matches-stat", projectId] });
    void queryClient.invalidateQueries({
      queryKey: ["requirement-match-review-queue", projectId],
    });
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

  const cancelMutation = useMutation({
    mutationFn: () => cancelRequirementMatchRun(projectId, runId!),
    onSuccess: (data) => {
      void queryClient.setQueryData(["requirement-match-run", projectId, data.id], data);
      invalidateMatches();
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
      title: "审核状态",
      dataIndex: "review_status",
      key: "review_status",
      width: 120,
      render: (v: MatchReviewStatus | undefined, row) => (
        <Space size={4}>
          {reviewStatusTag(v)}
          {row.is_review_protected && (
            <Tag bordered={false} color="purple">
              保护
            </Tag>
          )}
        </Space>
      ),
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
    !matchingCancelled &&
    !listQuery.isLoading &&
    (listQuery.data?.total ?? 0) === 0 &&
    Object.keys(filters).length === 0 &&
    !matchingSucceeded;

  if (isMatching && run) {
    return (
      <MatchProgress
        run={run}
        onCancel={() => {
          if (runId) cancelMutation.mutate();
        }}
        onRetry={() =>
          startMutation.mutate({
            force: false,
            documentTypes: selectedDocTypes.length ? selectedDocTypes : [...MATCH_DOC_TYPES],
          })
        }
        cancelling={cancelMutation.isPending}
        retrying={startMutation.isPending}
      />
    );
  }

  if (matchingCancelled && run) {
    return (
      <div className="bp-req-failed">
        <Alert
          type="info"
          showIcon
          message="已取消"
          description={run.error_summary || "任务已取消，未写入匹配结果"}
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
            重新开始匹配
          </Button>
          <Button icon={<StopOutlined />} onClick={() => setRunId(null)}>
            返回
          </Button>
        </div>
      </div>
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
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        message="当前结果仅为企业材料与招标 Requirement 的可追溯匹配及人工审核记录，不构成自动投标结论。"
      />
      {matchingSucceeded && run && (run.skipped_reviewed_requirement_count ?? 0) > 0 && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message={`本次匹配跳过 ${run.skipped_reviewed_requirement_count} 条已审核/受保护需求（受保护 ${run.protected_requirement_count ?? 0}）。`}
        />
      )}
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

      <div className="bp-req-stats bp-match-stats" style={{ marginTop: 8 }}>
        <StatCard label="待人工审核" value={stats.pendingReview} />
        <StatCard label="已人工确认" value={stats.confirmed} />
        <StatCard label="已人工驳回" value={stats.rejected} />
        <StatCard label="待补充材料" value={stats.needsMaterial} />
      </div>

      <Tabs
        activeKey={workspaceTab}
        onChange={(key) => setWorkspaceTab(key as "matches" | "review-queue")}
        items={[
          {
            key: "matches",
            label: "匹配结果",
            children: (
              <>
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
                    placeholder="审核状态"
                    style={{ minWidth: 140 }}
                    options={REVIEW_STATUS_OPTIONS}
                    value={filters.review_status}
                    onChange={(v) => {
                      setPage(1);
                      setFilters((f) => ({ ...f, review_status: v }));
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
                      filters.mandatory === undefined
                        ? undefined
                        : filters.mandatory
                          ? "true"
                          : "false"
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
                        <Empty
                          image={Empty.PRESENTED_IMAGE_SIMPLE}
                          description="当前筛选条件下无匹配结果"
                        />
                      ),
                    }}
                  />
                </div>
              </>
            ),
          },
          {
            key: "review-queue",
            label: `人工审核队列${stats.pendingReview ? ` (${stats.pendingReview})` : ""}`,
            children: (
              <ReviewQueuePanel
                projectId={projectId}
                onOpenMatch={(id) => setSelectedId(id)}
              />
            ),
          },
        ]}
      />

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
            actorLabel={actorLabel}
            onActorLabelChange={setActorLabel}
            onOpenSource={onOpenSource}
            onReviewed={invalidateMatches}
          />
        )}
      </Drawer>
    </div>
  );
}
