import { Button, Result } from "antd";
import { Link } from "react-router-dom";
import { usePageTitle } from "../components/usePageTitle";

export default function NotFoundPage() {
  usePageTitle("页面不存在");

  return (
    <div className="bp-panel">
      <Result
        status="404"
        title="404"
        subTitle="您访问的页面不存在或已被移动"
        extra={
          <Link to="/">
            <Button type="primary">返回工作台</Button>
          </Link>
        }
      />
    </div>
  );
}
