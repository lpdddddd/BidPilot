import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  App as AntApp,
  Button,
  Empty,
  Form,
  Input,
  Modal,
  Segmented,
  Select,
  Skeleton,
  Space,
} from "antd";
import { AppstoreOutlined, PlusOutlined, ReloadOutlined, UnorderedListOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
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

function stageOf(status: string): { label: string; weight: number } {
  const map: Record<string, { label: string; weight: number }> = {
    draft: { label: "起草", weight: 1 },
    parsing: { label: "解析", weight: 2 },
    analyzing: { label: "分析", weight: 3 },
    reviewing: { label: "审查", weight: 4 },
    completed: { label: "完成", weight: 5 },
    archived: { label: "归档", weight: 5 },
  };
  return map[status] ?? { label: PROJECT_STATUS_LABELS[status] ?? status, weight: 1 };
}

function formatDeadline(value: string | null | undefined): string {
  if (!value) return "未设定截止";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

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
  const [searchParams, setSearchParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  const [view, setView] = useState<"space" | "list">("space");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<string | undefined>();
  const query = useQuery({ queryKey: ["projects"], queryFn: listProjects });

  useEffect(() => {
    if (searchParams.get("create") === "1") {
      setCreateOpen(true);
      const next = new URLSearchParams(searchParams);
      next.delete("create");
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  const filtered = useMemo(() => {
    const items = query.data?.items ?? [];
    return items.filter((p) => {
      if (status && p.status !== status) return false;
      if (!q.trim()) return true;
      const needle = q.trim().toLowerCase();
      return (
        p.project_name.toLowerCase().includes(needle) ||
        p.project_code.toLowerCase().includes(needle) ||
        (p.purchaser || "").toLowerCase().includes(needle)
      );
    });
  }, [query.data, q, status]);

  return (
    <div className="bp-gallery-page">
      <header className="bp-gallery-head">
        <div>
          <h1 className="bp-page-title">项目</h1>
          <p className="bp-page-subtitle">以项目空间组织投标协作，快速筛选并继续处理。</p>
        </div>
        <Space wrap>
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
      </header>

      <div className="bp-gallery-toolbar">
        <Input
          allowClear
          placeholder="搜索项目名称、编号或采购人"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ maxWidth: 320 }}
        />
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 140 }}
          value={status}
          onChange={setStatus}
          options={Object.entries(PROJECT_STATUS_LABELS).map(([value, label]) => ({
            value,
            label,
          }))}
        />
        <Segmented
          value={view}
          onChange={(v) => setView(v as "space" | "list")}
          options={[
            { value: "space", icon: <AppstoreOutlined />, label: "空间" },
            { value: "list", icon: <UnorderedListOutlined />, label: "列表" },
          ]}
        />
      </div>

      {query.isLoading ? (
        <Skeleton active paragraph={{ rows: 6 }} />
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
      ) : filtered.length === 0 ? (
        <div className="bp-soft-empty">
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={query.data?.items.length ? "没有匹配的项目" : "还没有任何项目"}
          >
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              创建项目
            </Button>
          </Empty>
        </div>
      ) : view === "space" ? (
        <div className="bp-gallery-grid">
          {filtered.map((p: Project) => (
            <Link key={p.id} to={`/projects/${p.id}`} className="bp-gallery-card">
              <div className="bp-gallery-card-meta">
                <span>{PROJECT_STATUS_LABELS[p.status] ?? p.status}</span>
                <span>{p.project_code}</span>
              </div>
              <h2>{p.project_name}</h2>
              <p>{p.purchaser || "招标单位未填写"}</p>
              <div className="bp-project-stage" title="按项目状态估算的阶段，非真实完成度统计">
                <span className="bp-project-stage-label">阶段 · {stageOf(p.status).label}</span>
                <div className="bp-project-progress-track" aria-hidden="true">
                  <span style={{ width: `${(stageOf(p.status).weight / 5) * 100}%` }} />
                </div>
              </div>
              <div className="bp-gallery-card-foot">
                <span>{formatDeadline(p.bid_deadline)}</span>
                <span>进入空间</span>
              </div>
            </Link>
          ))}
        </div>
      ) : (
        <div className="bp-compact-list">
          {filtered.map((p) => (
            <Link key={p.id} to={`/projects/${p.id}`} className="bp-compact-row">
              <div>
                <strong>{p.project_name}</strong>
                <span>
                  {p.project_code}
                  {p.purchaser ? ` · ${p.purchaser}` : ""}
                </span>
              </div>
              <span className="bp-compact-status">{PROJECT_STATUS_LABELS[p.status] ?? p.status}</span>
              <span className="bp-compact-deadline">{formatDeadline(p.bid_deadline)}</span>
            </Link>
          ))}
        </div>
      )}

      <CreateProjectModal open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  );
}
