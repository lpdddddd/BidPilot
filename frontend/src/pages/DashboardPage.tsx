import { Badge, Button, Col, Row, Skeleton, Tag } from "antd";
import {
  ApiOutlined,
  ArrowRightOutlined,
  DeploymentUnitOutlined,
  FolderOpenOutlined,
} from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listProjects } from "../api/client";
import { useBackendHealth, useBackendReady } from "../components/BackendStatus";
import { usePageTitle } from "../components/usePageTitle";

type CapabilityStage = {
  name: string;
  status: "done" | "pending";
  note: string;
};

const CAPABILITY_STAGES: CapabilityStage[] = [
  { name: "基础工程", status: "done", note: "数据库、API 与前端壳层已就绪" },
  { name: "文件解析与 Chunk", status: "done", note: "上传、解析、结构感知切分与来源追溯" },
  { name: "混合检索", status: "done", note: "Qdrant 向量 + BM25 关键词召回，RRF 融合与重排" },
  { name: "带来源的文档问答", status: "pending", note: "第 6 步接入，基于检索证据生成回答" },
  { name: "智能审查 Agent", status: "pending", note: "合规规则与工作流审查，第 7～10 步接入" },
  { name: "LoRA 领域模型", status: "pending", note: "领域微调模型，第 14 步接入" },
];

export default function DashboardPage() {
  usePageTitle("工作台");
  const health = useBackendHealth();
  const ready = useBackendReady();
  const projects = useQuery({ queryKey: ["projects"], queryFn: listProjects, retry: 0 });

  const apiConnected = health.isSuccess;

  return (
    <div>
      <div className="bp-page-header">
        <h1 className="bp-page-title">BidPilot 工作台</h1>
        <p className="bp-page-subtitle">让每一份投标文件都可检索、可追溯、可审查。</p>
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={8}>
          <div className="bp-panel bp-stat-card">
            <span className="bp-stat-icon">
              <ApiOutlined />
            </span>
            <div>
              <div className="bp-stat-label">API 连接</div>
              <div className="bp-stat-value" style={{ fontSize: 16 }}>
                {health.isLoading ? (
                  <Skeleton.Button active size="small" />
                ) : (
                  <Badge
                    status={apiConnected ? "success" : "error"}
                    text={apiConnected ? "已连接" : "未连接"}
                  />
                )}
              </div>
            </div>
          </div>
        </Col>
        <Col xs={24} sm={12} lg={8}>
          <div className="bp-panel bp-stat-card">
            <span className="bp-stat-icon">
              <DeploymentUnitOutlined />
            </span>
            <div>
              <div className="bp-stat-label">后端依赖就绪</div>
              <div className="bp-stat-value" style={{ fontSize: 16 }}>
                {ready.isLoading ? (
                  <Skeleton.Button active size="small" />
                ) : ready.isSuccess ? (
                  <Badge
                    status={
                      ready.data.status === "ok"
                        ? "success"
                        : ready.data.status === "degraded"
                          ? "warning"
                          : "error"
                    }
                    text={`${ready.data.services.filter((s) => s.status === "ok").length} / ${
                      ready.data.services.length
                    } 项依赖正常`}
                  />
                ) : (
                  <Badge status="error" text="无法获取" />
                )}
              </div>
            </div>
          </div>
        </Col>
        <Col xs={24} sm={12} lg={8}>
          <div className="bp-panel bp-stat-card">
            <span className="bp-stat-icon">
              <FolderOpenOutlined />
            </span>
            <div>
              <div className="bp-stat-label">项目数量</div>
              <div className="bp-stat-value">
                {projects.isLoading ? (
                  <Skeleton.Button active size="small" />
                ) : projects.isSuccess ? (
                  <span>{projects.data.total}</span>
                ) : (
                  <span style={{ color: "var(--bp-text-muted)", fontWeight: 400, fontSize: 14 }}>
                    未能读取（需后端与数据库可用）
                  </span>
                )}
              </div>
            </div>
          </div>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={16}>
          <div className="bp-panel">
            <h2 style={{ margin: "0 0 12px", fontSize: 16 }}>数据能力建设进度</h2>
            <div style={{ display: "grid", gap: 2 }}>
              {CAPABILITY_STAGES.map((stage) => (
                <div key={stage.name} className="bp-stage-row">
                  <span
                    className={`bp-stage-dot ${stage.status}`}
                    aria-label={stage.status === "done" ? "已完成" : "待开发"}
                  />
                  <div style={{ flex: 1 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ fontWeight: 600 }}>{stage.name}</span>
                      {stage.status === "done" && (
                        <Tag color="green" style={{ marginInlineEnd: 0 }}>
                          已完成
                        </Tag>
                      )}
                    </div>
                    <div style={{ color: "var(--bp-text-muted)", fontSize: 13, marginTop: 2 }}>
                      {stage.note}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </Col>
        <Col xs={24} lg={8}>
          <div
            className="bp-panel"
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 12,
              background: "linear-gradient(160deg, #102138 0%, #16304f 100%)",
              border: "none",
              color: "#f4f7fa",
            }}
          >
            <h2 style={{ margin: 0, fontSize: 16, color: "#f4f7fa" }}>项目工作区</h2>
            <p style={{ margin: 0, color: "rgba(244,247,250,0.72)", fontSize: 13 }}>
              创建与管理招投标分析项目。文档解析、知识检索与智能审查能力将逐步在项目工作区内接入。
            </p>
            <Link to="/projects">
              <Button type="primary" icon={<ArrowRightOutlined />} iconPosition="end">
                进入项目工作区
              </Button>
            </Link>
          </div>
        </Col>
      </Row>
    </div>
  );
}
