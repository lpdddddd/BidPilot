import { Layout, Menu, Typography } from "antd";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import ProjectListPage from "./pages/ProjectListPage";
import ProjectCreatePage from "./pages/ProjectCreatePage";
import ProjectDetailPage from "./pages/ProjectDetailPage";
import DocumentListPage from "./pages/DocumentListPage";

const { Header, Content } = Layout;

export default function App() {
  const location = useLocation();
  const selected = location.pathname.startsWith("/projects") ? "projects" : "projects";

  return (
    <Layout className="bp-shell">
      <Header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 24,
          background: "rgba(16, 42, 67, 0.92)",
          paddingInline: 24,
        }}
      >
        <Typography.Title level={3} style={{ color: "#fff", margin: 0, letterSpacing: 0.5 }}>
          BidPilot
        </Typography.Title>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={[selected]}
          style={{ flex: 1, background: "transparent", minWidth: 0 }}
          items={[
            {
              key: "projects",
              label: <Link to="/projects">项目</Link>,
            },
          ]}
        />
      </Header>
      <Content className="bp-content">
        <Routes>
          <Route path="/" element={<Navigate to="/projects" replace />} />
          <Route path="/projects" element={<ProjectListPage />} />
          <Route path="/projects/new" element={<ProjectCreatePage />} />
          <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
          <Route path="/projects/:projectId/documents" element={<DocumentListPage />} />
        </Routes>
      </Content>
    </Layout>
  );
}
