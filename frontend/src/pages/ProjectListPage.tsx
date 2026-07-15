import { Alert, Button, Space, Table, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listProjects } from "../api/client";

export default function ProjectListPage() {
  const query = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
  });

  return (
    <div className="bp-panel">
      <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <h1 className="bp-title">招标项目</h1>
          <p className="bp-subtitle">查看与创建招投标分析项目</p>
        </div>
        <Link to="/projects/new">
          <Button type="primary">创建项目</Button>
        </Link>
      </Space>

      {query.isError && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message={(query.error as Error).message}
        />
      )}

      <Table
        rowKey="id"
        loading={query.isLoading}
        dataSource={query.data?.items || []}
        pagination={false}
        columns={[
          {
            title: "项目编号",
            dataIndex: "project_code",
            render: (value: string, row) => <Link to={`/projects/${row.id}`}>{value}</Link>,
          },
          { title: "项目名称", dataIndex: "project_name" },
          { title: "采购人", dataIndex: "purchaser" },
          {
            title: "状态",
            dataIndex: "status",
            render: (value: string) => <Tag color="blue">{value}</Tag>,
          },
          { title: "行业", dataIndex: "industry" },
          { title: "地区", dataIndex: "region" },
        ]}
      />
    </div>
  );
}
