import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Button, Dropdown, Drawer, Input, Modal } from "antd";
import {
  MenuOutlined,
  PlusOutlined,
  SearchOutlined,
  ThunderboltOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { Link, useLocation, useNavigate } from "react-router-dom";
import BackendStatus from "../components/BackendStatus";

const PRIMARY_NAV = [
  { key: "/", label: "工作台", to: "/" },
  { key: "/projects", label: "项目", to: "/projects" },
  { key: "/knowledge", label: "知识", to: "/knowledge" },
  { key: "/review", label: "审查", to: "/review" },
];

function selectedNavKey(pathname: string): string {
  if (pathname === "/") return "/";
  const match = PRIMARY_NAV.filter((item) => item.key !== "/").find((item) =>
    pathname.startsWith(item.key),
  );
  return match?.key ?? "";
}

function CommandPalette({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [q, setQ] = useState("");

  const actions = useMemo(
    () =>
      [
        { label: "打开工作台", path: "/", keywords: "home 工作台" },
        { label: "打开项目", path: "/projects", keywords: "projects 项目" },
        { label: "打开知识", path: "/knowledge", keywords: "knowledge 知识" },
        { label: "打开审查", path: "/review", keywords: "review 审查" },
        { label: "打开评估中心", path: "/evaluation", keywords: "evaluation 评估" },
        { label: "新建项目", path: "/projects?create=1", keywords: "new 新建" },
      ].filter((a) => {
        const needle = q.trim().toLowerCase();
        if (!needle) return true;
        return (
          a.label.toLowerCase().includes(needle) ||
          a.keywords.toLowerCase().includes(needle)
        );
      }),
    [q],
  );

  useEffect(() => {
    if (!open) setQ("");
  }, [open]);

  return (
    <Modal
      open={open}
      onCancel={onClose}
      footer={null}
      closable={false}
      width={480}
      className="bp-cmdk-modal"
      styles={{ body: { padding: 0 } }}
      destroyOnHidden
    >
      <div className="bp-cmdk">
        <Input
          autoFocus
          size="large"
          prefix={<SearchOutlined />}
          placeholder="搜索页面或操作…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && actions[0]) {
              navigate(actions[0].path);
              onClose();
            }
          }}
          variant="borderless"
        />
        <ul className="bp-cmdk-list">
          {actions.map((a) => (
            <li key={a.path}>
              <button
                type="button"
                onClick={() => {
                  navigate(a.path);
                  onClose();
                }}
              >
                {a.label}
              </button>
            </li>
          ))}
          {actions.length === 0 && <li className="bp-cmdk-empty">无匹配操作</li>}
        </ul>
        <div className="bp-cmdk-hint">Enter 执行 · Esc 关闭</div>
      </div>
    </Modal>
  );
}

export default function WorkbenchLayout({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [scrolled, setScrolled] = useState(false);
  const [cmdOpen, setCmdOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [sysOpen, setSysOpen] = useState(false);
  const active = selectedNavKey(location.pathname);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCmdOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  const moreMenu = {
    items: [
      { key: "eval", label: <Link to="/evaluation">评估中心</Link> },
      { key: "sys", label: "系统验证", onClick: () => setSysOpen(true) },
    ],
  };

  const quickMenu = {
    items: [
      { key: "cmd", label: "命令面板", onClick: () => setCmdOpen(true) },
      { key: "review", label: <Link to="/review">智能审查</Link> },
      { key: "knowledge", label: <Link to="/knowledge">知识与文件</Link> },
      { key: "eval", label: <Link to="/evaluation">评估中心</Link> },
    ],
  };

  return (
    <div className={`bp-app${scrolled ? " is-scrolled" : ""}`}>
      <header className="bp-float-nav">
        <Link to="/" className="bp-brand">
          <span className="bp-brand-mark" aria-hidden="true">
            B
          </span>
          <span className="bp-brand-text">BidPilot</span>
        </Link>

        <nav className="bp-float-links" aria-label="主导航">
          {PRIMARY_NAV.map((item) => (
            <Link
              key={item.key}
              to={item.to}
              className={`bp-float-link${active === item.key ? " is-active" : ""}`}
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <div className="bp-float-actions">
          <button type="button" className="bp-nav-search" onClick={() => setCmdOpen(true)}>
            <SearchOutlined />
            <span className="bp-nav-search-label">搜索</span>
            <kbd>⌘K</kbd>
          </button>
          <Button
            type="primary"
            className="bp-nav-create"
            icon={<PlusOutlined />}
            onClick={() => navigate("/projects?create=1")}
          >
            新建项目
          </Button>
          <Dropdown menu={moreMenu} placement="bottomRight" trigger={["click"]}>
            <Button type="text" className="bp-nav-icon-btn" icon={<UserOutlined />} aria-label="更多" />
          </Dropdown>
          <Button
            type="text"
            className="bp-nav-icon-btn bp-nav-mobile"
            icon={<MenuOutlined />}
            aria-label="打开菜单"
            onClick={() => setMobileOpen(true)}
          />
        </div>
      </header>

      <main className="bp-main">
        <div className="bp-main-inner">{children}</div>
      </main>

      <Dropdown menu={quickMenu} placement="topRight" trigger={["click"]}>
        <button type="button" className="bp-ai-float" aria-label="快捷能力">
          <ThunderboltOutlined />
          <span>快捷</span>
        </button>
      </Dropdown>

      <nav className="bp-mobile-tabbar" aria-label="移动导航">
        {PRIMARY_NAV.map((item) => (
          <Link
            key={item.key}
            to={item.to}
            className={active === item.key ? "is-active" : undefined}
          >
            {item.label}
          </Link>
        ))}
      </nav>

      <Drawer title="菜单" placement="right" open={mobileOpen} onClose={() => setMobileOpen(false)} width={300}>
        <div className="bp-mobile-menu">
          {PRIMARY_NAV.map((item) => (
            <Link key={item.key} to={item.to} onClick={() => setMobileOpen(false)}>
              {item.label}
            </Link>
          ))}
          <Link to="/evaluation" onClick={() => setMobileOpen(false)}>
            评估中心
          </Link>
          <button
            type="button"
            onClick={() => {
              setMobileOpen(false);
              setSysOpen(true);
            }}
          >
            系统验证
          </button>
        </div>
      </Drawer>

      <CommandPalette open={cmdOpen} onClose={() => setCmdOpen(false)} />

      <div className={sysOpen ? undefined : "bp-sys-host"} aria-hidden={!sysOpen}>
        <BackendStatus forceOpen={sysOpen} onOpenChange={setSysOpen} hideTrigger />
      </div>
    </div>
  );
}
