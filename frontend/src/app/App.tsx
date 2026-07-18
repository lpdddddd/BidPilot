import { Suspense, lazy } from "react";
import { Route, Routes } from "react-router-dom";
import { Skeleton } from "antd";
import WorkbenchLayout from "../layouts/WorkbenchLayout";

const DashboardPage = lazy(() => import("../pages/DashboardPage"));
const ProjectListPage = lazy(() => import("../pages/ProjectListPage"));
const ProjectDetailPage = lazy(() => import("../pages/ProjectDetailPage"));
const CapabilityPlaceholderPage = lazy(() => import("../pages/CapabilityPlaceholderPage"));
const NotFoundPage = lazy(() => import("../pages/NotFoundPage"));

function RouteFallback() {
  return (
    <div className="bp-panel">
      <Skeleton active paragraph={{ rows: 6 }} />
    </div>
  );
}

export default function App() {
  return (
    <WorkbenchLayout>
      <Suspense fallback={<RouteFallback />}>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/projects" element={<ProjectListPage />} />
          <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
          <Route
            path="/knowledge"
            element={
              <CapabilityPlaceholderPage
                title="知识库"
                description="文档解析、切块与向量入库能力将在第 3～5 步接入。届时可在此检索招标文件原文与证据片段。"
              />
            }
          />
          <Route
            path="/review"
            element={
              <CapabilityPlaceholderPage
                title="智能审查"
                description="基于规则与 Agent 工作流的合规审查能力将在第 6～10 步接入。届时可在此查看条款风险与证据溯源。"
              />
            }
          />
          <Route
            path="/evaluation"
            element={
              <CapabilityPlaceholderPage
                title="评估中心"
                description="检索与生成质量的自动评估能力将在后续步骤接入。届时可在此查看评测集与指标报告。"
              />
            }
          />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </Suspense>
    </WorkbenchLayout>
  );
}
