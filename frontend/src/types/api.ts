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

export type ProjectListResponse = {
  items: Project[];
  total: number;
};

export type ProjectCreatePayload = {
  project_code: string;
  project_name: string;
  purchaser?: string;
  industry?: string;
  region?: string;
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

export type DocumentListResponse = {
  items: DocumentItem[];
  total: number;
};

export type HealthResponse = {
  status: "ok";
};

export type ServiceStatus = {
  name: string;
  status: "ok" | "error";
  detail?: string | null;
};

export type ReadyResponse = {
  status: "ok" | "degraded" | "error";
  services: ServiceStatus[];
};
