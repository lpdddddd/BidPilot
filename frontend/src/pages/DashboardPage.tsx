import { Button, Skeleton } from "antd";
import { ArrowRightOutlined, PlusOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listProjects } from "../api/client";
import { useBackendHealth, useBackendReady } from "../components/BackendStatus";
import { usePageTitle } from "../components/usePageTitle";

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
  { name: "文档问答", ready: false, note: "大模型回答尚未接入，当前仅返回检索证据" },
  { name: "智能审查", ready: false, note: "规则与 Agent 工作流待后续开放" },
];

export default function DashboardPage() {
  usePageTitle("工作台");
  const health = useBackendHealth();
  const ready = useBackendReady();
  const projects = useQuery({ queryKey: ["projects"], queryFn: listProjects, retry: 0 });

  const apiConnected = health.isSuccess;
  const readyOkCount = ready.isSuccess
    ? ready.data.services.filter((s) => s.status === "ok").length
    : null;
  const readyTotal = ready.isSuccess ? ready.data.services.length : null;
  const openCapabilityCount = INTEL_NODES.filter((n) => n.ready).length;
  const projectTotal = projects.isSuccess ? projects.data.total : null;

  return (
    <div className="bp-dash">
      <section className="bp-dash-hero">
        <p className="bp-eyebrow">BidPilot / Evidence Workspace</p>
        <h1 className="bp-page-title">智能投标工作台</h1>
        <p className="bp-page-subtitle">
          定位资料、验证来源、形成可追溯的投标依据。当前开放文档解析与混合检索；问答与审查按阶段接入，不提供模拟结论。
        </p>
        <div className="bp-dash-actions">
          <Link to="/projects">
            <Button type="primary" size="large" icon={<ArrowRightOutlined />} iconPosition="end">
              进入项目
            </Button>
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
