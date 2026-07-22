import { Alert, Button, Descriptions, Space, Tag, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getAgentResult,
  getLatestAgentRun,
  resumeAgentRun,
  startAgentRun,
} from "../../api/agentRuns";
import {
  agentStatusLabel,
  buildAgentStartPayload,
  formatComplianceSummary,
  primaryDraftId,
} from "./agentParams";

type Props = {
  projectId: string;
};

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

  const run = result.data?.run ?? latest.data ?? null;
  const warnings = result.data?.warnings ?? run?.state?.warnings ?? [];
  const errors = result.data?.errors ?? run?.state?.errors ?? [];
  const citations = result.data?.citations ?? run?.state?.citations ?? [];
  const draftId = primaryDraftId(run);
  const compliance =
    (run?.state?.compliance_summary as Record<string, unknown> | undefined) ||
    (run?.output_summary_json?.compliance_summary as Record<string, unknown> | undefined);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 16 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>
            Agent 闭环
          </Typography.Title>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            LangGraph 编排检索 → 抽取 → 匹配 → 合规 → 草稿（Step 10）。实时时间线见 Step 11。
          </Typography.Paragraph>
        </div>
        <Space>
          <Button onClick={() => latest.refetch()} loading={latest.isFetching}>
            刷新
          </Button>
          {run?.status === "waiting_for_user" && (
            <Button onClick={() => resumeMut.mutate()} loading={resumeMut.isPending}>
              恢复
            </Button>
          )}
          <Button type="primary" onClick={() => startMut.mutate()} loading={startMut.isPending}>
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
        />
      )}
      {startMut.isError && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message="启动 Agent 失败"
          description={(startMut.error as Error)?.message}
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

      {!run && !latest.isLoading && (
        <Alert type="info" showIcon message="尚无 Agent 运行记录，点击「开始闭环」执行。" />
      )}

      {run && (
        <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label="状态">
            <Tag>{agentStatusLabel(run.status)}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="当前节点">{run.current_node || "—"}</Descriptions.Item>
          <Descriptions.Item label="图版本">{run.graph_version || "—"}</Descriptions.Item>
          <Descriptions.Item label="合规摘要">
            {formatComplianceSummary(compliance)}
          </Descriptions.Item>
          <Descriptions.Item label="草稿">
            {draftId ? (
              <Typography.Link href={`#draft-${draftId}`}>{draftId}</Typography.Link>
            ) : (
              "—"
            )}
          </Descriptions.Item>
          <Descriptions.Item label="引用数">{citations.length}</Descriptions.Item>
          <Descriptions.Item label="警告">
            {warnings.length ? warnings.join("；") : "—"}
          </Descriptions.Item>
          <Descriptions.Item label="错误">
            {errors.length ? (
              <Typography.Text type="danger">{errors.join("；")}</Typography.Text>
            ) : (
              "—"
            )}
          </Descriptions.Item>
          {run.error_summary && (
            <Descriptions.Item label="错误摘要">
              <Typography.Text type="danger">{run.error_summary}</Typography.Text>
            </Descriptions.Item>
          )}
        </Descriptions>
      )}
    </div>
  );
}
