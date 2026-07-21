import { useState } from "react";
import {
  Alert,
  App as AntApp,
  Button,
  Empty,
  Form,
  Input,
  Modal,
  Skeleton,
  Space,
  Table,
  Tag,
} from "antd";
import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { createProject, listProjects } from "../api/client";
import type { Project, ProjectCreatePayload } from "../types/api";
import { usePageTitle } from "../components/usePageTitle";

const PROJECT_STATUS_LABELS: Record<string, string> = {
  draft: "草稿",
  parsing: "解析中",
  analyzing: "分析中",
  reviewing: "审查中",
  completed: "已完成",
  archived: "已归档",
};

function CreateProjectModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const { message } = AntApp.useApp();
  const [form] = Form.useForm<ProjectCreatePayload>();
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: createProject,
    onSuccess: (project) => {
      message.success(`项目「${project.project_name}」已创建`);
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      form.resetFields();
      onClose();
    },
  });

  return (
    <Modal
      title="新建项目"
      open={open}
      onCancel={onClose}
      onOk={() => form.submit()}
      okText="创建"
      cancelText="取消"
      confirmLoading={mutation.isPending}
      destroyOnClose
    >
      {mutation.isError && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message={(mutation.error as Error).message}
        />
      )}
      <Form
        form={form}
        layout="vertical"
        onFinish={(values) => mutation.mutate(values)}
        requiredMark="optional"
      >
        <Form.Item
          label="项目编号"
          name="project_code"
          rules={[{ required: true, message: "请输入项目编号" }]}
          extra="用于唯一标识项目，例如 ZB-2026-001"
        >
          <Input maxLength={128} />
        </Form.Item>
        <Form.Item
          label="项目名称"
          name="project_name"
          rules={[{ required: true, message: "请输入项目名称" }]}
        >
          <Input maxLength={512} />
        </Form.Item>
        <Form.Item label="采购人" name="purchaser">
          <Input maxLength={255} />
        </Form.Item>
        <Form.Item label="行业" name="industry">
          <Input maxLength={128} />
        </Form.Item>
        <Form.Item label="地区" name="region">
          <Input maxLength={128} />
        </Form.Item>
      </Form>
    </Modal>
  );
}

export default function ProjectListPage() {
  usePageTitle("项目");
  const [createOpen, setCreateOpen] = useState(false);
  const query = useQuery({ queryKey: ["projects"], queryFn: listProjects });

  return (
    <div>
      <div className="bp-page-header bp-page-header-row">
        <div>
          <h1 className="bp-page-title">项目</h1>
          <p className="bp-page-subtitle">
            管理招投标分析项目。在项目工作区内上传文档、检索证据并准备审查。
          </p>
        </div>
        <Space>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => query.refetch()}
            loading={query.isFetching && !query.isLoading}
          >
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新建项目
          </Button>
        </Space>
      </div>

      <div className="bp-panel">
        {query.isSuccess ? (
          query.data.items.length === 0 ? (
            <div className="bp-empty-block">
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={
                  <div>
                    <div className="bp-empty-title">还没有任何项目</div>
                    <div className="bp-empty-desc">
                      创建第一个招投标分析项目，随后可在项目内上传与检索文件。
                    </div>
                  </div>
                }
              >
                <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
                  创建第一个项目
                </Button>
              </Empty>
            </div>
          ) : (
            <Table<Project>
              rowKey="id"
              dataSource={query.data.items}
              pagination={query.data.items.length > 20 ? { pageSize: 20 } : false}
              scroll={{ x: 800 }}
              columns={[
                {
                  title: "项目编号",
                  dataIndex: "project_code",
                  render: (value: string, row) => <Link to={`/projects/${row.id}`}>{value}</Link>,
                },
                { title: "项目名称", dataIndex: "project_name", ellipsis: true },
                { title: "采购人", dataIndex: "purchaser", render: (v: string | null) => v || "-" },
                {
                  title: "状态",
                  dataIndex: "status",
                  width: 100,
                  render: (value: string) => (
                    <Tag bordered={false} color="processing">
                      {PROJECT_STATUS_LABELS[value] ?? value}
                    </Tag>
                  ),
                },
                { title: "行业", dataIndex: "industry", render: (v: string | null) => v || "-" },
                { title: "地区", dataIndex: "region", render: (v: string | null) => v || "-" },
              ]}
            />
          )
        ) : query.isError ? (
          <Alert
            type="error"
            showIcon
            message="项目列表加载失败"
            description={(query.error as Error).message}
            action={
              <Button size="small" onClick={() => query.refetch()}>
                重试
              </Button>
            }
          />
        ) : (
          <Skeleton active paragraph={{ rows: 6 }} />
        )}
      </div>

      <CreateProjectModal open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  );
}
