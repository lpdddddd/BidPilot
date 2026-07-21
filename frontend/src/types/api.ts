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
