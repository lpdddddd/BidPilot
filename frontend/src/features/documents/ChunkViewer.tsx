import { useState } from "react";
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Pagination,
  Skeleton,
  Tooltip,
  Typography,
} from "antd";
import { useQuery } from "@tanstack/react-query";
import { getChunkSummary, listDocumentChunks } from "../../api/client";
import type { ChunkItem, DocumentItem } from "../../types/api";

const PAGE_SIZE = 10;

function pageRangeLabel(chunk: ChunkItem): string {
  if (chunk.page_start == null || chunk.page_end == null) {
    return "无可靠页码";
  }
  return chunk.page_start === chunk.page_end
    ? `第 ${chunk.page_start} 页`
    : `第 ${chunk.page_start}-${chunk.page_end} 页`;
}

function ChunkCard({ chunk }: { chunk: ChunkItem }) {
  const meta = chunk.metadata_json;
  const overlapChars = meta?.overlap_prefix_chars ?? 0;
  const hashShort = chunk.content_hash ? chunk.content_hash.slice(0, 12) : null;

  return (
    <div className="bp-chunk-card">
      <div className="bp-evidence-source" style={{ marginBottom: 10 }}>
        <span className="bp-rank" style={{ minWidth: 22, height: 22, fontSize: 11 }}>
          #{chunk.chunk_index}
        </span>
        <span>{chunk.section ? `章节 ${chunk.section}` : "未识别章节"}</span>
        {chunk.clause_id && <span>条款 {chunk.clause_id}</span>}
        <span>{pageRangeLabel(chunk)}</span>
        {chunk.token_count != null && <span>{chunk.token_count} tokens</span>}
        {overlapChars > 0 && (
          <Tooltip title={`前 ${overlapChars} 个字符与上一 Chunk 结尾重叠，用于保持上下文连续`}>
            <span>重叠 {overlapChars}</span>
          </Tooltip>
        )}
        {hashShort && (
          <Tooltip title={`内容 SHA-256：${chunk.content_hash}`}>
            <code style={{ fontSize: 11 }}>{hashShort}</code>
          </Tooltip>
        )}
      </div>
      {meta?.section_path && meta.section_path.length > 1 && (
        <div style={{ marginBottom: 8, fontSize: 12, color: "var(--bp-text-muted)" }}>
          来源路径：{meta.section_path.join(" / ")}
        </div>
      )}
      <Typography.Paragraph
        className="bp-evidence-excerpt"
        ellipsis={{ rows: 4, expandable: true, symbol: "展开全文" }}
        style={{ marginBottom: 0 }}
      >
        {chunk.content}
      </Typography.Paragraph>
    </div>
  );
}

export default function ChunkViewer({
  projectId,
  document,
  onClose,
}: {
  projectId: string;
  document: DocumentItem | null;
  onClose: () => void;
}) {
  const [page, setPage] = useState(1);

  const summary = useQuery({
    queryKey: ["chunk-summary", projectId, document?.id],
    queryFn: () => getChunkSummary(projectId, document!.id),
    enabled: Boolean(document),
    retry: 0,
  });

  const chunks = useQuery({
    queryKey: ["chunks", projectId, document?.id, page],
    queryFn: () =>
      listDocumentChunks(projectId, document!.id, {
        skip: (page - 1) * PAGE_SIZE,
        limit: PAGE_SIZE,
      }),
    enabled: Boolean(document),
    retry: 0,
  });

  const handleClose = () => {
    setPage(1);
    onClose();
  };

  return (
    <Drawer
      title={document ? `Chunk 切分结果：${document.file_name}` : "Chunk 切分结果"}
      open={Boolean(document)}
      onClose={handleClose}
      width={760}
    >
      {summary.isLoading ? (
        <Skeleton active paragraph={{ rows: 3 }} />
      ) : summary.isError ? (
        <Alert
          type="error"
          showIcon
          message="无法加载 Chunk 概况"
          description={(summary.error as Error).message}
          action={
            <Button size="small" onClick={() => summary.refetch()}>
              重试
            </Button>
          }
        />
      ) : summary.data ? (
        <>
          {summary.data.status === "success" ? (
            <Alert
              type="success"
              showIcon
              style={{ marginBottom: 16 }}
              message="文本已按结构切分，可在下一步接入检索索引"
            />
          ) : (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
              message="尚未进入向量化与检索阶段"
              description={summary.data.error ?? undefined}
            />
          )}
          <Descriptions
            size="small"
            column={{ xs: 1, sm: 2, md: 3 }}
            style={{ marginBottom: 16 }}
            items={[
              { key: "count", label: "Chunk 总数", children: summary.data.chunk_count },
              { key: "sections", label: "已识别章节数", children: summary.data.section_count },
              {
                key: "tokens",
                label: "总 token 数",
                children: summary.data.total_tokens.toLocaleString(),
              },
              {
                key: "chunker",
                label: "Chunker 版本",
                children: summary.data.chunker_version
                  ? `${summary.data.chunker_name ?? ""} ${summary.data.chunker_version}`.trim()
                  : "-",
              },
              {
                key: "tokenizer",
                label: "Tokenizer",
                children: summary.data.tokenizer ?? "-",
              },
            ]}
          />
        </>
      ) : null}

      {chunks.isLoading ? (
        <Skeleton active paragraph={{ rows: 8 }} />
      ) : chunks.isError ? (
        <Alert
          type="error"
          showIcon
          message="无法加载 Chunk 列表"
          description={(chunks.error as Error).message}
          action={
            <Button size="small" onClick={() => chunks.refetch()}>
              重试
            </Button>
          }
        />
      ) : chunks.data ? (
        chunks.data.total === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="该文档尚未生成 Chunk"
            style={{ padding: "32px 0" }}
          />
        ) : (
          <>
            {chunks.data.items.map((chunk) => (
              <ChunkCard key={chunk.id} chunk={chunk} />
            ))}
            {chunks.data.total > PAGE_SIZE && (
              <div style={{ display: "flex", justifyContent: "center", marginTop: 16 }}>
                <Pagination
                  current={page}
                  pageSize={PAGE_SIZE}
                  total={chunks.data.total}
                  onChange={setPage}
                  showSizeChanger={false}
                />
              </div>
            )}
          </>
        )
      ) : null}
    </Drawer>
  );
}
