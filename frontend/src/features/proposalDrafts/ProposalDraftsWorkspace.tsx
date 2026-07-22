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
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  ExportOutlined,
  PlusOutlined,
  ReloadOutlined,
  StopOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cancelProposalDraftRun,
  createProposalDraft,
  createProposalDraftManualRevision,
  getProposalDraft,
  getProposalDraftEligibility,
  getProposalDraftRun,
  listProposalDraftVersions,
  listProposalDrafts,
  proposalDraftExportUrl,
  reopenProposalDraft,
  reviewProposalDraft,
} from "../../api/client";
import { ApiError } from "../../api/http";
import type {
  ProposalDraftDetail,
  ProposalDraftGenerationMode,
  ProposalDraftRun,
  ProposalDraftSummary,
} from "../../types/api";
import {
  DRAFT_STATUS_LABELS,
  PROPOSAL_DRAFT_DISCLAIMER,
  canExportDraft,
  canMarkReviewed,
  conflictMessage,
  isDraftReadOnly,
  selectableRequirementIds,
} from "./draftUtils";

type Props = {
  projectId: string;
  onOpenSource?: (documentId: string) => void;
};

function formatDt(value?: string | null) {
  if (!value) return "-";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString("zh-CN", { hour12: false });
}

export default function ProposalDraftsWorkspace({ projectId, onOpenSource }: Props) {
  const qc = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [activeDraftId, setActiveDraftId] = useState<string | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [title, setTitle] = useState("响应准备草稿");
  const [mode, setMode] = useState<ProposalDraftGenerationMode>("response_outline");
  const [selectedReqIds, setSelectedReqIds] = useState<string[]>([]);
  const [reviewComment, setReviewComment] = useState("");
  const [actorLabel, setActorLabel] = useState("local-reviewer");
  const [manualJson, setManualJson] = useState("");
  const [busy, setBusy] = useState(false);

  const listQuery = useQuery({
    queryKey: ["proposal-drafts", projectId],
    queryFn: () => listProposalDrafts(projectId),
  });
  const eligibilityQuery = useQuery({
    queryKey: ["proposal-draft-eligibility", projectId],
    queryFn: () => getProposalDraftEligibility(projectId),
    enabled: createOpen,
  });
  const detailQuery = useQuery({
    queryKey: ["proposal-draft", projectId, activeDraftId],
    queryFn: () => getProposalDraft(projectId, activeDraftId!),
    enabled: Boolean(activeDraftId),
  });
  const versionsQuery = useQuery({
    queryKey: ["proposal-draft-versions", projectId, activeDraftId],
    queryFn: () => listProposalDraftVersions(projectId, activeDraftId!),
    enabled: Boolean(activeDraftId),
  });
  const runQuery = useQuery({
    queryKey: ["proposal-draft-run", projectId, activeRunId],
    queryFn: () => getProposalDraftRun(projectId, activeRunId!),
    enabled: Boolean(activeRunId),
    refetchInterval: (q) => {
      const st = q.state.data?.status;
      return st === "queued" || st === "running" ? 1500 : false;
    },
  });

  useEffect(() => {
    if (!eligibilityQuery.data) return;
    const { defaultSelected } = selectableRequirementIds(eligibilityQuery.data);
    setSelectedReqIds(defaultSelected);
  }, [eligibilityQuery.data]);

  useEffect(() => {
    const run = runQuery.data;
    if (!run) return;
    if (run.status === "succeeded" && run.draft_id) {
      setActiveDraftId(run.draft_id);
      void qc.invalidateQueries({ queryKey: ["proposal-drafts", projectId] });
    }
  }, [runQuery.data, projectId, qc]);

  useEffect(() => {
    if (detailQuery.data?.current_version?.content_json) {
      setManualJson(
        JSON.stringify(detailQuery.data.current_version.content_json, null, 2),
      );
    }
  }, [detailQuery.data?.current_version?.id]);

  const createMutation = useMutation({
    mutationFn: () =>
      createProposalDraft(projectId, {
        title,
        requirement_ids: selectedReqIds,
        mode,
        created_by: actorLabel,
      }, crypto.randomUUID()),
    onSuccess: (run) => {
      setActiveRunId(run.id);
      setCreateOpen(false);
      message.success("已提交草稿生成任务");
    },
    onError: (err) => {
      message.error((err as Error).message || "创建失败");
    },
  });

  const columns: ColumnsType<ProposalDraftSummary> = useMemo(
    () => [
      { title: "标题", dataIndex: "title", key: "title" },
      {
        title: "状态",
        dataIndex: "status",
        key: "status",
        render: (s: ProposalDraftSummary["status"]) => (
          <Tag>{DRAFT_STATUS_LABELS[s] ?? s}</Tag>
        ),
      },
      {
        title: "版本",
        dataIndex: "current_version_number",
        key: "ver",
        render: (v) => (v != null ? `v${v}` : "-"),
      },
      {
        title: "可用/缺口/风险/范围",
        key: "counts",
        render: (_, row) =>
          `${row.eligible_requirement_count ?? 0}/${row.material_gap_count ?? 0}/${row.risk_count ?? 0}/${row.scope_count ?? 0}`,
      },
      {
        title: "更新时间",
        dataIndex: "updated_at",
        key: "updated_at",
        render: formatDt,
      },
      {
        title: "操作",
        key: "actions",
        render: (_, row) => (
          <Button type="link" onClick={() => setActiveDraftId(row.id)}>
            查看
          </Button>
        ),
      },
    ],
    [],
  );

  async function handleCancelRun(run: ProposalDraftRun) {
    try {
      await cancelProposalDraftRun(projectId, run.id);
      message.info("已取消生成任务");
      void runQuery.refetch();
    } catch (err) {
      message.error((err as Error).message || "取消失败");
    }
  }

  async function handleReview(draft: ProposalDraftDetail) {
    if (busy) return;
    if (!reviewComment.trim()) {
      message.warning("审核备注不能为空");
      return;
    }
    if (!canMarkReviewed(draft)) {
      message.warning("含尚未提供证据的人工内容，或状态不允许审核");
      return;
    }
    setBusy(true);
    try {
      await reviewProposalDraft(
        projectId,
        draft.id,
        {
          actor_label: actorLabel,
          comment: reviewComment.trim(),
          review_lock_version: draft.review_lock_version,
        },
        crypto.randomUUID(),
      );
      message.success("已标记复核");
      void detailQuery.refetch();
      void listQuery.refetch();
    } catch (err) {
      const status = err instanceof ApiError ? err.status : undefined;
      const conflict = conflictMessage(status);
      message.error(conflict || (err as Error).message || "审核失败");
      if (conflict) void detailQuery.refetch();
    } finally {
      setBusy(false);
    }
  }

  async function handleReopen(draft: ProposalDraftDetail) {
    if (busy) return;
    if (!reviewComment.trim()) {
      message.warning("重开原因不能为空");
      return;
    }
    setBusy(true);
    try {
      await reopenProposalDraft(
        projectId,
        draft.id,
        {
          actor_label: actorLabel,
          comment: reviewComment.trim(),
          review_lock_version: draft.review_lock_version,
        },
        crypto.randomUUID(),
      );
      message.success("已重开草稿");
      void detailQuery.refetch();
      void listQuery.refetch();
    } catch (err) {
      const status = err instanceof ApiError ? err.status : undefined;
      message.error(conflictMessage(status) || (err as Error).message || "重开失败");
      void detailQuery.refetch();
    } finally {
      setBusy(false);
    }
  }

  async function handleManualSave(draft: ProposalDraftDetail) {
    if (busy || isDraftReadOnly(draft.status)) return;
    setBusy(true);
    try {
      const parsed = JSON.parse(manualJson) as Record<string, unknown>;
      await createProposalDraftManualRevision(
        projectId,
        draft.id,
        { content_json: parsed, created_by: actorLabel },
        crypto.randomUUID(),
      );
      message.success("已保存人工修订版本");
      void detailQuery.refetch();
      void versionsQuery.refetch();
    } catch (err) {
      message.error((err as Error).message || "保存失败");
    } finally {
      setBusy(false);
    }
  }

  const draft = detailQuery.data;
  const content = draft?.current_version?.content_json as
    | {
        sections?: Array<{
          title?: string;
          blocks?: Array<Record<string, unknown>>;
        }>;
        compliance_matrix?: Array<Record<string, unknown>>;
        warnings?: Array<Record<string, unknown>>;
      }
    | undefined;

  return (
    <div>
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        message="响应准备草稿（非投标提交文件）"
        description={PROPOSAL_DRAFT_DISCLAIMER}
      />

      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          创建响应草稿
        </Button>
        <Button icon={<ReloadOutlined />} onClick={() => listQuery.refetch()}>
          刷新
        </Button>
      </Space>

      {activeRunId && runQuery.data && (
        <Alert
          style={{ marginBottom: 16 }}
          type={
            runQuery.data.status === "failed"
              ? "error"
              : runQuery.data.status === "cancelled"
                ? "warning"
                : "info"
          }
          showIcon
          message={`生成任务 ${runQuery.data.status}`}
          description={runQuery.data.error_summary || runQuery.data.title}
          action={
            runQuery.data.status === "queued" || runQuery.data.status === "running" ? (
              <Button
                size="small"
                danger
                icon={<StopOutlined />}
                onClick={() => handleCancelRun(runQuery.data!)}
              >
                取消
              </Button>
            ) : undefined
          }
        />
      )}

      {listQuery.isLoading ? (
        <Typography.Paragraph>加载中…</Typography.Paragraph>
      ) : listQuery.isError ? (
        <Alert type="error" message={(listQuery.error as Error).message} />
      ) : (listQuery.data?.items.length ?? 0) === 0 ? (
        <Empty description="暂无响应草稿" />
      ) : (
        <Table
          rowKey="id"
          columns={columns}
          dataSource={listQuery.data?.items}
          pagination={false}
        />
      )}

      <Modal
        title="创建响应准备草稿"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => createMutation.mutate()}
        confirmLoading={createMutation.isPending}
        okText="开始生成"
        width={720}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="仅使用已人工确认的可追溯证据"
          description={PROPOSAL_DRAFT_DISCLAIMER}
        />
        <Typography.Paragraph>标题</Typography.Paragraph>
        <Input value={title} onChange={(e) => setTitle(e.target.value)} />
        <Typography.Paragraph style={{ marginTop: 12 }}>模式</Typography.Paragraph>
        <Select
          style={{ width: "100%" }}
          value={mode}
          onChange={setMode}
          options={[
            { value: "response_outline", label: "响应大纲 (response_outline)" },
            {
              value: "compliance_preparation_pack",
              label: "合规准备包 (compliance_preparation_pack)",
            },
          ]}
        />
        <Typography.Paragraph style={{ marginTop: 12 }}>
          选择 Requirement（默认可用于正向内容的 confirmed 项）
        </Typography.Paragraph>
        {eligibilityQuery.isLoading ? (
          <Typography.Text>加载准入列表…</Typography.Text>
        ) : eligibilityQuery.data ? (
          <div style={{ maxHeight: 280, overflow: "auto" }}>
            <Typography.Text strong>可用于正向内容</Typography.Text>
            <Checkbox.Group
              style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 12 }}
              value={selectedReqIds}
              onChange={(vals) => setSelectedReqIds(vals as string[])}
              options={eligibilityQuery.data.eligible.map((x) => ({
                label: `${x.title} [${x.match_status}]`,
                value: x.requirement_id,
              }))}
            />
            <Typography.Text type="secondary">缺失材料 / 风险 / 范围（可纳入清单）</Typography.Text>
            <Checkbox.Group
              style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 12 }}
              value={selectedReqIds}
              onChange={(vals) => setSelectedReqIds(vals as string[])}
              options={[
                ...eligibilityQuery.data.material_gaps,
                ...eligibilityQuery.data.risks,
                ...eligibilityQuery.data.scope_items,
              ].map((x) => ({
                label: `${x.title} (${x.eligibility})`,
                value: x.requirement_id,
              }))}
            />
            <Typography.Text type="danger">排除项（pending/rejected/待补材料）</Typography.Text>
            <ul>
              {eligibilityQuery.data.excluded.map((x) => (
                <li key={x.requirement_id}>
                  {x.title} — {x.reason}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </Modal>

      <Drawer
        width={880}
        open={Boolean(activeDraftId)}
        onClose={() => setActiveDraftId(null)}
        title={draft?.title || "草稿详情"}
        extra={
          draft && canExportDraft(draft) ? (
            <Space>
              <Button
                icon={<ExportOutlined />}
                href={proposalDraftExportUrl(projectId, draft.id, "markdown")}
                target="_blank"
              >
                导出 Markdown
              </Button>
              <Button
                icon={<ExportOutlined />}
                href={proposalDraftExportUrl(projectId, draft.id, "docx")}
                target="_blank"
              >
                导出 DOCX
              </Button>
            </Space>
          ) : (
            <Typography.Text type="secondary">导出仅对已复核版本开放</Typography.Text>
          )
        }
      >
        {detailQuery.isLoading || !draft ? (
          <Typography.Paragraph>加载中…</Typography.Paragraph>
        ) : (
          <>
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              message={PROPOSAL_DRAFT_DISCLAIMER}
            />
            <Space wrap style={{ marginBottom: 12 }}>
              <Tag>{DRAFT_STATUS_LABELS[draft.status]}</Tag>
              <Tag>v{draft.current_version_number ?? "-"}</Tag>
              {draft.has_unevidenced_manual_content && (
                <Tag color="orange">含尚未提供证据的人工内容</Tag>
              )}
            </Space>

            <Typography.Title level={5}>响应正文</Typography.Title>
            {(content?.sections || []).map((section, idx) => (
              <div key={idx} style={{ marginBottom: 16 }}>
                <Typography.Title level={5}>{section.title}</Typography.Title>
                {(section.blocks || []).map((block, bidx) => (
                  <div
                    key={bidx}
                    style={{
                      borderLeft: "3px solid #1677ff",
                      paddingLeft: 12,
                      marginBottom: 12,
                    }}
                  >
                    <Tag>{String(block.block_kind)}</Tag>
                    <Typography.Paragraph>{String(block.content || "")}</Typography.Paragraph>
                    <Typography.Text type="secondary">
                      Requirement: {(block.requirement_ids as string[] | undefined)?.join(", ")}
                    </Typography.Text>
                    <div>
                      {(
                        (block.locations as Array<Record<string, unknown>> | undefined) || []
                      ).map((loc, li) => (
                        <div key={li}>
                          <Typography.Text>
                            {[
                              loc.document_file_name,
                              loc.page_start != null ? `p.${loc.page_start}` : null,
                              loc.section,
                              loc.clause_id,
                            ]
                              .filter(Boolean)
                              .join(" / ")}
                          </Typography.Text>
                          {loc.document_id ? (
                            <Button
                              type="link"
                              size="small"
                              onClick={() => onOpenSource?.(String(loc.document_id))}
                            >
                              文档中心
                            </Button>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ))}

            <Typography.Title level={5}>合规准备矩阵</Typography.Title>
            <Table
              size="small"
              pagination={false}
              rowKey={(_, i) => String(i)}
              dataSource={content?.compliance_matrix || []}
              columns={[
                { title: "Requirement", dataIndex: "requirement_id" },
                { title: "Disposition", dataIndex: "disposition" },
                {
                  title: "Citations",
                  dataIndex: "citation_ids",
                  render: (v: string[]) => (v || []).join(", "),
                },
              ]}
            />

            <Typography.Title level={5} style={{ marginTop: 16 }}>
              风险与待核验
            </Typography.Title>
            <ul>
              {(content?.warnings || []).map((w, i) => (
                <li key={i}>
                  [{String(w.warning_type)}] {String(w.requirement_id)}: {String(w.content)}
                </li>
              ))}
            </ul>

            <Typography.Title level={5}>版本历史</Typography.Title>
            <ul>
              {(versionsQuery.data?.items || []).map((v) => (
                <li key={v.id}>
                  v{v.version_number} · {v.version_kind}
                  {v.is_current ? "（当前）" : ""} · {formatDt(v.created_at)}
                  {v.has_unevidenced_manual_content ? " · 含无证据人工内容" : ""}
                </li>
              ))}
            </ul>

            <Typography.Title level={5}>来源快照</Typography.Title>
            <ul>
              {(draft.current_version?.sources || []).slice(0, 30).map((s) => (
                <li key={s.id}>
                  {s.source_role}: {s.source_quote?.slice(0, 80) || s.evidence_link_id}
                </li>
              ))}
            </ul>

            {!isDraftReadOnly(draft.status) && (
              <>
                <Typography.Title level={5}>人工修订（结构化 JSON）</Typography.Title>
                <Input.TextArea
                  rows={10}
                  value={manualJson}
                  onChange={(e) => setManualJson(e.target.value)}
                />
                <Button
                  style={{ marginTop: 8 }}
                  onClick={() => handleManualSave(draft)}
                  loading={busy}
                >
                  保存为新版本
                </Button>
              </>
            )}

            <Typography.Title level={5} style={{ marginTop: 16 }}>
              审核 / 重开
            </Typography.Title>
            <Input
              style={{ marginBottom: 8 }}
              value={actorLabel}
              onChange={(e) => setActorLabel(e.target.value)}
              placeholder="操作人标识"
            />
            <Input.TextArea
              rows={3}
              value={reviewComment}
              onChange={(e) => setReviewComment(e.target.value)}
              placeholder="备注（审核与重开均必填）"
            />
            <Space style={{ marginTop: 8 }}>
              <Button
                type="primary"
                disabled={!canMarkReviewed(draft) || busy}
                onClick={() => handleReview(draft)}
              >
                标记已复核
              </Button>
              <Button
                disabled={draft.status !== "reviewed" || busy}
                onClick={() => handleReopen(draft)}
              >
                重开
              </Button>
            </Space>
          </>
        )}
      </Drawer>
    </div>
  );
}
