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
        <p className="bp-eyebrow">功能入口</p>
        <h1 className="bp-page-title">{title}</h1>
        <p className="bp-page-subtitle">此入口暂未开放</p>
      </div>
      <div className="bp-panel">
        <div className="bp-pending-capability">
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <div style={{ maxWidth: 420, margin: "0 auto" }}>
                <div className="bp-pending-capability-title">{title}尚未开放</div>
                <div className="bp-pending-capability-desc">{description}</div>
              </div>
            }
          >
            <Link to="/">
              <Button>返回工作台</Button>
            </Link>
          </Empty>
        </div>
      </div>
    </div>
  );
}
