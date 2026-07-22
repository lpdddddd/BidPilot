import { Suspense, lazy } from "react";
import { Route, Routes } from "react-router-dom";
import { Skeleton } from "antd";
import WorkbenchLayout from "../layouts/WorkbenchLayout";

const DashboardPage = lazy(() => import("../pages/DashboardPage"));
const ProjectListPage = lazy(() => import("../pages/ProjectListPage"));
const ProjectDetailPage = lazy(() => import("../pages/ProjectDetailPage"));
const CapabilityPlaceholderPage = lazy(() => import("../pages/CapabilityPlaceholderPage"));
const ComplianceReviewPage = lazy(
  () => import("../features/compliance/ComplianceReviewPage"),
);
const EvaluationCenterPage = lazy(
  () => import("../features/evaluation/EvaluationCenterPage"),
);
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
          <Route path="/review" element={<ComplianceReviewPage />} />
          <Route path="/evaluation" element={<EvaluationCenterPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </Suspense>
    </WorkbenchLayout>
  );
}
