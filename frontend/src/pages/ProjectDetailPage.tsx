import { Alert, Button, Descriptions, Space, Spin, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { getProject } from "../api/client";

export default function ProjectDetailPage() {
  const { projectId = "" } = useParams();
  const query = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => getProject(projectId),
    enabled: Boolean(projectId),
  });

  if (query.isLoading) {
    return (
      <div className="bp-panel">
        <Spin tip="加载项目中..." />
      </div>
    );
  }

  if (query.isError || !query.data) {
    return (
      <div className="bp-panel">
        <Alert type="error" showIcon message={(query.error as Error)?.message || "项目不存在"} />
      </div>
    );
  }

  const project = query.data;

  return (
    <div className="bp-panel">
      <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <h1 className="bp-title">{project.project_name}</h1>
          <p className="bp-subtitle">{project.project_code}</p>
        </div>
        <Space>
          <Link to={`/projects/${project.id}/documents`}>
            <Button type="primary">文档列表</Button>
          </Link>
          <Link to="/projects">
            <Button>返回列表</Button>
          </Link>
        </Space>
      </Space>

      <Descriptions bordered column={2} size="middle">
        <Descriptions.Item label="状态">
          <Tag color="blue">{project.status}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="采购人">{project.purchaser || "-"}</Descriptions.Item>
        <Descriptions.Item label="代理机构">
          {project.procurement_agency || "-"}
        </Descriptions.Item>
        <Descriptions.Item label="采购方式">
          {project.procurement_method || "-"}
        </Descriptions.Item>
        <Descriptions.Item label="行业">{project.industry || "-"}</Descriptions.Item>
        <Descriptions.Item label="地区">{project.region || "-"}</Descriptions.Item>
        <Descriptions.Item label="预算(CNY)">{project.budget_cny || "-"}</Descriptions.Item>
        <Descriptions.Item label="最高限价(CNY)">
          {project.price_ceiling_cny || "-"}
        </Descriptions.Item>
      </Descriptions>
    </div>
  );
}
