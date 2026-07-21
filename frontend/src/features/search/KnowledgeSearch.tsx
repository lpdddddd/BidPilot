import { useMemo, useState, type ReactNode } from "react";
import {
  Alert,
  Button,
  Collapse,
  Empty,
  Input,
  InputNumber,
  Select,
  Skeleton,
  Space,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { SearchOutlined } from "@ant-design/icons";
import { useMutation, useQuery } from "@tanstack/react-query";
import { listDocuments, searchProject } from "../../api/client";
import type { SearchResponse, SearchResultItem } from "../../types/api";

const TYPE_LABELS: Record<string, string> = {
  tender: "招标文件",
  announcement: "招标公告",
  amendment: "澄清/补遗",
  result: "中标结果",
  contract: "合同",
  company_profile: "企业资料",
  qualification: "资质文件",
  case: "业绩案例",
  personnel: "人员材料",
  product: "产品资料",
  other: "其他",
};

const DOCUMENT_TYPE_OPTIONS = Object.entries(TYPE_LABELS).map(([value, label]) => ({
  value,
  label,
}));

function hitChannel(item: SearchResultItem): string {
  if (item.dense_rank != null && item.bm25_rank != null) return "双路命中";
  if (item.dense_rank != null) return "Dense";
  return "BM25";
}

function pageRangeLabel(item: SearchResultItem): string {
  if (item.page_start == null || item.page_end == null) return "无可靠页码";
  return item.page_start === item.page_end
    ? `第 ${item.page_start} 页`
    : `第 ${item.page_start}-${item.page_end} 页`;
}

/** Highlight query tokens in excerpt text (display only; does not alter retrieval). */
function highlightQuery(text: string, query: string): ReactNode {
  const tokens = query
    .trim()
    .split(/\s+/)
    .filter((t) => t.length >= 2)
    .slice(0, 8);
  if (tokens.length === 0) return text;

  const escaped = tokens.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(`(${escaped.join("|")})`, "gi");
  const parts = text.split(pattern);
  if (parts.length === 1) return text;

  const lowerTokens = new Set(tokens.map((t) => t.toLowerCase()));
  return parts.map((part, i) =>
    lowerTokens.has(part.toLowerCase()) ? <mark key={i}>{part}</mark> : part,
  );
}

function EvidenceCard({
  item,
  query,
  onOpenSource,
}: {
  item: SearchResultItem;
  query: string;
  onOpenSource?: (documentId: string) => void;
}) {
  return (
    <article className="bp-evidence-card">
      <div className="bp-evidence-head">
        <span className="bp-rank" title="排名">
          {item.rank}
        </span>
        <div className="bp-evidence-main">
          <div className="bp-evidence-title-row">
            <span className="bp-evidence-filename">{item.file_name ?? "未知文件"}</span>
            {item.document_type && (
              <Tag bordered={false}>{TYPE_LABELS[item.document_type] ?? item.document_type}</Tag>
            )}
            <Tag bordered={false}>{hitChannel(item)}</Tag>
          </div>
          <div className="bp-evidence-source">
            {item.section && <span>章节 {item.section}</span>}
            {item.clause_id && <span>条款 {item.clause_id}</span>}
            <span>{pageRangeLabel(item)}</span>
          </div>
          <Typography.Paragraph
            className="bp-evidence-excerpt"
            ellipsis={{ rows: 5, expandable: true, symbol: "展开全文" }}
          >
            {highlightQuery(item.content, query)}
          </Typography.Paragraph>
          <div className="bp-evidence-actions">
            {onOpenSource && item.document_id ? (
              <Button type="link" size="small" onClick={() => onOpenSource(item.document_id)}>
                在文档中心查看
              </Button>
            ) : (
              <span />
            )}
            <Collapse
              className="bp-tech-details"
              ghost
              size="small"
              items={[
                {
                  key: "debug",
                  label: "检索技术详情",
                  children: (
                    <div className="bp-tech-grid">
                      <span>
                        rerank{" "}
                        {item.rerank_score != null ? item.rerank_score.toFixed(4) : "不可用"}
                      </span>
                      <span>rrf {item.rrf_score.toFixed(5)}</span>
                      <span>
                        dense{" "}
                        {item.dense_rank != null
                          ? `#${item.dense_rank} / ${item.dense_score?.toFixed(4)}`
                          : "未命中"}
                      </span>
                      <span>
                        bm25{" "}
                        {item.bm25_rank != null
                          ? `#${item.bm25_rank} / ${item.bm25_score?.toFixed(2)}`
                          : "未命中"}
                      </span>
                      {item.content_hash && (
                        <Tooltip title={item.content_hash}>
                          <code>hash {item.content_hash.slice(0, 12)}</code>
                        </Tooltip>
                      )}
                      <span>chunk {item.chunk_index ?? "-"}</span>
                    </div>
                  ),
                },
              ]}
            />
          </div>
        </div>
      </div>
    </article>
  );
}

export default function KnowledgeSearch({
  projectId,
  onOpenSource,
}: {
  projectId: string;
  onOpenSource?: (documentId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState<number>(8);
  const [documentTypes, setDocumentTypes] = useState<string[]>([]);
  const [documentIds, setDocumentIds] = useState<string[]>([]);
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [lastQuery, setLastQuery] = useState("");

  const documentsQuery = useQuery({
    queryKey: ["documents", projectId],
    queryFn: () => listDocuments(projectId),
  });

  const documentOptions = useMemo(
    () =>
      (documentsQuery.data?.items ?? []).map((doc) => ({
        value: doc.id,
        label: doc.file_name,
      })),
    [documentsQuery.data],
  );

  const searchMutation = useMutation({
    mutationFn: () =>
      searchProject(projectId, {
        query: query.trim(),
        top_k: topK,
        document_types: documentTypes,
        document_ids: documentIds,
      }),
    onSuccess: (data) => {
      setLastQuery(query.trim());
      setResponse(data);
    },
  });

  const canSearch = query.trim().length > 0 && !searchMutation.isPending;

  return (
    <div className="bp-search-shell">
      <div className="bp-search-head">
        <h2 className="bp-page-title" style={{ fontSize: 22, marginBottom: 0 }}>
          知识检索
        </h2>
        <p className="bp-page-subtitle">基于项目资料进行混合检索与来源定位。</p>
        <div className="bp-search-hint">
          <span className="bp-search-hint-dot" aria-hidden="true" />
          <span>当前仅返回检索证据，尚未生成模型回答。</span>
        </div>
      </div>

      <div className="bp-command">
        <Input
          prefix={<SearchOutlined style={{ color: "var(--bp-text-faint)" }} />}
          size="large"
          placeholder="检索项目资料中的证据，例如：投标人需要具备哪些资质？"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onPressEnter={() => canSearch && searchMutation.mutate()}
          allowClear
        />
        <Button
          className="bp-command-btn"
          type="primary"
          size="large"
          icon={<SearchOutlined />}
          disabled={!canSearch}
          loading={searchMutation.isPending}
          onClick={() => searchMutation.mutate()}
        >
          检索证据
        </Button>
      </div>

      <div className="bp-search-filters">
        <span className="bp-search-filters-label">筛选</span>
        <Select
          mode="multiple"
          allowClear
          placeholder="文档类型"
          style={{ minWidth: 160 }}
          options={DOCUMENT_TYPE_OPTIONS}
          value={documentTypes}
          onChange={setDocumentTypes}
          maxTagCount="responsive"
        />
        <Select
          mode="multiple"
          allowClear
          placeholder="指定文档"
          style={{ minWidth: 180 }}
          options={documentOptions}
          loading={documentsQuery.isLoading}
          value={documentIds}
          onChange={setDocumentIds}
          maxTagCount="responsive"
        />
        <Space size={6}>
          <span className="bp-search-filters-label">返回</span>
          <InputNumber min={1} max={20} value={topK} onChange={(v) => setTopK(v ?? 8)} />
        </Space>
        <div className="bp-search-tags">
          <span className="bp-pill">Hybrid Retrieval</span>
          <span className="bp-pill">Evidence First</span>
        </div>
      </div>

      {searchMutation.isPending ? (
        <Skeleton active paragraph={{ rows: 8 }} />
      ) : searchMutation.isError ? (
        <Alert
          type="error"
          showIcon
          message="检索失败"
          description={(searchMutation.error as Error).message}
          action={
            <Button size="small" onClick={() => searchMutation.mutate()}>
              重试
            </Button>
          }
        />
      ) : response ? (
        response.results.length === 0 ? (
          <div className="bp-empty-block">
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <div>
                  <div className="bp-empty-title">未找到相关证据</div>
                  <div className="bp-empty-desc">
                    请确认相关文档已完成解析、切分与索引，或换一组更具体的关键词。
                  </div>
                </div>
              }
            />
          </div>
        ) : (
          <>
            <div className="bp-search-summary">
              <span className="bp-search-summary-title">证据结果</span>
              <span className="bp-search-summary-meta">
                {response.trace.returned_count} 条 · {response.trace.latency.total_ms.toFixed(0)} ms
              </span>
            </div>
            {response.results.map((item) => (
              <EvidenceCard
                key={item.chunk_id}
                item={item}
                query={lastQuery}
                onOpenSource={onOpenSource}
              />
            ))}
            <Collapse
              className="bp-tech-details"
              ghost
              size="small"
              items={[
                {
                  key: "trace",
                  label: "检索技术详情",
                  children: (
                    <div className="bp-tech-grid">
                      <span>Dense 候选 {response.trace.dense_candidate_count}</span>
                      <span>BM25 候选 {response.trace.bm25_candidate_count}</span>
                      <span>融合候选 {response.trace.fused_candidate_count}</span>
                      <span>Embedding {response.trace.embedding_model}</span>
                      <span>
                        Reranker {response.trace.reranker_model ?? "不可用（已降级为 RRF）"}
                      </span>
                      <span>
                        embed {response.trace.latency.embed_ms.toFixed(0)} / 召回{" "}
                        {response.trace.latency.dense_ms.toFixed(0)} / rerank{" "}
                        {response.trace.latency.rerank_ms.toFixed(0)} ms
                      </span>
                      {response.trace.degraded.length > 0 && (
                        <Tag bordered={false} color="warning">
                          降级：{response.trace.degraded.join(", ")}
                        </Tag>
                      )}
                    </div>
                  ),
                },
              ]}
            />
          </>
        )
      ) : (
        <div className="bp-empty-block">
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <div>
                <div className="bp-empty-title">从问题开始检索证据</div>
                <div className="bp-empty-desc">
                  在已索引文档中混合检索，返回带章节与页码的原文片段，便于核对来源。
                </div>
              </div>
            }
          />
        </div>
      )}
    </div>
  );
}
