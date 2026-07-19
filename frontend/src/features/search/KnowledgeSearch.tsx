import { useMemo, useState } from "react";
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

function hitChannel(item: SearchResultItem): { label: string; color: string } {
  if (item.dense_rank != null && item.bm25_rank != null) {
    return { label: "双路命中", color: "purple" };
  }
  if (item.dense_rank != null) {
    return { label: "Dense 命中", color: "cyan" };
  }
  return { label: "BM25 命中", color: "blue" };
}

function pageRangeLabel(item: SearchResultItem): string {
  if (item.page_start == null || item.page_end == null) return "无可靠页码";
  return item.page_start === item.page_end
    ? `第 ${item.page_start} 页`
    : `第 ${item.page_start}–${item.page_end} 页`;
}

function ResultCard({
  item,
  onOpenSource,
}: {
  item: SearchResultItem;
  onOpenSource?: (documentId: string) => void;
}) {
  const channel = hitChannel(item);
  return (
    <div
      style={{
        border: "1px solid var(--bp-border)",
        borderRadius: 8,
        padding: "14px 16px",
        marginBottom: 12,
        background: "var(--bp-surface)",
      }}
    >
      <Space size={8} wrap style={{ marginBottom: 8 }}>
        <Tag color="blue">#{item.rank}</Tag>
        <Typography.Text strong>{item.file_name ?? "未知文件"}</Typography.Text>
        {item.document_type && <Tag>{TYPE_LABELS[item.document_type] ?? item.document_type}</Tag>}
        {item.section && <Tag color="geekblue">{item.section}</Tag>}
        {item.clause_id && <Tag color="geekblue">{item.clause_id}</Tag>}
        <Tag color={item.page_start != null ? "cyan" : "default"}>{pageRangeLabel(item)}</Tag>
        <Tag color={channel.color}>{channel.label}</Tag>
        {onOpenSource && item.document_id && (
          <Button
            type="link"
            size="small"
            style={{ padding: 0 }}
            onClick={() => onOpenSource(item.document_id)}
          >
            在文档中心查看
          </Button>
        )}
      </Space>
      <Typography.Paragraph
        ellipsis={{ rows: 4, expandable: true, symbol: "展开全文" }}
        style={{ whiteSpace: "pre-wrap", marginBottom: 8, fontSize: 13, lineHeight: 1.7 }}
      >
        {item.content}
      </Typography.Paragraph>
      <Collapse
        ghost
        size="small"
        items={[
          {
            key: "debug",
            label: (
              <span style={{ fontSize: 12, color: "var(--bp-text-muted)" }}>检索调试信息</span>
            ),
            children: (
              <Space size={12} wrap style={{ fontSize: 12 }}>
                <span>rerank_score：{item.rerank_score != null ? item.rerank_score.toFixed(4) : "重排不可用"}</span>
                <span>rrf_score：{item.rrf_score.toFixed(5)}</span>
                <span>
                  dense：{item.dense_rank != null ? `rank ${item.dense_rank} / ${item.dense_score?.toFixed(4)}` : "未命中"}
                </span>
                <span>
                  bm25：{item.bm25_rank != null ? `rank ${item.bm25_rank} / ${item.bm25_score?.toFixed(2)}` : "未命中"}
                </span>
                {item.content_hash && (
                  <Tooltip title={item.content_hash}>
                    <span style={{ fontFamily: "monospace" }}>
                      hash {item.content_hash.slice(0, 12)}
                    </span>
                  </Tooltip>
                )}
                <span>chunk_index：{item.chunk_index ?? "-"}</span>
              </Space>
            ),
          },
        ]}
      />
    </div>
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
    onSuccess: setResponse,
  });

  const canSearch = query.trim().length > 0 && !searchMutation.isPending;

  return (
    <div>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="当前为混合检索结果，尚未调用大模型生成回答。"
        description="Dense 向量与 BM25 关键词并行召回，经 RRF 融合与 Cross-Encoder 重排后返回原文片段与来源。"
      />
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        <Input
          style={{ flex: "1 1 360px", minWidth: 280 }}
          size="large"
          placeholder="例如：投标人需要具备哪些资质？"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onPressEnter={() => canSearch && searchMutation.mutate()}
          allowClear
        />
        <Button
          type="primary"
          size="large"
          icon={<SearchOutlined />}
          disabled={!canSearch}
          loading={searchMutation.isPending}
          onClick={() => searchMutation.mutate()}
        >
          检索
        </Button>
      </div>
      <Space size={12} wrap style={{ marginBottom: 16 }}>
        <Select
          mode="multiple"
          allowClear
          placeholder="文档类型（全部）"
          style={{ minWidth: 200 }}
          options={DOCUMENT_TYPE_OPTIONS}
          value={documentTypes}
          onChange={setDocumentTypes}
        />
        <Select
          mode="multiple"
          allowClear
          placeholder="指定文档（全部）"
          style={{ minWidth: 240 }}
          options={documentOptions}
          loading={documentsQuery.isLoading}
          value={documentIds}
          onChange={setDocumentIds}
        />
        <span style={{ fontSize: 13, color: "var(--bp-text-muted)" }}>
          返回数量
          <InputNumber
            min={1}
            max={20}
            value={topK}
            onChange={(v) => setTopK(v ?? 8)}
            style={{ width: 64, marginLeft: 8 }}
          />
        </span>
      </Space>

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
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            style={{ padding: "32px 0" }}
            description={
              <div>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>未检索到相关内容</div>
                <div style={{ color: "var(--bp-text-muted)", fontSize: 13 }}>
                  请确认相关文档已完成解析、切分与索引，或尝试其他关键词
                </div>
              </div>
            }
          />
        ) : (
          <>
            {response.results.map((item) => (
              <ResultCard key={item.chunk_id} item={item} onOpenSource={onOpenSource} />
            ))}
            <Collapse
              size="small"
              items={[
                {
                  key: "trace",
                  label: "检索过程（真实统计）",
                  children: (
                    <Space size={16} wrap style={{ fontSize: 13 }}>
                      <span>Dense 候选：{response.trace.dense_candidate_count}</span>
                      <span>BM25 候选：{response.trace.bm25_candidate_count}</span>
                      <span>融合候选：{response.trace.fused_candidate_count}</span>
                      <span>返回：{response.trace.returned_count}</span>
                      <span>Embedding：{response.trace.embedding_model}</span>
                      <span>Reranker：{response.trace.reranker_model ?? "不可用（已降级为 RRF 排序）"}</span>
                      <span>总耗时：{response.trace.latency.total_ms.toFixed(0)} ms</span>
                      <span>
                        （embed {response.trace.latency.embed_ms.toFixed(0)} / 召回{" "}
                        {response.trace.latency.dense_ms.toFixed(0)} / rerank{" "}
                        {response.trace.latency.rerank_ms.toFixed(0)} ms）
                      </span>
                      {response.trace.degraded.length > 0 && (
                        <Tag color="orange">降级：{response.trace.degraded.join(", ")}</Tag>
                      )}
                    </Space>
                  ),
                },
              ]}
            />
          </>
        )
      ) : (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          style={{ padding: "32px 0" }}
          description={
            <div>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>输入问题开始检索</div>
              <div style={{ color: "var(--bp-text-muted)", fontSize: 13 }}>
                在已完成索引的文档范围内进行混合检索，返回带章节与页码的原文片段
              </div>
            </div>
          }
        />
      )}
    </div>
  );
}
