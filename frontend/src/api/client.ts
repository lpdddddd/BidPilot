import axios from "axios";

const baseURL = import.meta.env.VITE_API_BASE_URL || "";

export const api = axios.create({
  baseURL,
  timeout: 15000,
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const detail =
      error.response?.data?.detail ||
      error.message ||
      "请求失败，请稍后重试";
    return Promise.reject(new Error(typeof detail === "string" ? detail : JSON.stringify(detail)));
  },
);

export type Project = {
  id: string;
  organization_id: string;
  project_code: string;
  project_name: string;
  purchaser?: string | null;
  procurement_agency?: string | null;
  procurement_method?: string | null;
  industry?: string | null;
  region?: string | null;
  budget_cny?: string | null;
  price_ceiling_cny?: string | null;
  bid_deadline?: string | null;
  status: string;
  metadata_json?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
};

export type DocumentItem = {
  id: string;
  project_id: string;
  file_name: string;
  document_type: string;
  parse_status: string;
  mime_type?: string | null;
  created_at: string;
};

export async function listProjects() {
  const { data } = await api.get<{ items: Project[]; total: number }>("/api/v1/projects");
  return data;
}

export async function createProject(payload: {
  project_code: string;
  project_name: string;
  purchaser?: string;
  industry?: string;
  region?: string;
}) {
  const { data } = await api.post<Project>("/api/v1/projects", payload);
  return data;
}

export async function getProject(projectId: string) {
  const { data } = await api.get<Project>(`/api/v1/projects/${projectId}`);
  return data;
}

export async function listDocuments(projectId: string) {
  const { data } = await api.get<{ items: DocumentItem[]; total: number }>(
    `/api/v1/projects/${projectId}/documents`,
  );
  return data;
}
