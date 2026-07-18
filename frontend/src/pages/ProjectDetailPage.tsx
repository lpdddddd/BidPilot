import { Alert, Button, Descriptions, Empty, Skeleton, Tabs, Tag } from "antd";
import { ArrowLeftOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { getProject } from "../api/client";
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

function ProjectOverview({ project }: { project: Project }) {
  return (
    <Descriptions bordered column={{ xs: 1, md: 2 }} size="middle">
      <Descriptions.Item label="状态">
        <Tag color="blue">{project.status}</Tag>
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
      <Descriptions.Item label="投标截止时间">{project.bid_deadline || "-"}</Descriptions.Item>
      <Descriptions.Item label="创建时间">{project.created_at}</Descriptions.Item>
    </Descriptions>
  );
}

export default function ProjectDetailPage() {
  const { projectId = "" } = useParams();
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
          defaultActiveKey="overview"
          items={[
            {
              key: "overview",
              label: "项目概览",
              children: <ProjectOverview project={project} />,
            },
            {
              key: "documents",
              label: "文档中心",
              children: <PendingCapability title="文档中心" step="第 3 步（文件上传与解析）" />,
            },
            {
              key: "search",
              label: "知识检索",
              children: <PendingCapability title="知识检索" step="第 4～5 步（向量检索与 RAG）" />,
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
