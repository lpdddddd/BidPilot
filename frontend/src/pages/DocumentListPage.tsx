import { Alert, Button, Empty, Space, Table, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { listDocuments } from "../api/client";

export default function DocumentListPage() {
  const { projectId = "" } = useParams();
  const query = useQuery({
    queryKey: ["documents", projectId],
    queryFn: () => listDocuments(projectId),
    enabled: Boolean(projectId),
  });

  return (
    <div className="bp-panel">
      <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <h1 className="bp-title">项目文档</h1>
          <p className="bp-subtitle">当前为元数据占位页，完整上传与解析将在后续阶段实现</p>
        </div>
        <Link to={`/projects/${projectId}`}>
          <Button>返回项目</Button>
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
        locale={{ emptyText: <Empty description="暂无文档元数据" /> }}
        columns={[
          { title: "文件名", dataIndex: "file_name" },
          { title: "类型", dataIndex: "document_type" },
          {
            title: "解析状态",
            dataIndex: "parse_status",
            render: (value: string) => <Tag>{value}</Tag>,
          },
          { title: "MIME", dataIndex: "mime_type" },
          { title: "创建时间", dataIndex: "created_at" },
        ]}
      />
    </div>
  );
}
