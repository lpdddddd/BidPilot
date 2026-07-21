import { useState, type ReactNode } from "react";
import { Layout, Menu } from "antd";
import {
  AppstoreOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  ProjectOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { Link, useLocation } from "react-router-dom";
import BackendStatus from "../components/BackendStatus";

const { Sider, Header, Content } = Layout;

const NAV_ITEMS = [
  { key: "/", icon: <AppstoreOutlined />, label: <Link to="/">工作台</Link> },
  { key: "/projects", icon: <ProjectOutlined />, label: <Link to="/projects">项目</Link> },
  { key: "/knowledge", icon: <DatabaseOutlined />, label: <Link to="/knowledge">知识库</Link> },
  {
    key: "/review",
    icon: <SafetyCertificateOutlined />,
    label: <Link to="/review">智能审查</Link>,
  },
  {
    key: "/evaluation",
    icon: <ExperimentOutlined />,
    label: <Link to="/evaluation">评估中心</Link>,
  },
];

const CRUMB_LABELS: Record<string, string> = {
  "/": "工作台",
  "/projects": "项目",
  "/knowledge": "知识库",
  "/review": "智能审查",
  "/evaluation": "评估中心",
};

function selectedNavKey(pathname: string): string {
  if (pathname === "/") return "/";
  const match = NAV_ITEMS.filter((item) => item.key !== "/").find((item) =>
    pathname.startsWith(item.key),
  );
  return match?.key ?? "/";
}

function topbarCrumb(pathname: string): { section: string; detail?: string } {
  if (pathname === "/") return { section: "工作台" };
  if (pathname.startsWith("/projects/") && pathname !== "/projects") {
    return { section: "项目", detail: "工作区" };
  }
  const key = selectedNavKey(pathname);
  return { section: CRUMB_LABELS[key] ?? "BidPilot" };
}

export default function WorkbenchLayout({ children }: { children: ReactNode }) {
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const crumb = topbarCrumb(location.pathname);

  return (
    <Layout className="bp-shell" hasSider>
      <Sider
        className="bp-sider"
        width={196}
        breakpoint="lg"
        collapsedWidth={64}
        collapsed={collapsed}
        onCollapse={setCollapsed}
        trigger={null}
        style={{ position: "sticky", top: 0, height: "100vh", overflow: "auto" }}
      >
        <div className={`bp-logo${collapsed ? " bp-logo-collapsed" : ""}`}>
          <span className="bp-logo-mark" aria-hidden="true">
            BP
          </span>
          {!collapsed && <span>BidPilot</span>}
        </div>
        <Menu
          className="bp-sider-menu"
          theme="dark"
          mode="inline"
          selectedKeys={[selectedNavKey(location.pathname)]}
          items={NAV_ITEMS}
        />
        {!collapsed && (
          <div className="bp-sider-footer">
            证据优先的投标工作台
            <br />
            定位资料 · 验证来源 · 可追溯依据
          </div>
        )}
      </Sider>
      <Layout>
        <Header className="bp-topbar">
          <div className="bp-topbar-crumb" aria-label="当前位置">
            <strong>{crumb.section}</strong>
            {crumb.detail && (
              <>
                <span className="bp-faint">/</span>
                <span>{crumb.detail}</span>
              </>
            )}
          </div>
          <div className="bp-topbar-right">
            <BackendStatus />
          </div>
        </Header>
        <Content>
          <div className="bp-content">{children}</div>
        </Content>
      </Layout>
    </Layout>
  );
}
