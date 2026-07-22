import { useMemo, useState } from "react";
import {
  Button,
  Checkbox,
  Empty,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { ReloadOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { getRequirementMatchReviewQueue } from "../../api/client";
import type {
  EvidenceMatchStatus,
  MatchReviewStatus,
  RequirementCategory,
  ReviewQueueItem,
  RiskLevel,
} from "../../types/api";
import {
  buildReviewQueueParams,
  createDefaultReviewQueueFilters,
  type ReviewQueueFilterState,
} from "./reviewQueueParams";

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
  insufficient_evidence: "证据不足",
  conflicting_evidence: "材料冲突",
  not_applicable: "明确不适用",
};

const REVIEW_STATUS_LABELS: Record<MatchReviewStatus, string> = {
  pending: "待人工审核",
  confirmed: "已人工确认",
  rejected: "已人工驳回",
  needs_more_material: "待补充材料",
};

function reviewStatusTag(status: MatchReviewStatus) {
  const color =
    status === "confirmed"
      ? "success"
      : status === "rejected"
        ? "error"
        : status === "needs_more_material"
          ? "warning"
          : "processing";
  return (
    <Tag bordered={false} color={color}>
      {REVIEW_STATUS_LABELS[status]}
    </Tag>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="bp-req-stat" data-testid={`review-queue-stat-${label}`}>
      <div className="bp-req-stat-label">{label}</div>
      <div className="bp-req-stat-value">{value}</div>
    </div>
  );
}

export default function ReviewQueuePanel({
  projectId,
  onOpenMatch,
}: {
  projectId: string;
  onOpenMatch: (matchId: string) => void;
}) {
  const [filters, setFilters] = useState<ReviewQueueFilterState>(
    createDefaultReviewQueueFilters,
  );

  const params = useMemo(() => buildReviewQueueParams(filters), [filters]);

  const queueQuery = useQuery({
    queryKey: ["requirement-match-review-queue", projectId, params],
    queryFn: () => getRequirementMatchReviewQueue(projectId, params),
  });

  const counts = queueQuery.data?.counts;

  const columns: ColumnsType<ReviewQueueItem> = [
    {
      title: "需求",
      key: "title",
      ellipsis: true,
      render: (_: unknown, row) => (
        <button
          type="button"
          className="bp-req-title-btn"
          onClick={() => onOpenMatch(row.detail_id || row.id)}
        >
          {row.requirement_title || row.requirement_code || row.requirement_id}
        </button>
      ),
    },
    {
      title: "类别",
      key: "category",
      width: 100,
      render: (_: unknown, row) =>
        row.requirement_category
          ? (CATEGORY_LABELS[row.requirement_category] ?? row.requirement_category)
          : "-",
    },
    {
      title: "匹配状态",
      dataIndex: "status",
      key: "status",
      width: 120,
      render: (v: EvidenceMatchStatus) => MATCH_STATUS_LABELS[v] ?? v,
    },
    {
      title: "审核",
      dataIndex: "review_status",
      key: "review_status",
      width: 120,
      render: (v: MatchReviewStatus) => reviewStatusTag(v),
    },
    {
      title: "风险",
      dataIndex: "risk_level",
      key: "risk_level",
      width: 72,
      render: (v: RiskLevel) => RISK_LABELS[v] ?? v,
    },
    {
      title: "标记",
      key: "flags",
      width: 160,
      render: (_: unknown, row) => (
        <Space size={4} wrap>
          {row.is_review_protected && (
            <Tag bordered={false} color="purple">
              保护
            </Tag>
          )}
          {row.has_conflict && (
            <Tag bordered={false} color="error">
              冲突
            </Tag>
          )}
          {row.has_scope_exclusion && (
            <Tag bordered={false} color="default">
              范围排除
            </Tag>
          )}
          {row.lifecycle_status === "superseded" && (
            <Tag bordered={false} color="default">
              已超版
            </Tag>
          )}
        </Space>
      ),
    },
    {
      title: "最近审核",
      key: "last",
      width: 140,
      render: (_: unknown, row) =>
        row.last_reviewer || row.reviewed_by
          ? `${row.last_reviewer || row.reviewed_by}`
          : "-",
    },
  ];

  return (
    <div className="bp-review-queue" data-testid="review-queue-panel">
      <div className="bp-req-toolbar" style={{ marginBottom: 12 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>
            人工审核队列
          </Typography.Title>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            默认仅展示 active + pending。可筛选冲突/范围排除，并打开已超版历史。
          </Typography.Paragraph>
        </div>
        <Button
          icon={<ReloadOutlined />}
          onClick={() => void queueQuery.refetch()}
          loading={queueQuery.isFetching}
        >
          刷新
        </Button>
      </div>

      <div className="bp-req-stats bp-match-stats" style={{ marginBottom: 12 }}>
        <Stat label="待审核" value={counts?.pending ?? 0} />
        <Stat label="已确认" value={counts?.confirmed ?? 0} />
        <Stat label="已驳回" value={counts?.rejected ?? 0} />
        <Stat label="待补充" value={counts?.needs_more_material ?? 0} />
        <Stat label="合计" value={counts?.total ?? 0} />
      </div>

      <div className="bp-req-filters" data-testid="review-queue-filters">
        <Select
          style={{ minWidth: 140 }}
          value={filters.review_status}
          options={[
            { value: "pending", label: "待人工审核" },
            { value: "confirmed", label: "已确认" },
            { value: "rejected", label: "已驳回" },
            { value: "needs_more_material", label: "待补充材料" },
            { value: "all", label: "全部审核状态" },
          ]}
          onChange={(v) =>
            setFilters((f) => ({
              ...f,
              review_status: v as MatchReviewStatus | "all",
              page: 1,
            }))
          }
        />
        <Select
          allowClear
          placeholder="匹配状态"
          style={{ minWidth: 140 }}
          value={filters.match_status}
          options={(Object.keys(MATCH_STATUS_LABELS) as EvidenceMatchStatus[]).map(
            (value) => ({ value, label: MATCH_STATUS_LABELS[value] }),
          )}
          onChange={(v) => setFilters((f) => ({ ...f, match_status: v, page: 1 }))}
        />
        <Select
          allowClear
          placeholder="风险"
          style={{ minWidth: 100 }}
          value={filters.risk_level}
          options={(Object.keys(RISK_LABELS) as RiskLevel[]).map((value) => ({
            value,
            label: RISK_LABELS[value],
          }))}
          onChange={(v) => setFilters((f) => ({ ...f, risk_level: v, page: 1 }))}
        />
        <Select
          allowClear
          placeholder="类别"
          style={{ minWidth: 120 }}
          value={filters.requirement_category}
          options={(Object.keys(CATEGORY_LABELS) as RequirementCategory[]).map(
            (value) => ({ value, label: CATEGORY_LABELS[value] }),
          )}
          onChange={(v) =>
            setFilters((f) => ({ ...f, requirement_category: v, page: 1 }))
          }
        />
        <Select
          allowClear
          placeholder="冲突"
          style={{ minWidth: 110 }}
          value={
            filters.has_conflict === undefined
              ? undefined
              : filters.has_conflict
                ? "true"
                : "false"
          }
          options={[
            { value: "true", label: "有冲突" },
            { value: "false", label: "无冲突" },
          ]}
          onChange={(v) =>
            setFilters((f) => ({
              ...f,
              has_conflict: v === undefined ? undefined : v === "true",
              page: 1,
            }))
          }
        />
        <Select
          allowClear
          placeholder="范围排除"
          style={{ minWidth: 120 }}
          value={
            filters.has_scope_exclusion === undefined
              ? undefined
              : filters.has_scope_exclusion
                ? "true"
                : "false"
          }
          options={[
            { value: "true", label: "有范围排除" },
            { value: "false", label: "无范围排除" },
          ]}
          onChange={(v) =>
            setFilters((f) => ({
              ...f,
              has_scope_exclusion: v === undefined ? undefined : v === "true",
              page: 1,
            }))
          }
        />
        <Checkbox
          checked={filters.include_superseded}
          data-testid="review-queue-history-toggle"
          onChange={(e) =>
            setFilters((f) => ({
              ...f,
              include_superseded: e.target.checked,
              page: 1,
            }))
          }
        >
          包含已超版历史
        </Checkbox>
      </div>

      <Table<ReviewQueueItem>
        rowKey="id"
        size="middle"
        loading={queueQuery.isFetching}
        columns={columns}
        dataSource={queueQuery.data?.items ?? []}
        pagination={{
          current: filters.page,
          pageSize: filters.limit,
          total: queueQuery.data?.total ?? 0,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, ps) => setFilters((f) => ({ ...f, page: p, limit: ps })),
        }}
        onRow={(row) => ({
          onClick: () => onOpenMatch(row.detail_id || row.id),
          style: { cursor: "pointer" },
        })}
        locale={{
          emptyText: (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="当前筛选下无审核队列条目"
            />
          ),
        }}
      />
    </div>
  );
}
