import { Button, Skeleton } from "antd";
import { ArrowRightOutlined, PlusOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listModels, listProjects } from "../api/client";
import { useBackendHealth, useBackendReady } from "../components/BackendStatus";
import { usePageTitle } from "../components/usePageTitle";
import {
  modelOnlineStatusLabel,
  pickBaseModel,
  pickCourseLora,
} from "../features/models/modelStatus";

type CapabilityNode = {
  name: string;
  ready: boolean;
  note: string;
};

const INTEL_NODES: CapabilityNode[] = [
  { name: "文档解析", ready: true, note: "上传后提取文本与结构，保留页码区间" },
  { name: "结构化切分", ready: true, note: "按章节与条款切分，生成可追溯 Chunk" },
  { name: "混合检索", ready: true, note: "Dense + BM25 召回，RRF 融合与重排" },
  { name: "来源追溯", ready: true, note: "结果携带文件、章节、条款与页码" },
  { name: "文档问答", ready: true, note: "受检索证据约束的带来源回答（Qwen3-8B）" },
  { name: "智能审查", ready: true, note: "确定性规则引擎：覆盖/证据/资格风险/草稿安全/一致性" },
  { name: "评测中心", ready: true, note: "项目级评测、compare 与导出" },
  {
    name: "领域微调",
    ready: true,
    note: "Step 13 course_pilot + Step 14 在线服务（非 human_gold；须 served 才显示在线）",
  },
];

export default function DashboardPage() {
  usePageTitle("工作台");
  const health = useBackendHealth();
  const ready = useBackendReady();
  const projects = useQuery({ queryKey: ["projects"], queryFn: listProjects, retry: 0 });
  const modelsQuery = useQuery({ queryKey: ["models"], queryFn: listModels, retry: 0 });

  const apiConnected = health.isSuccess;
  const readyOkCount = ready.isSuccess
    ? ready.data.services.filter((s) => s.status === "ok").length
    : null;
  const readyTotal = ready.isSuccess ? ready.data.services.length : null;
  const openCapabilityCount = INTEL_NODES.filter((n) => n.ready).length;
  const projectTotal = projects.isSuccess ? projects.data.total : null;

  const baseModel = modelsQuery.data ? pickBaseModel(modelsQuery.data.items) : undefined;
  const loraModel = modelsQuery.data ? pickCourseLora(modelsQuery.data.items) : undefined;

  return (
    <div className="bp-dash">
      <section className="bp-dash-hero">
        <p className="bp-eyebrow">BidPilot / Evidence Workspace</p>
        <h1 className="bp-page-title">智能投标工作台</h1>
        <p className="bp-page-subtitle">
          定位资料、验证来源、形成可追溯的投标依据。已接入文档解析、混合检索、合规审查、Agent
          工作流与评测中心；领域微调以 course_pilot LoRA 轨道演示（非 human_gold；在线须 vLLM
          --enable-lora）。
        </p>
        <div className="bp-dash-actions">
          <Link to="/projects">
            <Button type="primary" size="large" icon={<ArrowRightOutlined />} iconPosition="end">
              进入项目
            </Button>
          </Link>
          <Link to="/evaluation">
            <Button size="large">评估中心</Button>
          </Link>
          <Link to="/projects">
            <Button size="large" icon={<PlusOutlined />}>
              创建项目
            </Button>
          </Link>
        </div>

        <div className="bp-dash-metrics" aria-label="系统概览">
          {projects.isLoading ? (
            <Skeleton.Button active size="small" style={{ width: 88, height: 32 }} />
          ) : (
            projectTotal != null && (
              <span className="bp-metric-chip">
                项目 <strong>{projectTotal}</strong>
              </span>
            )
          )}
          <span className="bp-metric-chip">
            已开放能力 <strong>{openCapabilityCount}</strong>
          </span>
          {health.isLoading ? (
            <Skeleton.Button active size="small" style={{ width: 100, height: 32 }} />
          ) : (
            <span className="bp-metric-chip">
              API <strong>{apiConnected ? "已连接" : "未连接"}</strong>
            </span>
          )}
          {ready.isLoading ? (
            <Skeleton.Button active size="small" style={{ width: 110, height: 32 }} />
          ) : (
            readyOkCount != null &&
            readyTotal != null && (
              <span className="bp-metric-chip">
                依赖 <strong>{`${readyOkCount}/${readyTotal}`}</strong>
              </span>
            )
          )}
          {modelsQuery.isLoading ? (
            <Skeleton.Button active size="small" style={{ width: 200, height: 32 }} />
          ) : (
            <>
              {baseModel && (
                <span className="bp-metric-chip" data-testid="base-model-chip">
                  Base <strong>{modelOnlineStatusLabel(baseModel)}</strong>
                </span>
              )}
              {loraModel && (
                <span className="bp-metric-chip" data-testid="lora-model-chip">
                  LoRA{" "}
                  <strong>
                    {loraModel.display_name || "Course LoRA"} · {modelOnlineStatusLabel(loraModel)}
                    {loraModel.version ? ` · ${loraModel.version}` : ""}
                  </strong>
                </span>
              )}
            </>
          )}
        </div>
      </section>

      <aside className="bp-intel" aria-label="Evidence Intelligence">
        <div className="bp-intel-scan" aria-hidden="true" />
        <div className="bp-intel-header">
          <h2 className="bp-intel-title">Evidence Intelligence</h2>
          <span className="bp-intel-sub">pipeline · live</span>
        </div>
        <div className="bp-intel-flow">
          {INTEL_NODES.map((node) => (
            <div
              key={node.name}
              className={`bp-intel-node${node.ready ? "" : " is-pending"}`}
            >
              <span className="bp-intel-dot" aria-hidden="true" />
              <div>
                <div className="bp-intel-node-title">
                  {node.name}
                  <span className="bp-intel-badge">{node.ready ? "online" : "queued"}</span>
                </div>
                <div className="bp-intel-node-note">{node.note}</div>
              </div>
            </div>
          ))}
        </div>
      </aside>
    </div>
  );
}
