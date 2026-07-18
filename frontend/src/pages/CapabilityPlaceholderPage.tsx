import { Button, Empty } from "antd";
import { Link } from "react-router-dom";
import { usePageTitle } from "../components/usePageTitle";

export default function CapabilityPlaceholderPage({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  usePageTitle(title);

  return (
    <div>
      <div className="bp-page-header">
        <h1 className="bp-page-title">{title}</h1>
        <p className="bp-page-subtitle">能力建设中</p>
      </div>
      <div className="bp-panel">
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          style={{ padding: "64px 0" }}
          description={
            <div style={{ maxWidth: 420, margin: "0 auto" }}>
              <div style={{ fontWeight: 600, marginBottom: 8 }}>{title}尚未开放</div>
              <div style={{ color: "var(--bp-text-muted)", fontSize: 13, lineHeight: 1.7 }}>
                {description}
              </div>
            </div>
          }
        >
          <Link to="/">
            <Button>返回工作台</Button>
          </Link>
        </Empty>
      </div>
    </div>
  );
}
