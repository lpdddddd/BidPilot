import { Button, Empty } from "antd";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { FolderOpenOutlined, SearchOutlined, UploadOutlined } from "@ant-design/icons";
import { listProjects } from "../api/client";
import { usePageTitle } from "../components/usePageTitle";

/** Global knowledge hub — project files live inside each project space. */
export default function KnowledgeHubPage() {
  usePageTitle("知识");
  const projects = useQuery({ queryKey: ["projects"], queryFn: listProjects, retry: 0 });
  const items = projects.data?.items ?? [];

  return (
    <div className="bp-knowledge-hub">
      <header className="bp-gallery-head">
        <div>
          <h1 className="bp-page-title">知识</h1>
          <p className="bp-page-subtitle">
            以项目组织招标文件与证据。跨项目知识库入口完善前，请从项目空间进入文件与检索。
          </p>
        </div>
        <Link to="/projects">
          <Button type="primary" icon={<UploadOutlined />}>
            去项目上传
          </Button>
        </Link>
      </header>

      <div className="bp-knowledge-search">
        <SearchOutlined />
        <span>搜索文件名、条款或证据片段（进入项目后可用）</span>
      </div>

      <section className="bp-knowledge-section">
        <h2 className="bp-space-section-title">项目文件集合</h2>
        {projects.isLoading ? (
          <div className="bp-soft-empty">加载中…</div>
        ) : items.length === 0 ? (
          <div className="bp-soft-empty">
            <Empty description="暂无项目文件集合" image={Empty.PRESENTED_IMAGE_SIMPLE}>
              <Link to="/projects?create=1">
                <Button type="primary">创建项目</Button>
              </Link>
            </Empty>
          </div>
        ) : (
          <div className="bp-knowledge-collections">
            {items.map((p) => (
              <Link key={p.id} to={`/projects/${p.id}?tab=documents`} className="bp-knowledge-collection">
                <FolderOpenOutlined />
                <div>
                  <strong>{p.project_name}</strong>
                  <span>{p.project_code}</span>
                </div>
                <span className="bp-quiet-link">打开文件</span>
              </Link>
            ))}
          </div>
        )}
      </section>

      <section className="bp-knowledge-section">
        <h2 className="bp-space-section-title">最近入口</h2>
        <div className="bp-knowledge-hints">
          <Link to="/projects">在项目中上传与解析文档</Link>
          <Link to="/review">从审查回到证据缺口</Link>
        </div>
      </section>
    </div>
  );
}
