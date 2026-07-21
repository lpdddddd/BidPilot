import { Badge, Button, Skeleton } from "antd";
import { ArrowRightOutlined, PlusOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listProjects } from "../api/client";
import { useBackendHealth, useBackendReady } from "../components/BackendStatus";
import { usePageTitle } from "../components/usePageTitle";

type CapabilityItem = {
  name: string;
  ready: boolean;
  note: string;
};

const CAPABILITIES: CapabilityItem[] = [
  { name: "文档解析与切分", ready: true, note: "上传、解析、结构感知 Chunk 与来源追溯" },
  { name: "混合检索", ready: true, note: "向量 + BM25 召回，RRF 融合与重排" },
  { name: "带来源的文档问答", ready: false, note: "尚未接入大模型，当前仅返回检索证据" },
  { name: "智能审查", ready: false, note: "规则与 Agent 工作流尚未开放" },
];

export default function DashboardPage() {
  usePageTitle("工作台");
  const health = useBackendHealth();
  const ready = useBackendReady();
  const projects = useQuery({ queryKey: ["projects"], queryFn: listProjects, retry: 0 });

  const apiConnected = health.isSuccess;
  const readyLabel = ready.isLoading
    ? "检查中"
    : ready.isSuccess
      ? `${ready.data.services.filter((s) => s.status === "ok").length}/${ready.data.services.length} 依赖正常`
      : "无法获取";
  const readyStatus: "success" | "warning" | "error" | "processing" = ready.isLoading
    ? "processing"
    : ready.isSuccess
      ? ready.data.status === "ok"
        ? "success"
        : ready.data.status === "degraded"
          ? "warning"
          : "error"
      : "error";

  return (
    <div>
      <div className="bp-system-strip" aria-label="系统概况">
        <span className="bp-system-item">
          {health.isLoading ? (
            <Skeleton.Button active size="small" style={{ width: 72, height: 18 }} />
          ) : (
            <Badge status={apiConnected ? "success" : "error"} text={apiConnected ? "API 已连接" : "API 未连接"} />
          )}
        </span>
        <span className="bp-system-item">
          {ready.isLoading ? (
            <Skeleton.Button active size="small" style={{ width: 96, height: 18 }} />
          ) : (
            <Badge status={readyStatus} text={readyLabel} />
          )}
        </span>
        <span className="bp-system-item">
          项目{" "}
          <strong style={{ color: "var(--bp-text)", fontWeight: 600 }}>
            {projects.isLoading ? "…" : projects.isSuccess ? projects.data.total : "-"}
          </strong>
        </span>
      </div>

      <div className="bp-hero">
        <section className="bp-hero-main">
          <p className="bp-hero-kicker">BidPilot</p>
          <h1 className="bp-page-title">智能投标工作台</h1>
          <p className="bp-page-subtitle">
            在项目资料中检索证据、追溯来源、准备合规审查。当前开放文档解析与混合检索；问答与审查能力按阶段接入，不提供模拟结论。
          </p>
          <div className="bp-hero-actions">
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
        </section>

        <aside className="bp-panel-quiet">
          <h2 className="bp-section-title">系统能力</h2>
          <div className="bp-capability-list">
            {CAPABILITIES.map((item) => (
              <div key={item.name} className="bp-capability-row">
                <span
                  className={`bp-capability-dot${item.ready ? " is-ready" : ""}`}
                  aria-label={item.ready ? "已开放" : "未开放"}
                />
                <div>
                  <div className="bp-capability-name">
                    {item.name}
                    <span className="bp-faint" style={{ marginLeft: 8, fontWeight: 400, fontSize: 12 }}>
                      {item.ready ? "已开放" : "待接入"}
                    </span>
                  </div>
                  <div className="bp-capability-note">{item.note}</div>
                </div>
              </div>
            ))}
          </div>
        </aside>
      </div>
    </div>
  );
}
