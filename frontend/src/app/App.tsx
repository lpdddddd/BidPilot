import { Suspense, lazy } from "react";
import { Route, Routes } from "react-router-dom";
import { Skeleton } from "antd";
import WorkbenchLayout from "../layouts/WorkbenchLayout";

const DashboardPage = lazy(() => import("../pages/DashboardPage"));
const ProjectListPage = lazy(() => import("../pages/ProjectListPage"));
const ProjectDetailPage = lazy(() => import("../pages/ProjectDetailPage"));
const KnowledgeHubPage = lazy(() => import("../pages/KnowledgeHubPage"));
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
          <Route path="/knowledge" element={<KnowledgeHubPage />} />
          <Route path="/review" element={<ComplianceReviewPage />} />
          <Route path="/evaluation" element={<EvaluationCenterPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </Suspense>
    </WorkbenchLayout>
  );
}
