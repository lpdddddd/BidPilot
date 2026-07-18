import { http } from "./http";
import type {
  DocumentDownloadResponse,
  DocumentItem,
  DocumentListResponse,
  DocumentPreviewResponse,
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

export async function uploadDocument(
  projectId: string,
  file: File,
  options: {
    documentType?: string;
    onProgress?: (percent: number) => void;
  } = {},
): Promise<DocumentItem> {
  const form = new FormData();
  form.append("file", file);
  if (options.documentType) {
    form.append("document_type", options.documentType);
  }
  const { data } = await http.post<DocumentItem>(
    `/api/v1/projects/${projectId}/documents/upload`,
    form,
    {
      timeout: 120000,
      onUploadProgress: (event) => {
        if (options.onProgress && event.total) {
          options.onProgress(Math.round((event.loaded / event.total) * 100));
        }
      },
    },
  );
  return data;
}

export async function getDocumentPreview(
  projectId: string,
  documentId: string,
): Promise<DocumentPreviewResponse> {
  const { data } = await http.get<DocumentPreviewResponse>(
    `/api/v1/projects/${projectId}/documents/${documentId}/preview`,
  );
  return data;
}

export async function getDocumentDownload(
  projectId: string,
  documentId: string,
): Promise<DocumentDownloadResponse> {
  const { data } = await http.get<DocumentDownloadResponse>(
    `/api/v1/projects/${projectId}/documents/${documentId}/download`,
  );
  return data;
}

export async function reparseDocument(
  projectId: string,
  documentId: string,
): Promise<DocumentItem> {
  const { data } = await http.post<DocumentItem>(
    `/api/v1/projects/${projectId}/documents/${documentId}/reparse`,
  );
  return data;
}
