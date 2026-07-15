import { Alert, Button, Form, Input, Space, message } from "antd";
import { useMutation } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { createProject } from "../api/client";

export default function ProjectCreatePage() {
  const navigate = useNavigate();
  const mutation = useMutation({
    mutationFn: createProject,
    onSuccess: (project) => {
      message.success("项目已创建");
      navigate(`/projects/${project.id}`);
    },
  });

  return (
    <div className="bp-panel" style={{ maxWidth: 720 }}>
      <h1 className="bp-title">创建项目</h1>
      <p className="bp-subtitle">登记基础项目元数据，后续再上传招标文件</p>

      {mutation.isError && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message={(mutation.error as Error).message}
        />
      )}

      <Form
        layout="vertical"
        onFinish={(values) => mutation.mutate(values)}
        initialValues={{}}
      >
        <Form.Item
          label="项目编号"
          name="project_code"
          rules={[{ required: true, message: "请输入项目编号" }]}
        >
          <Input placeholder="例如 DEMO-2026-001" />
        </Form.Item>
        <Form.Item
          label="项目名称"
          name="project_name"
          rules={[{ required: true, message: "请输入项目名称" }]}
        >
          <Input placeholder="招标项目名称" />
        </Form.Item>
        <Form.Item label="采购人" name="purchaser">
          <Input />
        </Form.Item>
        <Form.Item label="行业" name="industry">
          <Input />
        </Form.Item>
        <Form.Item label="地区" name="region">
          <Input />
        </Form.Item>
        <Space>
          <Button type="primary" htmlType="submit" loading={mutation.isPending}>
            提交
          </Button>
          <Link to="/projects">
            <Button>返回</Button>
          </Link>
        </Space>
      </Form>
    </div>
  );
}
