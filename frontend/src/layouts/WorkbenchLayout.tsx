import { useState, type ReactNode } from "react";
import { Layout, Menu, Tag } from "antd";
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

function selectedNavKey(pathname: string): string {
  if (pathname === "/") return "/";
  const match = NAV_ITEMS.filter((item) => item.key !== "/").find((item) =>
    pathname.startsWith(item.key),
  );
  return match?.key ?? "/";
}

const ENV_LABEL = import.meta.env.DEV ? "开发环境" : "生产构建";

export default function WorkbenchLayout({ children }: { children: ReactNode }) {
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);

  return (
    <Layout className="bp-shell" hasSider>
      <Sider
        className="bp-sider"
        width={220}
        breakpoint="lg"
        collapsedWidth={64}
        collapsed={collapsed}
        onCollapse={setCollapsed}
        trigger={null}
        style={{ position: "sticky", top: 0, height: "100vh", overflow: "auto" }}
      >
        <div className="bp-logo" style={collapsed ? { padding: "0 18px" } : undefined}>
          <span className="bp-logo-mark" aria-hidden="true">
            BP
          </span>
          {!collapsed && <span>BidPilot</span>}
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedNavKey(location.pathname)]}
          items={NAV_ITEMS}
          style={{ borderInlineEnd: "none", marginTop: 8 }}
        />
        {!collapsed && (
          <div className="bp-sider-footer">
            招投标文件分析与
            <br />
            合规审查工作台
          </div>
        )}
      </Sider>
      <Layout>
        <Header className="bp-topbar">
          <div className="bp-topbar-title">BidPilot 投标工作台</div>
          <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
            <Tag color={import.meta.env.DEV ? "cyan" : "blue"}>{ENV_LABEL}</Tag>
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
