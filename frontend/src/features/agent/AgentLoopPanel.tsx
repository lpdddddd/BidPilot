import {
  Alert,
  Button,
  Descriptions,
  List,
  Progress,
  Space,
  Tag,
  Typography,
} from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getProposalDraft } from "../../api/client";
import {
  getAgentResult,
  getLatestAgentRun,
  resumeAgentRun,
  retryAgentRun,
  startAgentRun,
} from "../../api/agentRuns";
import {
  agentStatusLabel,
  buildAgentStartPayload,
  citationLabel,
  countFindingSeverities,
  documentCitationHref,
  formatComplianceSummary,
  formatSeverityCounts,
  primaryDraftId,
  type AgentCitation,
} from "./agentParams";
import {
  CONNECTION_LABELS,
  type ConnectionState,
  buildTimelineDisplay,
  countCompletedSteps,
  deriveCurrentNode,
  deriveCurrentTool,
  deriveProgressPercent,
  eventTypeLabel,
  formatDurationMs,
  formatElapsed,
  isTerminalRunStatus,
  shortRunId,
} from "./agentTimeline";
import { useAgentEventStream } from "./useAgentEventStream";

type Props = {
  projectId: string;
};

function asFindings(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) return [];
  return value.filter((x) => x && typeof x === "object") as Array<Record<string, unknown>>;
}

function asCitations(value: unknown): AgentCitation[] {
  if (!Array.isArray(value)) return [];
  return value.filter((x) => x && typeof x === "object") as AgentCitation[];
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((x) => String(x));
}

function connectionTagColor(state: ConnectionState): string {
  switch (state) {
    case "live":
      return "success";
    case "connecting":
    case "reconnecting":
      return "processing";
    case "polling":
      return "warning";
    case "completed":
      return "default";
    default:
      return "error";
  }
}

export default function AgentLoopPanel({ projectId }: Props) {
  const queryClient = useQueryClient();
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [reconnectToken, setReconnectToken] = useState(0);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [stickToBottom, setStickToBottom] = useState(true);
  const timelineRef = useRef<HTMLDivElement | null>(null);

  const latest = useQuery({
    queryKey: ["agent-run-latest", projectId],
    queryFn: () => getLatestAgentRun(projectId),
    enabled: Boolean(projectId),
    refetchInterval: (q) => {
      const status = q.state.data?.status;
      if (status && !isTerminalRunStatus(status) && status !== "waiting_for_user") {
        return 3000;
      }
      return false;
    },
  });

  useEffect(() => {
    if (latest.data?.id && !activeRunId) {
      setActiveRunId(latest.data.id);
    }
  }, [latest.data?.id, activeRunId]);

  const runId = activeRunId || latest.data?.id;

  const result = useQuery({
    queryKey: ["agent-run-result", projectId, runId],
    queryFn: () => getAgentResult(projectId, runId!),
    enabled: Boolean(projectId && runId),
    refetchInterval: (q) => {
      const status = q.state.data?.run?.status;
      if (status && !isTerminalRunStatus(status) && status !== "waiting_for_user") {
        return 3000;
      }
      return false;
    },
  });

  const run = result.data?.run ?? latest.data ?? null;
  const draftId = primaryDraftId(run);

  const draft = useQuery({
    queryKey: ["agent-draft", projectId, draftId],
    queryFn: () => getProposalDraft(projectId, draftId!),
    enabled: Boolean(projectId && draftId),
  });

  const stream = useAgentEventStream({
    projectId,
    runId,
    runStatus: run?.status,
    streamPath: run?.events_stream_path,
    reconnectToken,
    enabled: Boolean(projectId && runId),
    onRunStatus: (status) => {
      void queryClient.invalidateQueries({ queryKey: ["agent-run-latest", projectId] });
      void queryClient.invalidateQueries({
        queryKey: ["agent-run-result", projectId, runId],
      });
      if (isTerminalRunStatus(status)) {
        void queryClient.invalidateQueries({
          queryKey: ["agent-draft", projectId],
        });
      }
    },
  });

  useEffect(() => {
    if (isTerminalRunStatus(run?.status)) return;
    const t = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(t);
  }, [run?.status]);

  const startMut = useMutation({
    mutationFn: () =>
      startAgentRun(
        projectId,
        buildAgentStartPayload("执行招投标分析闭环"),
        `ui-${projectId}-${Date.now()}`,
      ),
    onSuccess: (data) => {
      setActiveRunId(data.id);
      setReconnectToken(0);
      setStickToBottom(true);
      void queryClient.invalidateQueries({ queryKey: ["agent-run-latest", projectId] });
    },
  });

  const resumeMut = useMutation({
    mutationFn: () => resumeAgentRun(projectId, runId!),
    onSuccess: (data) => {
      setActiveRunId(data.id);
      // Keep timeline; bump token to re-subscribe SSE
      setReconnectToken((n) => n + 1);
      void queryClient.invalidateQueries({ queryKey: ["agent-run-latest", projectId] });
      void queryClient.invalidateQueries({
        queryKey: ["agent-run-result", projectId, runId],
      });
    },
  });

  const retryMut = useMutation({
    mutationFn: () => retryAgentRun(projectId, runId!),
    onSuccess: (data) => {
      setActiveRunId(data.id);
      // Same run_id retry: keep timeline; bump token to re-subscribe SSE (like resume).
      setReconnectToken((n) => n + 1);
      setStickToBottom(true);
      void queryClient.invalidateQueries({ queryKey: ["agent-run-latest", projectId] });
      void queryClient.invalidateQueries({
        queryKey: ["agent-run-result", projectId, data.id],
      });
    },
  });

  const busy =
    startMut.isPending ||
    resumeMut.isPending ||
    retryMut.isPending ||
    run?.status === "pending" ||
    run?.status === "running";

  const warnings = asStringList(result.data?.warnings ?? run?.state?.warnings);
  const errors = asStringList(result.data?.errors ?? run?.state?.errors);
  const citations = asCitations(result.data?.citations ?? run?.state?.citations);
  const draftFindings = asFindings(run?.state?.draft_findings);
  const severityCounts = countFindingSeverities(draftFindings);
  const compliance =
    (run?.state?.compliance_summary as Record<string, unknown> | undefined) ||
    (run?.output_summary_json?.compliance_summary as Record<string, unknown> | undefined);

  const draftBody =
    draft.data?.current_version?.content_markdown ||
    (typeof draft.data?.current_version?.content_json === "object" &&
    draft.data?.current_version?.content_json &&
    "text" in (draft.data.current_version.content_json as Record<string, unknown>)
      ? String((draft.data.current_version.content_json as Record<string, unknown>).text || "")
      : "");

  const failingFindings = draftFindings.filter(
    (f) =>
      String(f.status) === "fail" &&
      ["error", "critical"].includes(String(f.severity || "").toLowerCase()),
  );
  const warningFindings = draftFindings.filter(
    (f) =>
      String(f.status) === "fail" &&
      String(f.severity || "").toLowerCase() === "warning",
  );

  const currentNode = deriveCurrentNode(stream.events, run?.current_node);
  const currentTool = deriveCurrentTool(stream.events);
  const completedSteps = countCompletedSteps(stream.events);
  const progress = deriveProgressPercent(stream.events);
  const displayItems = useMemo(
    () => buildTimelineDisplay(stream.events),
    [stream.events],
  );

  const onTimelineScroll = useCallback(() => {
    const el = timelineRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    setStickToBottom(distance < 48);
  }, []);

  useEffect(() => {
    if (!stickToBottom) return;
    const el = timelineRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [stream.events, stickToBottom, displayItems]);

  function refresh() {
    void latest.refetch();
    if (runId) {
      void queryClient.invalidateQueries({
        queryKey: ["agent-run-result", projectId, runId],
      });
    }
    if (draftId) {
      void queryClient.invalidateQueries({
        queryKey: ["agent-draft", projectId, draftId],
      });
    }
  }

  const showRetry =
    run &&
    (run.status === "failed" ||
      run.status === "cancelled" ||
      run.status === "blocked");

  return (
    <div data-testid="agent-loop-panel">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 16 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>
            Agent 闭环
          </Typography.Title>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            LangGraph 编排检索 → 抽取 → 匹配 → 合规 → 草稿；下方为实时执行时间线。
          </Typography.Paragraph>
        </div>
        <Space wrap>
          <Button onClick={refresh} loading={latest.isFetching} data-testid="agent-refresh">
            刷新
          </Button>
          {run?.status === "waiting_for_user" && (
            <Button
              onClick={() => resumeMut.mutate()}
              loading={resumeMut.isPending}
              disabled={busy && !resumeMut.isPending}
              data-testid="agent-resume"
            >
              恢复
            </Button>
          )}
          {showRetry && (
            <Button
              onClick={() => retryMut.mutate()}
              loading={retryMut.isPending}
              disabled={busy && !retryMut.isPending}
              data-testid="agent-retry"
            >
              重试
            </Button>
          )}
          <Button
            type="primary"
            onClick={() => startMut.mutate()}
            loading={startMut.isPending}
            disabled={busy && !startMut.isPending}
            data-testid="agent-start"
          >
            开始闭环
          </Button>
        </Space>
      </div>

      {latest.isError && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message="加载最近 Agent 运行失败"
          description={(latest.error as Error)?.message}
          data-testid="agent-latest-error"
        />
      )}
      {startMut.isError && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message="启动 Agent 失败"
          description={(startMut.error as Error)?.message}
          data-testid="agent-start-error"
        />
      )}
      {resumeMut.isError && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message="恢复 Agent 失败"
          description={(resumeMut.error as Error)?.message}
        />
      )}
      {retryMut.isError && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message="重试 Agent 失败"
          description={(retryMut.error as Error)?.message}
        />
      )}
      {result.isError && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message="加载 Agent 结果失败"
          description={(result.error as Error)?.message}
        />
      )}
      {stream.error && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="事件流异常"
          description={stream.error}
          data-testid="agent-stream-error"
        />
      )}

      {!run && !latest.isLoading && (
        <Alert
          type="info"
          showIcon
          message="尚无 Agent 运行记录，点击「开始闭环」执行。"
          data-testid="agent-empty"
        />
      )}

      {run && (
        <div
          data-testid="agent-run-status-bar"
          style={{
            marginBottom: 16,
            padding: 12,
            background: "var(--ant-color-fill-quaternary, #fafafa)",
            borderRadius: 6,
            border: "1px solid var(--ant-color-border-secondary, #f0f0f0)",
          }}
        >
          <Space wrap size={[16, 8]} style={{ width: "100%" }}>
            <span>
              状态{" "}
              <Tag data-testid="agent-status">{agentStatusLabel(run.status)}</Tag>
            </span>
            <span data-testid="agent-run-id">
              运行 <Typography.Text code>{shortRunId(run.id)}</Typography.Text>
            </span>
            <span data-testid="agent-started-at">
              开始{" "}
              {run.started_at
                ? new Date(run.started_at).toLocaleString("zh-CN")
                : "—"}
            </span>
            <span data-testid="agent-elapsed">
              耗时 {formatElapsed(run.started_at, run.finished_at, nowMs)}
            </span>
            <span data-testid="agent-current-node">当前节点 {currentNode}</span>
            <span data-testid="agent-current-tool">当前工具 {currentTool}</span>
            <span data-testid="agent-completed-steps">已完成步骤 {completedSteps}</span>
            <span>
              连接{" "}
              <Tag
                color={connectionTagColor(stream.connection)}
                data-testid="agent-connection"
              >
                {CONNECTION_LABELS[stream.connection]}
              </Tag>
            </span>
          </Space>
          <div style={{ marginTop: 8, maxWidth: 360 }} data-testid="agent-progress">
            <Progress percent={progress} size="small" status={
              run.status === "failed"
                ? "exception"
                : isTerminalRunStatus(run.status)
                  ? "success"
                  : "active"
            } />
          </div>
        </div>
      )}

      {run && (
        <div style={{ marginBottom: 16 }} data-testid="agent-timeline-section">
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 8,
            }}
          >
            <Typography.Title level={5} style={{ margin: 0 }}>
              执行时间线
            </Typography.Title>
            {!stickToBottom && (
              <Button
                size="small"
                type="link"
                data-testid="agent-scroll-latest"
                onClick={() => {
                  setStickToBottom(true);
                  const el = timelineRef.current;
                  if (el) el.scrollTop = el.scrollHeight;
                }}
              >
                回到最新
              </Button>
            )}
          </div>
          <div
            ref={timelineRef}
            onScroll={onTimelineScroll}
            data-testid="agent-timeline"
            style={{
              maxHeight: 320,
              overflow: "auto",
              padding: 8,
              background: "var(--ant-color-bg-container, #fff)",
              border: "1px solid var(--ant-color-border-secondary, #f0f0f0)",
              borderRadius: 6,
            }}
          >
            {stream.events.length === 0 && (
              <Typography.Text type="secondary">
                {stream.historyLoaded ? "暂无事件" : "加载事件…"}
              </Typography.Text>
            )}
            {displayItems.map((item) => {
              if (item.kind === "event") {
                return (
                  <TimelineEventRow
                    key={`${item.event.run_id}-${item.event.sequence}`}
                    event={item.event}
                    nested={item.nested}
                  />
                );
              }
              return (
                <div
                  key={`step-${item.stepId}`}
                  data-testid={`agent-timeline-step-${item.stepId}`}
                  style={{ marginBottom: 8 }}
                >
                  {item.events.map((ev) => (
                    <TimelineEventRow
                      key={`${ev.run_id}-${ev.sequence}`}
                      event={ev}
                      nested={false}
                    />
                  ))}
                  {item.tools.map((ev) => (
                    <TimelineEventRow
                      key={`${ev.run_id}-${ev.sequence}`}
                      event={ev}
                      nested
                    />
                  ))}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {run && (
        <Descriptions bordered size="small" column={1} data-testid="agent-run-summary">
          <Descriptions.Item label="图版本">{run.graph_version || "—"}</Descriptions.Item>
          <Descriptions.Item label="合规摘要">
            {formatComplianceSummary(compliance)}
          </Descriptions.Item>
          <Descriptions.Item label="严重级别统计">
            <span data-testid="agent-severity-counts">
              {formatSeverityCounts(severityCounts)}
            </span>
          </Descriptions.Item>
          <Descriptions.Item label="草稿 ID">{draftId || "—"}</Descriptions.Item>
          <Descriptions.Item label="草稿状态">
            {draft.data?.status || "—"}
          </Descriptions.Item>
          <Descriptions.Item label="草稿版本">
            {draft.data?.current_version?.version_number != null
              ? `v${draft.data.current_version.version_number}`
              : "—"}
          </Descriptions.Item>
          {run.error_summary && (
            <Descriptions.Item label="错误摘要">
              <Typography.Text type="danger" data-testid="agent-error-summary">
                {run.error_summary}
              </Typography.Text>
            </Descriptions.Item>
          )}
        </Descriptions>
      )}

      {draftId && (
        <div style={{ marginTop: 16 }} data-testid="agent-draft-body">
          <Typography.Title level={5}>最终草稿</Typography.Title>
          {draft.isError && (
            <Alert
              type="error"
              showIcon
              message="加载草稿失败"
              description={(draft.error as Error)?.message}
            />
          )}
          {draft.isLoading && <Typography.Text type="secondary">加载草稿…</Typography.Text>}
          {!draft.isLoading && !draft.isError && (
            <Typography.Paragraph
              style={{
                whiteSpace: "pre-wrap",
                background: "var(--ant-color-fill-quaternary, #fafafa)",
                padding: 12,
                borderRadius: 4,
                maxHeight: 320,
                overflow: "auto",
              }}
            >
              {draftBody?.trim() || "（草稿正文为空）"}
            </Typography.Paragraph>
          )}
        </div>
      )}

      {(failingFindings.length > 0 || warningFindings.length > 0) && (
        <div style={{ marginTop: 16 }} data-testid="agent-draft-findings">
          <Typography.Title level={5}>草稿校验发现</Typography.Title>
          {failingFindings.length > 0 && (
            <List
              size="small"
              header={<Typography.Text type="danger">错误 / 严重</Typography.Text>}
              dataSource={failingFindings}
              renderItem={(f) => (
                <List.Item>
                  <Typography.Text type="danger">
                    [{String(f.rule_id)}] {String(f.message || "")}
                    {f.remediation ? ` — ${String(f.remediation)}` : ""}
                  </Typography.Text>
                </List.Item>
              )}
            />
          )}
          {warningFindings.length > 0 && (
            <List
              size="small"
              header={<Typography.Text type="warning">警告</Typography.Text>}
              dataSource={warningFindings}
              renderItem={(f) => (
                <List.Item>
                  <Typography.Text>
                    [{String(f.rule_id)}] {String(f.message || "")}
                  </Typography.Text>
                </List.Item>
              )}
            />
          )}
        </div>
      )}

      {(warnings.length > 0 || errors.length > 0) && (
        <div style={{ marginTop: 16 }}>
          {warnings.length > 0 && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 8 }}
              message="警告"
              description={
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              }
              data-testid="agent-warnings"
            />
          )}
          {errors.length > 0 && (
            <Alert
              type="error"
              showIcon
              message="错误"
              description={
                <ul style={{ margin: 0, paddingLeft: 18 }} data-testid="agent-errors">
                  {errors.map((e) => (
                    <li key={e}>{e}</li>
                  ))}
                </ul>
              }
            />
          )}
        </div>
      )}

      {citations.length > 0 && (
        <div style={{ marginTop: 16 }} data-testid="agent-citations">
          <Typography.Title level={5}>引用</Typography.Title>
          <List
            size="small"
            dataSource={citations}
            renderItem={(c, idx) => {
              const href = documentCitationHref(projectId, c);
              const summary = c.conclusion_summary || c.summary || "";
              return (
                <List.Item>
                  <Space direction="vertical" size={0}>
                    {href ? (
                      <Typography.Link href={href} data-testid={`agent-citation-${idx}`}>
                        {citationLabel(c)}
                      </Typography.Link>
                    ) : (
                      <Typography.Text>{citationLabel(c)}</Typography.Text>
                    )}
                    {summary ? (
                      <Typography.Text type="secondary">{summary}</Typography.Text>
                    ) : null}
                  </Space>
                </List.Item>
              );
            }}
          />
        </div>
      )}
    </div>
  );
}

function TimelineEventRow({
  event,
  nested,
}: {
  event: {
    sequence: number;
    event_type: string;
    node_name?: string | null;
    tool_name?: string | null;
    status: string;
    duration_ms?: number | null;
    safe_summary?: string | null;
    attempt?: number | null;
    timestamp?: string | null;
  };
  nested: boolean;
}) {
  const title = event.tool_name || event.node_name || event.event_type;
  return (
    <div
      data-testid={`agent-event-${event.sequence}`}
      data-event-type={event.event_type}
      data-nested={nested ? "true" : "false"}
      style={{
        padding: "6px 8px",
        marginLeft: nested ? 20 : 0,
        marginBottom: 4,
        borderLeft: nested
          ? "2px solid var(--ant-color-primary, #1677ff)"
          : "2px solid var(--ant-color-border, #d9d9d9)",
        background: nested
          ? "var(--ant-color-primary-bg, #e6f4ff)"
          : "transparent",
        fontSize: 13,
      }}
    >
      <Space size={8} wrap>
        <Typography.Text type="secondary">#{event.sequence}</Typography.Text>
        <Tag style={{ margin: 0 }}>{eventTypeLabel(event.event_type)}</Tag>
        <Typography.Text strong>{title}</Typography.Text>
        {event.duration_ms != null && (
          <Typography.Text type="secondary" data-testid={`agent-event-duration-${event.sequence}`}>
            {formatDurationMs(event.duration_ms)}
          </Typography.Text>
        )}
        {event.attempt != null && event.attempt > 1 && (
          <Typography.Text type="secondary">尝试 {event.attempt}</Typography.Text>
        )}
      </Space>
      {event.safe_summary ? (
        <div>
          <Typography.Text type="secondary">{event.safe_summary}</Typography.Text>
        </div>
      ) : null}
    </div>
  );
}
