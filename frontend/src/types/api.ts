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

export type ChunkingStatus = "pending" | "processing" | "success" | "failed";

export type ChunkingMeta = {
  status: ChunkingStatus;
  chunk_count?: number;
  total_tokens?: number;
  section_count?: number;
  chunker_name?: string;
  chunker_version?: string;
  tokenizer?: string | null;
  source_sha256?: string;
  error?: string | null;
  completed_at?: string | null;
};

export type IndexingStatus = "pending" | "processing" | "success" | "failed";

export type IndexingMeta = {
  status: IndexingStatus;
  indexed_chunk_count?: number;
  embedding_model?: string | null;
  embedding_dimension?: number | null;
  qdrant_collection?: string | null;
  opensearch_index?: string | null;
  error?: string | null;
  completed_at?: string | null;
};

export type DocumentMetadata = {
  parser_name?: string;
  parser_version?: string;
  parsed_at?: string;
  source_sha256?: string;
  extracted_text_storage_key?: string | null;
  page_index_storage_key?: string | null;
  extracted_characters?: number | null;
  parse_error?: string | null;
  original_file_name?: string;
  chunking?: ChunkingMeta | null;
  indexing?: IndexingMeta | null;
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

export type ChunkMetadata = {
  chunker_name?: string;
  chunker_version?: string;
  tokenizer?: string;
  source_sha256?: string;
  source_char_start?: number;
  source_char_end?: number;
  core_char_start?: number;
  core_char_end?: number;
  overlap_prefix_chars?: number;
  section_path?: string[];
  heading_level?: number | null;
  chunk_kind?: string;
  extracted_text_storage_key?: string;
};

export type ChunkItem = {
  id: string;
  document_id: string;
  project_id: string;
  chunk_index: number;
  section: string | null;
  clause_id: string | null;
  page_start: number | null;
  page_end: number | null;
  content: string;
  content_hash: string | null;
  token_count: number | null;
  metadata_json: ChunkMetadata | null;
  qdrant_point_id: string | null;
  created_at: string;
};

export type ChunkListResponse = {
  items: ChunkItem[];
  total: number;
};

export type ChunkSummaryResponse = {
  document_id: string;
  status: string;
  chunk_count: number;
  section_count: number;
  total_tokens: number;
  chunker_name: string | null;
  chunker_version: string | null;
  tokenizer: string | null;
  error: string | null;
  completed_at: string | null;
};

export type IndexSummaryResponse = {
  document_id: string;
  status: string;
  indexed_chunk_count: number;
  embedding_model: string | null;
  embedding_dimension: number | null;
  qdrant_collection: string | null;
  opensearch_index: string | null;
  error: string | null;
  completed_at: string | null;
};

export type SearchRequestPayload = {
  query: string;
  top_k?: number;
  document_types?: string[];
  document_ids?: string[];
};

export type SearchResultItem = {
  rank: number;
  chunk_id: string;
  document_id: string;
  file_name: string | null;
  document_type: string | null;
  chunk_index: number | null;
  section: string | null;
  clause_id: string | null;
  page_start: number | null;
  page_end: number | null;
  content: string;
  content_hash: string | null;
  source_sha256: string | null;
  chunker_version: string | null;
  dense_rank: number | null;
  dense_score: number | null;
  bm25_rank: number | null;
  bm25_score: number | null;
  rrf_score: number;
  rerank_score: number | null;
};

export type RetrievalTrace = {
  dense_candidate_count: number;
  bm25_candidate_count: number;
  fused_candidate_count: number;
  returned_count: number;
  embedding_model: string;
  reranker_model: string | null;
  qdrant_collection: string;
  opensearch_index: string;
  rrf_k: number;
  latency: {
    embed_ms: number;
    dense_ms: number;
    bm25_ms: number;
    fusion_ms: number;
    rerank_ms: number;
    total_ms: number;
  };
  degraded: string[];
};

export type SearchResponse = {
  query: string;
  results: SearchResultItem[];
  trace: RetrievalTrace;
};

export type CitationItem = {
  source_id: string;
  chunk_id: string;
  document_id: string;
  file_name: string | null;
  document_type: string | null;
  section: string | null;
  clause_id: string | null;
  page_start: number | null;
  page_end: number | null;
  excerpt: string;
  content_hash: string | null;
  rerank_score: number | null;
  rrf_score: number | null;
  dense_rank: number | null;
  dense_score: number | null;
  bm25_rank: number | null;
  bm25_score: number | null;
  chunk_index: number | null;
  document_url: string | null;
};

export type RagRetrievalTrace = RetrievalTrace & {
  rag_prepare_ms: number;
  context_chunk_count: number;
  context_token_count: number;
  filtered_by_min_score: number;
};

export type GenerationTrace = {
  model: string;
  context_chunk_count: number;
  context_token_count: number;
  latency_ms: number;
  finish_reason: string | null;
  request_id: string | null;
};

export type AskRequestPayload = {
  question: string;
  document_types?: string[];
  document_ids?: string[];
  top_k?: number;
  stream?: boolean;
};

export type AskResponse = {
  question: string;
  answer: string;
  citations: CitationItem[];
  sources: CitationItem[];
  retrieval_trace: RagRetrievalTrace;
  generation_trace: GenerationTrace | null;
  status: "answered" | "insufficient_evidence" | "llm_disabled";
};

export type LlmHealthResponse = {
  status: "ok" | "disabled" | "error";
  enabled: boolean;
  model: string;
  base_url: string;
  reachable: boolean;
  detail: string | null;
  latency_ms: number | null;
};

export type AskStreamHandlers = {
  onRetrieval?: (data: {
    sources: CitationItem[];
    retrieval_trace: RagRetrievalTrace;
    status: "ok" | "insufficient_evidence";
  }) => void;
  /** Progress only. Never carries unvalidated answer text (Scheme A). */
  onGenerationStarted?: (data: {
    request_id?: string;
    model?: string;
    context_chunk_count?: number;
    message?: string;
  }) => void;
  /**
   * @deprecated Backend no longer emits user-visible delta before citation
   * validation. Kept for forward-compat; UI must not render it as final answer.
   */
  onDelta?: (text: string) => void;
  onFinal?: (result: AskResponse) => void;
  onError?: (error: { message: string; detail?: unknown }) => void;
};

export type ReindexResponse = {
  project_id: string;
  scheduled_document_count: number;
  document_ids: string[];
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

export type ExtractionRunStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export type RequirementCategory =
  | "project_info"
  | "qualification"
  | "commercial"
  | "technical"
  | "scoring"
  | "material"
  | "deadline"
  | "mandatory"
  | "invalid_bid"
  | "contract";

export type RiskLevel = "low" | "medium" | "high" | "critical";

export type QualityLevel = "gold" | "silver" | "pending";

export type ReviewStatus = "reviewed" | "auto_checked" | "unreviewed";

export type ExtractionStartPayload = {
  document_ids?: string[];
  document_types?: string[];
  force?: boolean;
};

export type ExtractionRun = {
  id: string;
  project_id: string;
  status: ExtractionRunStatus;
  document_ids_json?: unknown[] | null;
  document_types_json?: unknown[] | null;
  total_chunks: number;
  processed_chunks: number;
  candidate_count: number;
  created_count: number;
  merged_count: number;
  conflict_count: number;
  failed_chunk_count: number;
  error_summary?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  config_json?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
};

export type EvidenceLink = {
  id: string;
  requirement_id: string;
  document_id?: string | null;
  chunk_id?: string | null;
  evidence_type?: string | null;
  confidence?: string | number | null;
  notes?: string | null;
  created_at: string;
  updated_at: string;
  document_file_name?: string | null;
  document_type?: string | null;
  chunk_index?: number | null;
  section?: string | null;
  clause_id?: string | null;
  page_start?: number | null;
  page_end?: number | null;
  document_center_path?: string | null;
};

export type RequirementSummary = {
  id: string;
  project_id: string;
  source_document_id?: string | null;
  requirement_code?: string | null;
  category: RequirementCategory;
  title: string;
  normalized_requirement?: string | null;
  mandatory: boolean;
  score?: string | number | null;
  risk_level: RiskLevel;
  source_page?: number | null;
  source_section?: string | null;
  source_clause_id?: string | null;
  quality_level: QualityLevel;
  review_status: ReviewStatus;
  metadata_json?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  evidence_count: number;
  has_conflict: boolean;
  source_document_file_name?: string | null;
};

export type RequirementListParams = {
  category?: RequirementCategory;
  mandatory?: boolean;
  risk_level?: RiskLevel;
  review_status?: ReviewStatus;
  source_document_id?: string;
  has_conflict?: boolean;
  page?: number;
  limit?: number;
  offset?: number;
};

export type RequirementListResponse = {
  items: RequirementSummary[];
  total: number;
  page: number;
  limit: number;
  offset: number;
};

export type RequirementDetail = RequirementSummary & {
  evidence_required_json?: Record<string, unknown> | unknown[] | null;
  evidence_links: EvidenceLink[];
};

export type EvidenceMatchStatus =
  | "supported"
  | "partially_supported"
  | "insufficient_evidence"
  | "conflicting_evidence"
  | "not_applicable";

export type MatchStartPayload = {
  requirement_ids?: string[];
  document_ids?: string[];
  document_types?: string[];
  force?: boolean;
};

export type MatchRun = {
  id: string;
  project_id: string;
  status: ExtractionRunStatus;
  requirement_ids_json?: unknown[] | null;
  document_ids_json?: unknown[] | null;
  document_types_json?: unknown[] | null;
  total_requirements: number;
  processed_requirements: number;
  matched_count: number;
  partial_count: number;
  missing_evidence_count: number;
  conflict_count: number;
  failed_requirement_count: number;
  protected_requirement_count?: number;
  skipped_reviewed_requirement_count?: number;
  error_summary?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  config_json?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
};

export type MatchReviewStatus =
  | "pending"
  | "confirmed"
  | "rejected"
  | "needs_more_material";

export type MatchReviewAction =
  | "confirm"
  | "reject"
  | "needs_more_material"
  | "reopen";

export type MatchReviewReasonCode =
  | "evidence_insufficient"
  | "evidence_incorrect"
  | "status_incorrect"
  | "scope_unclear"
  | "needs_updated_material"
  | "other";

export type ActorAuthn = "authenticated" | "unverified_local_operator";

export type MatchReview = {
  id: string;
  project_id: string;
  match_id: string;
  action: MatchReviewAction;
  from_review_status: MatchReviewStatus;
  to_review_status: MatchReviewStatus;
  comment?: string | null;
  reason_code?: MatchReviewReasonCode | null;
  actor_id?: string | null;
  actor_label: string;
  actor_authn: ActorAuthn;
  idempotency_key?: string | null;
  created_at: string;
  updated_at: string;
};

export type CompanyEvidenceLink = {
  id: string;
  match_id: string;
  document_id?: string | null;
  chunk_id?: string | null;
  quote?: string | null;
  notes?: string | null;
  role: string;
  created_at: string;
  updated_at: string;
  document_file_name?: string | null;
  document_type?: string | null;
  chunk_index?: number | null;
  section?: string | null;
  clause_id?: string | null;
  page_start?: number | null;
  page_end?: number | null;
  document_center_path?: string | null;
};

export type MatchSummary = {
  id: string;
  project_id: string;
  requirement_id: string;
  status: EvidenceMatchStatus;
  confidence?: string | number | null;
  summary?: string | null;
  needs_review: boolean;
  risk_level: RiskLevel;
  primary_company_document_id?: string | null;
  primary_company_chunk_id?: string | null;
  primary_company_quote?: string | null;
  metadata_json?: Record<string, unknown> | null;
  review_status?: MatchReviewStatus;
  reviewed_at?: string | null;
  reviewed_by?: string | null;
  is_review_protected?: boolean;
  review_lock_version?: number;
  lifecycle_status?: string;
  superseded_by_match_id?: string | null;
  supersedes_match_id?: string | null;
  created_at: string;
  updated_at: string;
  requirement?: RequirementSummary | null;
  primary_company_document_file_name?: string | null;
  primary_company_document_type?: string | null;
  document_center_path?: string | null;
  recent_reviews?: MatchReview[];
};

export type MatchListParams = {
  requirement_id?: string;
  status?: EvidenceMatchStatus;
  risk_level?: RiskLevel;
  category?: RequirementCategory;
  mandatory?: boolean;
  needs_review?: boolean;
  review_status?: MatchReviewStatus;
  source_document_id?: string;
  page?: number;
  limit?: number;
  offset?: number;
};

export type MatchListResponse = {
  items: MatchSummary[];
  total: number;
  page: number;
  limit: number;
  offset: number;
};

export type MatchDetail = MatchSummary & {
  tender_evidence_links: EvidenceLink[];
  company_links: CompanyEvidenceLink[];
  requirement_category?: RequirementCategory | null;
  requirement_mandatory?: boolean | null;
};

export type MatchReviewRequest = {
  action: Exclude<MatchReviewAction, "reopen">;
  actor_label: string;
  comment?: string | null;
  reason_code?: MatchReviewReasonCode | null;
  review_lock_version: number;
};

export type MatchReopenRequest = {
  actor_label: string;
  comment: string;
  review_lock_version: number;
};

export type ReviewQueueCounts = {
  pending: number;
  confirmed: number;
  rejected: number;
  needs_more_material: number;
  total: number;
  by_match_status?: Record<string, number>;
  by_risk_level?: Record<string, number>;
};

export type ReviewQueueItem = {
  id: string;
  project_id: string;
  requirement_id: string;
  status: EvidenceMatchStatus;
  review_status: MatchReviewStatus;
  risk_level: RiskLevel;
  needs_review: boolean;
  is_review_protected: boolean;
  review_lock_version: number;
  lifecycle_status: string;
  summary?: string | null;
  reviewed_at?: string | null;
  reviewed_by?: string | null;
  requirement_title?: string | null;
  requirement_code?: string | null;
  requirement_category?: RequirementCategory | null;
  requirement_risk_level?: RiskLevel | null;
  has_conflict?: boolean;
  has_scope_exclusion?: boolean;
  source_run_id?: string | null;
  superseded_by_match_id?: string | null;
  supersedes_match_id?: string | null;
  last_reviewer?: string | null;
  last_reviewed_at?: string | null;
  detail_id?: string | null;
  created_at: string;
  updated_at: string;
};

export type ReviewQueueParams = {
  review_status?: MatchReviewStatus | "all";
  match_status?: EvidenceMatchStatus;
  status?: EvidenceMatchStatus;
  risk_level?: RiskLevel;
  requirement_category?: RequirementCategory;
  category?: RequirementCategory;
  has_conflict?: boolean;
  has_scope_exclusion?: boolean;
  include_superseded?: boolean;
  requirement_id?: string;
  page?: number;
  page_size?: number;
  limit?: number;
  offset?: number;
  sort?: string;
};

export type ReviewQueueResponse = {
  counts: ReviewQueueCounts;
  items: ReviewQueueItem[];
  total: number;
  page: number;
  limit: number;
  offset: number;
  include_superseded?: boolean;
};

export type MatchReviewListResponse = {
  items: MatchReview[];
  total: number;
};
