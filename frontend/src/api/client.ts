import { http } from "./http";
import type {
  ChunkListResponse,
  ChunkSummaryResponse,
  ComplianceFindingListParams,
  ComplianceFindingListResponse,
  ComplianceReport,
  ComplianceRuleListResponse,
  ComplianceRun,
  ComplianceStartPayload,
  DocumentDownloadResponse,
  DocumentItem,
  DocumentListResponse,
  DocumentPreviewResponse,
  ExtractionRun,
  ExtractionStartPayload,
  HealthResponse,
  IndexSummaryResponse,
  MatchDetail,
  MatchListParams,
  MatchListResponse,
  MatchReopenRequest,
  MatchReviewListResponse,
  MatchReviewRequest,
  MatchRun,
  MatchStartPayload,
  Project,
  ProjectCreatePayload,
  ProjectListResponse,
  ProposalDraftCreatePayload,
  ProposalDraftDetail,
  ProposalDraftEligibilityResponse,
  ProposalDraftListResponse,
  ProposalDraftManualRevisionPayload,
  ProposalDraftReopenPayload,
  ProposalDraftReviewPayload,
  ProposalDraftRun,
  ProposalDraftVersionDetail,
  ProposalDraftVersionListResponse,
  ReadyResponse,
  ReindexResponse,
  RequirementDetail,
  RequirementListParams,
  RequirementListResponse,
  ReviewQueueParams,
  ReviewQueueResponse,
  SearchRequestPayload,
  SearchResponse,
} from "../types/api";

export async function getHealth(): Promise<HealthResponse> {
  const { data } = await http.get<HealthResponse>("/health");
  return data;
}

export type ModelCatalogStatusLabel =
  | "online"
  | "adapter_ready"
  | "registered"
  | "unavailable";

export type ModelCatalogItem = {
  model_id: string;
  display_name: string;
  model_type: "base" | "lora";
  registered: boolean;
  adapter_exists: boolean;
  /** Live vLLM probe — only true when the served name is online. */
  served: boolean;
  served_model_name: string | null;
  version: string | null;
  train_track: string | null;
  reason_codes: string[];
  reason_code?: string | null;
  base_model_match?: string | null;
  configured_base_model?: string | null;
  adapter_base_model?: string | null;
  last_probe_at?: string | null;
  capabilities?: string[];
  notes: string | null;
  status_label: ModelCatalogStatusLabel;
};

export type ModelCatalogResponse = {
  llm_enabled: boolean;
  default_model_id: string;
  active_finetune_model_id: string | null;
  items: ModelCatalogItem[];
};

export type ActiveModelInfo = {
  llm_enabled: boolean;
  served_model: string;
  base_model_source?: string | null;
  provider?: string | null;
  train_track?: string | null;
  version?: string | null;
  notes?: string | null;
  default_model_id?: string;
  models?: ModelCatalogItem[];
  active_finetune?: {
    model_id?: string;
    display_name?: string;
    train_track?: string;
    version?: string;
    base_model?: string;
    adapter_name?: string;
    metrics?: Record<string, unknown>;
    notes?: string;
    registered?: boolean;
    adapter_exists?: boolean;
    served?: boolean;
    status_label?: ModelCatalogStatusLabel;
    reason_codes?: string[];
  } | null;
};

export async function getActiveModel(): Promise<ActiveModelInfo> {
  const { data } = await http.get<ActiveModelInfo>("/api/v1/models/active");
  return data;
}

export async function listModels(): Promise<ModelCatalogResponse> {
  const { data } = await http.get<ModelCatalogResponse>("/api/v1/models");
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

export async function buildDocumentChunks(
  projectId: string,
  documentId: string,
): Promise<DocumentItem> {
  const { data } = await http.post<DocumentItem>(
    `/api/v1/projects/${projectId}/documents/${documentId}/chunk`,
  );
  return data;
}

export async function listDocumentChunks(
  projectId: string,
  documentId: string,
  params: { skip?: number; limit?: number } = {},
): Promise<ChunkListResponse> {
  const { data } = await http.get<ChunkListResponse>(
    `/api/v1/projects/${projectId}/documents/${documentId}/chunks`,
    { params },
  );
  return data;
}

export async function getChunkSummary(
  projectId: string,
  documentId: string,
): Promise<ChunkSummaryResponse> {
  const { data } = await http.get<ChunkSummaryResponse>(
    `/api/v1/projects/${projectId}/documents/${documentId}/chunk-summary`,
  );
  return data;
}

export async function buildDocumentIndex(
  projectId: string,
  documentId: string,
): Promise<DocumentItem> {
  const { data } = await http.post<DocumentItem>(
    `/api/v1/projects/${projectId}/documents/${documentId}/index`,
  );
  return data;
}

export async function getIndexSummary(
  projectId: string,
  documentId: string,
): Promise<IndexSummaryResponse> {
  const { data } = await http.get<IndexSummaryResponse>(
    `/api/v1/projects/${projectId}/documents/${documentId}/index-summary`,
  );
  return data;
}

export async function searchProject(
  projectId: string,
  payload: SearchRequestPayload,
): Promise<SearchResponse> {
  const { data } = await http.post<SearchResponse>(
    `/api/v1/projects/${projectId}/search`,
    payload,
    { timeout: 60000 },
  );
  return data;
}

export async function reindexProject(projectId: string): Promise<ReindexResponse> {
  const { data } = await http.post<ReindexResponse>(`/api/v1/projects/${projectId}/reindex`);
  return data;
}

export async function startRequirementExtraction(
  projectId: string,
  payload: ExtractionStartPayload = {},
): Promise<ExtractionRun> {
  const { data } = await http.post<ExtractionRun>(
    `/api/v1/projects/${projectId}/requirements/extractions`,
    payload,
    { timeout: 60000 },
  );
  return data;
}

export type StructuredClauseTaskType =
  | "requirement_classify"
  | "qualification_extract"
  | "scoring_extract"
  | "risk_detect"
  | "project_info_extract";

export type StructuredClauseRequest = {
  clause_text: string;
  task_type?: StructuredClauseTaskType;
  model_id?: string;
  allow_base_fallback?: boolean;
  temperature?: number;
  max_tokens?: number;
};

export type StructuredClauseResponse = {
  id?: string | null;
  project_id?: string | null;
  task_type: string;
  clause_text: string;
  raw_output: string;
  parsed: Record<string, unknown> | null;
  schema_valid: boolean;
  required_field_coverage: number;
  missing_fields: string[];
  parse_error: string | null;
  requested_model_id: string;
  resolved_model_id: string | null;
  served_model_name: string | null;
  model_type: string | null;
  adapter_version: string | null;
  dataset_version: string;
  fallback_used: boolean;
  latency_ms: number;
  capability: string;
  created_at?: string | null;
};

export type StructuredClauseListResponse = {
  items: StructuredClauseResponse[];
  total: number;
};

export async function analyzeStructuredClause(
  projectId: string,
  payload: StructuredClauseRequest,
): Promise<StructuredClauseResponse> {
  const { data } = await http.post<StructuredClauseResponse>(
    `/api/v1/projects/${projectId}/requirements/structured-analyses`,
    payload,
    { timeout: 180000 },
  );
  return data;
}

export async function listStructuredClauseAnalyses(
  projectId: string,
  params?: { limit?: number; offset?: number },
): Promise<StructuredClauseListResponse> {
  const { data } = await http.get<StructuredClauseListResponse>(
    `/api/v1/projects/${projectId}/requirements/structured-analyses`,
    { params },
  );
  return data;
}

export async function getRequirementExtractionRun(
  projectId: string,
  runId: string,
): Promise<ExtractionRun> {
  const { data } = await http.get<ExtractionRun>(
    `/api/v1/projects/${projectId}/requirements/extractions/${runId}`,
  );
  return data;
}

export async function listRequirements(
  projectId: string,
  params: RequirementListParams = {},
): Promise<RequirementListResponse> {
  const { data } = await http.get<RequirementListResponse>(
    `/api/v1/projects/${projectId}/requirements`,
    { params },
  );
  return data;
}

export async function getRequirement(
  projectId: string,
  requirementId: string,
): Promise<RequirementDetail> {
  const { data } = await http.get<RequirementDetail>(
    `/api/v1/projects/${projectId}/requirements/${requirementId}`,
  );
  return data;
}

export async function startRequirementMatching(
  projectId: string,
  payload: MatchStartPayload = {},
): Promise<MatchRun> {
  const { data } = await http.post<MatchRun>(
    `/api/v1/projects/${projectId}/requirement-matches/runs`,
    payload,
    { timeout: 60000 },
  );
  return data;
}

export async function getRequirementMatchRun(
  projectId: string,
  runId: string,
): Promise<MatchRun> {
  const { data } = await http.get<MatchRun>(
    `/api/v1/projects/${projectId}/requirement-matches/runs/${runId}`,
  );
  return data;
}

export async function cancelRequirementMatchRun(
  projectId: string,
  runId: string,
): Promise<MatchRun> {
  const { data } = await http.post<MatchRun>(
    `/api/v1/projects/${projectId}/requirement-matches/runs/${runId}/cancel`,
    {},
    { timeout: 60000 },
  );
  return data;
}

export async function listRequirementMatches(
  projectId: string,
  params: MatchListParams = {},
): Promise<MatchListResponse> {
  const { data } = await http.get<MatchListResponse>(
    `/api/v1/projects/${projectId}/requirement-matches`,
    { params },
  );
  return data;
}

export async function getRequirementMatch(
  projectId: string,
  matchId: string,
): Promise<MatchDetail> {
  const { data } = await http.get<MatchDetail>(
    `/api/v1/projects/${projectId}/requirement-matches/${matchId}`,
  );
  return data;
}

export async function getRequirementMatchReviewQueue(
  projectId: string,
  params: ReviewQueueParams = {},
): Promise<ReviewQueueResponse> {
  const { data } = await http.get<ReviewQueueResponse>(
    `/api/v1/projects/${projectId}/requirement-matches/review-queue`,
    { params },
  );
  return data;
}

export async function listRequirementMatchReviews(
  projectId: string,
  matchId: string,
): Promise<MatchReviewListResponse> {
  const { data } = await http.get<MatchReviewListResponse>(
    `/api/v1/projects/${projectId}/requirement-matches/${matchId}/reviews`,
  );
  return data;
}

export async function reviewRequirementMatch(
  projectId: string,
  matchId: string,
  payload: MatchReviewRequest,
  idempotencyKey?: string,
): Promise<MatchDetail> {
  const { data } = await http.post<MatchDetail>(
    `/api/v1/projects/${projectId}/requirement-matches/${matchId}/review`,
    payload,
    {
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
      timeout: 60000,
    },
  );
  return data;
}

export async function reopenRequirementMatch(
  projectId: string,
  matchId: string,
  payload: MatchReopenRequest,
  idempotencyKey?: string,
): Promise<MatchDetail> {
  const { data } = await http.post<MatchDetail>(
    `/api/v1/projects/${projectId}/requirement-matches/${matchId}/reopen`,
    payload,
    {
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
      timeout: 60000,
    },
  );
  return data;
}

export async function getProposalDraftEligibility(
  projectId: string,
): Promise<ProposalDraftEligibilityResponse> {
  const { data } = await http.get<ProposalDraftEligibilityResponse>(
    `/api/v1/projects/${projectId}/proposal-drafts/eligibility`,
  );
  return data;
}

export async function listProposalDrafts(
  projectId: string,
): Promise<ProposalDraftListResponse> {
  const { data } = await http.get<ProposalDraftListResponse>(
    `/api/v1/projects/${projectId}/proposal-drafts`,
  );
  return data;
}

export async function createProposalDraft(
  projectId: string,
  payload: ProposalDraftCreatePayload,
  idempotencyKey?: string,
): Promise<ProposalDraftRun> {
  const { data } = await http.post<ProposalDraftRun>(
    `/api/v1/projects/${projectId}/proposal-drafts`,
    payload,
    {
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
      timeout: 60000,
    },
  );
  return data;
}

export async function getProposalDraft(
  projectId: string,
  draftId: string,
): Promise<ProposalDraftDetail> {
  const { data } = await http.get<ProposalDraftDetail>(
    `/api/v1/projects/${projectId}/proposal-drafts/${draftId}`,
  );
  return data;
}

export async function listProposalDraftVersions(
  projectId: string,
  draftId: string,
): Promise<ProposalDraftVersionListResponse> {
  const { data } = await http.get<ProposalDraftVersionListResponse>(
    `/api/v1/projects/${projectId}/proposal-drafts/${draftId}/versions`,
  );
  return data;
}

export async function getProposalDraftVersion(
  projectId: string,
  draftId: string,
  versionId: string,
): Promise<ProposalDraftVersionDetail> {
  const { data } = await http.get<ProposalDraftVersionDetail>(
    `/api/v1/projects/${projectId}/proposal-drafts/${draftId}/versions/${versionId}`,
  );
  return data;
}

export async function createProposalDraftManualRevision(
  projectId: string,
  draftId: string,
  payload: ProposalDraftManualRevisionPayload,
  idempotencyKey?: string,
): Promise<ProposalDraftDetail> {
  const { data } = await http.post<ProposalDraftDetail>(
    `/api/v1/projects/${projectId}/proposal-drafts/${draftId}/manual-revisions`,
    payload,
    {
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
      timeout: 60000,
    },
  );
  return data;
}

export async function reviewProposalDraft(
  projectId: string,
  draftId: string,
  payload: ProposalDraftReviewPayload,
  idempotencyKey?: string,
): Promise<ProposalDraftDetail> {
  const { data } = await http.post<ProposalDraftDetail>(
    `/api/v1/projects/${projectId}/proposal-drafts/${draftId}/review`,
    payload,
    {
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
      timeout: 60000,
    },
  );
  return data;
}

export async function reopenProposalDraft(
  projectId: string,
  draftId: string,
  payload: ProposalDraftReopenPayload,
  idempotencyKey?: string,
): Promise<ProposalDraftDetail> {
  const { data } = await http.post<ProposalDraftDetail>(
    `/api/v1/projects/${projectId}/proposal-drafts/${draftId}/reopen`,
    payload,
    {
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
      timeout: 60000,
    },
  );
  return data;
}

export async function getProposalDraftRun(
  projectId: string,
  runId: string,
): Promise<ProposalDraftRun> {
  const { data } = await http.get<ProposalDraftRun>(
    `/api/v1/projects/${projectId}/proposal-draft-runs/${runId}`,
  );
  return data;
}

export async function cancelProposalDraftRun(
  projectId: string,
  runId: string,
): Promise<ProposalDraftRun> {
  const { data } = await http.post<ProposalDraftRun>(
    `/api/v1/projects/${projectId}/proposal-draft-runs/${runId}/cancel`,
    {},
    { timeout: 60000 },
  );
  return data;
}

export function proposalDraftExportUrl(
  projectId: string,
  draftId: string,
  format: "markdown" | "docx",
): string {
  return `/api/v1/projects/${projectId}/proposal-drafts/${draftId}/export?format=${format}`;
}

export async function listComplianceRules(
  projectId?: string,
): Promise<ComplianceRuleListResponse> {
  const path = projectId
    ? `/api/v1/projects/${projectId}/compliance/rules`
    : `/api/v1/projects/compliance/rules`;
  const { data } = await http.get<ComplianceRuleListResponse>(path);
  return data;
}

export async function startComplianceRun(
  projectId: string,
  payload: ComplianceStartPayload = {},
  idempotencyKey?: string,
): Promise<ComplianceReport> {
  const { data } = await http.post<ComplianceReport>(
    `/api/v1/projects/${projectId}/compliance/runs`,
    payload,
    {
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
      timeout: 120000,
    },
  );
  return data;
}

export async function startDraftComplianceRun(
  projectId: string,
  draftId: string,
  payload: ComplianceStartPayload = {},
  idempotencyKey?: string,
): Promise<ComplianceReport> {
  const { data } = await http.post<ComplianceReport>(
    `/api/v1/projects/${projectId}/proposal-drafts/${draftId}/compliance/runs`,
    payload,
    {
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
      timeout: 120000,
    },
  );
  return data;
}

export async function getComplianceRun(
  projectId: string,
  runId: string,
): Promise<ComplianceRun> {
  const { data } = await http.get<ComplianceRun>(
    `/api/v1/projects/${projectId}/compliance/runs/${runId}`,
  );
  return data;
}

export async function getComplianceReport(
  projectId: string,
  runId: string,
): Promise<ComplianceReport> {
  const { data } = await http.get<ComplianceReport>(
    `/api/v1/projects/${projectId}/compliance/runs/${runId}/report`,
  );
  return data;
}

export async function getLatestCompliance(
  projectId: string,
): Promise<ComplianceReport | null> {
  const { data } = await http.get<ComplianceReport | null>(
    `/api/v1/projects/${projectId}/compliance/latest`,
  );
  return data;
}

export async function listComplianceFindings(
  projectId: string,
  params: ComplianceFindingListParams = {},
): Promise<ComplianceFindingListResponse> {
  const { data } = await http.get<ComplianceFindingListResponse>(
    `/api/v1/projects/${projectId}/compliance/findings`,
    { params },
  );
  return data;
}

export { askProject, askProjectStream, getLlmHealth } from "./ask";
