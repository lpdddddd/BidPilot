import { useState } from "react";
import { Alert, Button, Descriptions, Empty, Skeleton, Tabs, Tag } from "antd";
import { ArrowLeftOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { getProject } from "../api/client";
import DocumentCenter from "../features/documents/DocumentCenter";
import KnowledgeSearch from "../features/search/KnowledgeSearch";
import type { Project } from "../types/api";
import { usePageTitle } from "../components/usePageTitle";

function PendingCapability({ title, step }: { title: string; step: string }) {
  return (
    <Empty
      image={Empty.PRESENTED_IMAGE_SIMPLE}
      style={{ padding: "48px 0" }}
      description={
        <div>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>{title}能力建设中</div>
          <div style={{ color: "var(--bp-text-muted)", fontSize: 13 }}>
            将在{step}接入，当前阶段不提供模拟数据
          </div>
        </div>
      }
    />
  );
}

const PROJECT_STATUS_LABELS: Record<string, string> = {
  draft: "草稿",
  parsing: "解析中",
  analyzing: "分析中",
  reviewing: "审查中",
  completed: "已完成",
  archived: "已归档",
};

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function ProjectOverview({ project }: { project: Project }) {
  return (
    <Descriptions bordered column={{ xs: 1, md: 2 }} size="middle">
      <Descriptions.Item label="状态">
        <Tag bordered={false} color="blue">
          {PROJECT_STATUS_LABELS[project.status] ?? project.status}
        </Tag>
      </Descriptions.Item>
      <Descriptions.Item label="采购人">{project.purchaser || "-"}</Descriptions.Item>
      <Descriptions.Item label="代理机构">{project.procurement_agency || "-"}</Descriptions.Item>
      <Descriptions.Item label="采购方式">{project.procurement_method || "-"}</Descriptions.Item>
      <Descriptions.Item label="行业">{project.industry || "-"}</Descriptions.Item>
      <Descriptions.Item label="地区">{project.region || "-"}</Descriptions.Item>
      <Descriptions.Item label="预算 (CNY)">{project.budget_cny || "-"}</Descriptions.Item>
      <Descriptions.Item label="最高限价 (CNY)">
        {project.price_ceiling_cny || "-"}
      </Descriptions.Item>
      <Descriptions.Item label="投标截止时间">
        {formatDateTime(project.bid_deadline)}
      </Descriptions.Item>
      <Descriptions.Item label="创建时间">{formatDateTime(project.created_at)}</Descriptions.Item>
    </Descriptions>
  );
}

export default function ProjectDetailPage() {
  const { projectId = "" } = useParams();
  const [activeTab, setActiveTab] = useState("overview");
  const [chunkFocusDocumentId, setChunkFocusDocumentId] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => getProject(projectId),
    enabled: Boolean(projectId),
  });
  usePageTitle(query.data ? query.data.project_name : "项目详情");

  if (query.isLoading) {
    return (
      <div className="bp-panel">
        <Skeleton active paragraph={{ rows: 8 }} />
      </div>
    );
  }

  if (query.isError || !query.data) {
    return (
      <div className="bp-panel">
        <Alert
          type="error"
          showIcon
          message="项目加载失败"
          description={(query.error as Error)?.message || "项目不存在"}
          action={
            <Button size="small" onClick={() => query.refetch()}>
              重试
            </Button>
          }
        />
        <div style={{ marginTop: 16 }}>
          <Link to="/projects">
            <Button icon={<ArrowLeftOutlined />}>返回项目列表</Button>
          </Link>
        </div>
      </div>
    );
  }

  const project = query.data;

  return (
    <div>
      <div
        className="bp-page-header"
        style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 16, flexWrap: "wrap" }}
      >
        <div>
          <h1 className="bp-page-title">{project.project_name}</h1>
          <p className="bp-page-subtitle">{project.project_code}</p>
        </div>
        <Link to="/projects">
          <Button icon={<ArrowLeftOutlined />}>返回项目列表</Button>
        </Link>
      </div>

      <div className="bp-panel">
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            {
              key: "overview",
              label: "项目概览",
              children: <ProjectOverview project={project} />,
            },
            {
              key: "documents",
              label: "文档中心",
              children: (
                <DocumentCenter
                  projectId={project.id}
                  focusChunkDocumentId={chunkFocusDocumentId}
                  onFocusConsumed={() => setChunkFocusDocumentId(null)}
                />
              ),
            },
            {
              key: "search",
              label: "知识检索",
              children: (
                <KnowledgeSearch
                  projectId={project.id}
                  onOpenSource={(documentId) => {
                    setChunkFocusDocumentId(documentId);
                    setActiveTab("documents");
                  }}
                />
              ),
            },
            {
              key: "review",
              label: "智能审查",
              children:
                <PendingCapability title="智能审查" step="第 6～10 步（规则与 Agent 工作流）" />,
            },
          ]}
        />
      </div>
    </div>
  );
}
