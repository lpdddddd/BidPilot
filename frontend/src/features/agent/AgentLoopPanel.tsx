import { Alert, Button, Descriptions, List, Space, Tag, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getProposalDraft } from "../../api/client";
import {
  getAgentResult,
  getLatestAgentRun,
  resumeAgentRun,
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

export default function AgentLoopPanel({ projectId }: Props) {
  const queryClient = useQueryClient();
  const latest = useQuery({
    queryKey: ["agent-run-latest", projectId],
    queryFn: () => getLatestAgentRun(projectId),
    enabled: Boolean(projectId),
  });

  const runId = latest.data?.id;
  const result = useQuery({
    queryKey: ["agent-run-result", projectId, runId],
    queryFn: () => getAgentResult(projectId, runId!),
    enabled: Boolean(projectId && runId),
  });

  const run = result.data?.run ?? latest.data ?? null;
  const draftId = primaryDraftId(run);

  const draft = useQuery({
    queryKey: ["agent-draft", projectId, draftId],
    queryFn: () => getProposalDraft(projectId, draftId!),
    enabled: Boolean(projectId && draftId),
  });

  const startMut = useMutation({
    mutationFn: () =>
      startAgentRun(
        projectId,
        buildAgentStartPayload("执行招投标分析闭环"),
        `ui-${projectId}-${Date.now()}`,
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["agent-run-latest", projectId] });
    },
  });

  const resumeMut = useMutation({
    mutationFn: () => resumeAgentRun(projectId, runId!),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["agent-run-latest", projectId] });
      void queryClient.invalidateQueries({
        queryKey: ["agent-run-result", projectId, runId],
      });
    },
  });

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

  return (
    <div data-testid="agent-loop-panel">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 16 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>
            Agent 闭环
          </Typography.Title>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            LangGraph 编排检索 → 抽取 → 匹配 → 合规 → 草稿。
          </Typography.Paragraph>
        </div>
        <Space>
          <Button onClick={refresh} loading={latest.isFetching} data-testid="agent-refresh">
            刷新
          </Button>
          {run?.status === "waiting_for_user" && (
            <Button onClick={() => resumeMut.mutate()} loading={resumeMut.isPending}>
              恢复
            </Button>
          )}
          <Button
            type="primary"
            onClick={() => startMut.mutate()}
            loading={startMut.isPending}
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
      {result.isError && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message="加载 Agent 结果失败"
          description={(result.error as Error)?.message}
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
        <Descriptions bordered size="small" column={1} data-testid="agent-run-summary">
          <Descriptions.Item label="状态">
            <Tag data-testid="agent-status">{agentStatusLabel(run.status)}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="当前节点">{run.current_node || "—"}</Descriptions.Item>
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
