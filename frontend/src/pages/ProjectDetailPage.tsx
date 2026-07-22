import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Button, Skeleton, Tabs, Tag } from "antd";
import { ArrowLeftOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { getProject } from "../api/client";
import DocumentCenter from "../features/documents/DocumentCenter";
import MatchingWorkspace from "../features/matching/MatchingWorkspace";
import ProposalDraftsWorkspace from "../features/proposalDrafts/ProposalDraftsWorkspace";
import RequirementsWorkspace from "../features/requirements/RequirementsWorkspace";
import KnowledgeSearch from "../features/search/KnowledgeSearch";
import AgentLoopPanel from "../features/agent/AgentLoopPanel";
import type { Project } from "../types/api";
import { usePageTitle } from "../components/usePageTitle";
import {
  buildProjectSearchParams,
  parseProjectSearchParams,
} from "./projectDetailParams";


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

function MetaItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="bp-meta-item">
      <div className="bp-meta-label">{label}</div>
      <div className="bp-meta-value">{children}</div>
    </div>
  );
}

function ProjectOverview({ project }: { project: Project }) {
  return (
    <div>
      <h2 className="bp-section-title">项目信息</h2>
      <div className="bp-meta-grid">
        <MetaItem label="状态">
          <Tag bordered={false} color="processing">
            {PROJECT_STATUS_LABELS[project.status] ?? project.status}
          </Tag>
        </MetaItem>
        <MetaItem label="采购人">{project.purchaser || "-"}</MetaItem>
        <MetaItem label="代理机构">{project.procurement_agency || "-"}</MetaItem>
        <MetaItem label="采购方式">{project.procurement_method || "-"}</MetaItem>
        <MetaItem label="行业">{project.industry || "-"}</MetaItem>
        <MetaItem label="地区">{project.region || "-"}</MetaItem>
        <MetaItem label="预算 (CNY)">{project.budget_cny || "-"}</MetaItem>
        <MetaItem label="最高限价 (CNY)">{project.price_ceiling_cny || "-"}</MetaItem>
        <MetaItem label="投标截止">{formatDateTime(project.bid_deadline)}</MetaItem>
        <MetaItem label="创建时间">{formatDateTime(project.created_at)}</MetaItem>
      </div>
    </div>
  );
}

export default function ProjectDetailPage() {
  const { projectId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const focus = useMemo(
    () => parseProjectSearchParams(searchParams),
    [searchParams],
  );
  const [activeTab, setActiveTab] = useState(focus.tab);
  const [chunkFocusDocumentId, setChunkFocusDocumentId] = useState<string | null>(
    focus.documentId,
  );
  const [focusPage, setFocusPage] = useState<number | null>(focus.page);
  const [focusChunkId, setFocusChunkId] = useState<string | null>(focus.chunkId);
  const [sourceAlert, setSourceAlert] = useState<string | null>(null);

  // Sync URL → page state (initial load, in-app navigation, back/forward).
  useEffect(() => {
    setActiveTab(focus.tab);
    setChunkFocusDocumentId(focus.documentId);
    setFocusPage(focus.page);
    setFocusChunkId(focus.chunkId);
    setSourceAlert(null);
  }, [focus.tab, focus.documentId, focus.page, focus.chunkId]);

  const query = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => getProject(projectId),
    enabled: Boolean(projectId),
  });
  usePageTitle(query.data ? query.data.project_name : "项目详情");

  const openSource = useCallback(
    (documentId: string, chunkId?: string | null, page?: number | null) => {
      setChunkFocusDocumentId(documentId);
      setFocusChunkId(chunkId ?? null);
      setFocusPage(page ?? null);
      setActiveTab("documents");
      setSourceAlert(null);
      setSearchParams(
        buildProjectSearchParams({
          tab: "documents",
          documentId,
          page: page ?? null,
          chunkId: chunkId ?? null,
        }),
        { replace: false },
      );
    },
    [setSearchParams],
  );

  const handleTabChange = useCallback(
    (key: string) => {
      setActiveTab(key);
      const next = buildProjectSearchParams({
        tab: key,
        documentId: key === "documents" ? chunkFocusDocumentId : null,
        page: key === "documents" ? focusPage : null,
        chunkId: key === "documents" ? focusChunkId : null,
      });
      setSearchParams(next, { replace: true });
    },
    [chunkFocusDocumentId, focusChunkId, focusPage, setSearchParams],
  );

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
    <div data-testid="project-detail-page">
      <header className="bp-workspace-banner">
        <div className="bp-workspace-title-row">
          <div>
            <p className="bp-eyebrow" style={{ marginBottom: 8 }}>
              Project Workspace
            </p>
            <h1 className="bp-page-title" style={{ marginBottom: 0 }}>
              {project.project_name}
            </h1>
            <div className="bp-workspace-meta">
              <span className="bp-workspace-code">{project.project_code}</span>
              <Tag bordered={false} color="processing">
                {PROJECT_STATUS_LABELS[project.status] ?? project.status}
              </Tag>
              {project.purchaser && <span>{project.purchaser}</span>}
              {(project.industry || project.region) && (
                <span>
                  {[project.industry, project.region].filter(Boolean).join(" / ")}
                </span>
              )}
            </div>
          </div>
          <Link to="/projects">
            <Button type="text" icon={<ArrowLeftOutlined />}>
              返回项目列表
            </Button>
          </Link>
        </div>
      </header>

      {sourceAlert && (
        <Alert
          type="warning"
          showIcon
          closable
          style={{ marginBottom: 12 }}
          message="无法打开引用来源"
          description={sourceAlert}
          data-testid="source-link-alert"
          onClose={() => setSourceAlert(null)}
        />
      )}

      <Tabs
        className="bp-workspace-nav"
        activeKey={activeTab}
        onChange={handleTabChange}
        items={[
          {
            key: "overview",
            label: "项目概览",
            children: (
              <div className="bp-workspace-body">
                <ProjectOverview project={project} />
              </div>
            ),
          },
          {
            key: "documents",
            label: "文档中心",
            children: (
              <div className="bp-workspace-body" data-testid="documents-tab-panel">
                <DocumentCenter
                  projectId={project.id}
                  focusChunkDocumentId={chunkFocusDocumentId}
                  focusPage={focusPage}
                  focusChunkId={focusChunkId}
                  onFocusConsumed={() => {
                    /* keep URL shareable; clear only transient open-once intent if needed */
                  }}
                  onFocusError={(msg) => setSourceAlert(msg)}
                />
              </div>
            ),
          },
          {
            key: "search",
            label: "知识检索",
            children: (
              <div className="bp-workspace-body">
                <KnowledgeSearch
                  projectId={project.id}
                  onOpenSource={(documentId, chunkId) => openSource(documentId, chunkId)}
                />
              </div>
            ),
          },
          {
            key: "requirements",
            label: "需求清单",
            children: (
              <div className="bp-workspace-body">
                <RequirementsWorkspace
                  projectId={project.id}
                  onOpenSource={(documentId, chunkId) => openSource(documentId, chunkId)}
                />
              </div>
            ),
          },
          {
            key: "matching",
            label: "材料匹配",
            children: (
              <div className="bp-workspace-body">
                <MatchingWorkspace
                  projectId={project.id}
                  onOpenSource={(documentId, chunkId) => openSource(documentId, chunkId)}
                />
              </div>
            ),
          },
          {
            key: "proposal-drafts",
            label: "响应草稿",
            children: (
              <div className="bp-workspace-body">
                <ProposalDraftsWorkspace
                  projectId={project.id}
                  onOpenSource={(documentId, chunkId) => openSource(documentId, chunkId)}
                />
              </div>
            ),
          },
          {
            key: "agent-loop",
            label: "Agent 闭环",
            children: (
              <div className="bp-workspace-body">
                <AgentLoopPanel projectId={project.id} />
              </div>
            ),
          },
        ]}
      />
    </div>
  );
}
