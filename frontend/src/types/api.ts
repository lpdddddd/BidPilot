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

export type ParseStatus =
  | "pending"
  | "processing"
  | "success"
  | "partial"
  | "ocr_required"
  | "failed";

export type DocumentMetadata = {
  parser_name?: string;
  parser_version?: string;
  parsed_at?: string;
  source_sha256?: string;
  extracted_text_storage_key?: string | null;
  extracted_characters?: number | null;
  parse_error?: string | null;
  original_file_name?: string;
};

export type DocumentItem = {
  id: string;
  project_id: string;
  organization_id: string;
  file_name: string;
  document_type: string;
  parse_status: ParseStatus;
  mime_type?: string | null;
  sha256?: string | null;
  file_size?: number | null;
  page_count?: number | null;
  is_scanned: boolean;
  metadata_json?: DocumentMetadata | null;
  created_at: string;
  updated_at: string;
};

export type DocumentListResponse = {
  items: DocumentItem[];
  total: number;
};

export type DocumentPreviewResponse = {
  document_id: string;
  parse_status: ParseStatus;
  page_count: number | null;
  extracted_characters: number | null;
  preview: string;
  truncated: boolean;
  max_chars: number;
};

export type DocumentDownloadResponse = {
  download_url: string;
  expires_in_seconds: number;
  file_name: string;
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
