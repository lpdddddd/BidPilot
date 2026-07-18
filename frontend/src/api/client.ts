import { http } from "./http";
import type {
  DocumentListResponse,
  HealthResponse,
  Project,
  ProjectCreatePayload,
  ProjectListResponse,
  ReadyResponse,
} from "../types/api";

export async function getHealth(): Promise<HealthResponse> {
  const { data } = await http.get<HealthResponse>("/health");
  return data;
}

export async function getReady(): Promise<ReadyResponse> {
  const { data } = await http.get<ReadyResponse>("/ready");
  return data;
}

export async function listProjects(): Promise<ProjectListResponse> {
  const { data } = await http.get<ProjectListResponse>("/api/v1/projects");
  return data;
}

export async function createProject(payload: ProjectCreatePayload): Promise<Project> {
  const { data } = await http.post<Project>("/api/v1/projects", payload);
  return data;
}

export async function getProject(projectId: string): Promise<Project> {
  const { data } = await http.get<Project>(`/api/v1/projects/${projectId}`);
  return data;
}

export async function listDocuments(projectId: string): Promise<DocumentListResponse> {
  const { data } = await http.get<DocumentListResponse>(
    `/api/v1/projects/${projectId}/documents`,
  );
  return data;
}
