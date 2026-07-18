import { useMemo, useState } from "react";
import {
  Alert,
  App as AntApp,
  Button,
  Drawer,
  Empty,
  Progress,
  Select,
  Skeleton,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from "antd";
import {
  BlockOutlined,
  DownloadOutlined,
  EyeOutlined,
  InboxOutlined,
  RedoOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  buildDocumentChunks,
  getDocumentDownload,
  getDocumentPreview,
  listDocuments,
  reparseDocument,
  uploadDocument,
} from "../../api/client";
import ChunkViewer from "./ChunkViewer";
import type { ChunkingStatus, DocumentItem, ParseStatus } from "../../types/api";

const MAX_UPLOAD_MB = 50;
const SUPPORTED_EXTENSIONS = ["pdf", "docx", "txt", "html", "htm", "xlsx"];

const DOCUMENT_TYPE_OPTIONS = [
  { value: "", label: "自动识别（按文件名）" },
  { value: "tender", label: "招标文件" },
  { value: "announcement", label: "招标公告" },
  { value: "amendment", label: "澄清/补遗" },
  { value: "result", label: "中标结果" },
  { value: "contract", label: "合同" },
  { value: "company_profile", label: "企业资料" },
  { value: "qualification", label: "资质文件" },
  { value: "case", label: "业绩案例" },
  { value: "personnel", label: "人员材料" },
  { value: "product", label: "产品资料" },
  { value: "other", label: "其他" },
];

const TYPE_LABELS: Record<string, string> = Object.fromEntries(
  DOCUMENT_TYPE_OPTIONS.filter((o) => o.value).map((o) => [o.value, o.label]),
);

const STATUS_CONFIG: Record<ParseStatus, { label: string; color: string }> = {
  pending: { label: "待解析", color: "default" },
  processing: { label: "解析中", color: "processing" },
  success: { label: "解析成功", color: "green" },
  partial: { label: "部分成功", color: "orange" },
  ocr_required: { label: "需要 OCR", color: "orange" },
  failed: { label: "解析失败", color: "red" },
};

const CHUNK_STATUS_CONFIG: Record<ChunkingStatus, { label: string; color: string }> = {
  pending: { label: "等待构建", color: "default" },
  processing: { label: "构建中", color: "processing" },
  success: { label: "已完成", color: "green" },
  failed: { label: "构建失败", color: "red" },
};

function formatBytes(size?: number | null): string {
  if (size == null) return "-";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function PreviewDrawer({
  projectId,
  document,
  onClose,
}: {
  projectId: string;
  document: DocumentItem | null;
  onClose: () => void;
}) {
  const preview = useQuery({
    queryKey: ["document-preview", projectId, document?.id],
    queryFn: () => getDocumentPreview(projectId, document!.id),
    enabled: Boolean(document),
    retry: 0,
  });

  return (
    <Drawer
      title={document ? `解析预览：${document.file_name}` : "解析预览"}
      open={Boolean(document)}
      onClose={onClose}
      width={720}
    >
      {preview.isLoading ? (
        <Skeleton active paragraph={{ rows: 10 }} />
      ) : preview.isError ? (
        <Alert
          type="error"
          showIcon
          message="无法加载解析预览"
          description={(preview.error as Error).message}
          action={
            <Button size="small" onClick={() => preview.refetch()}>
              重试
            </Button>
          }
        />
      ) : preview.data ? (
        <div>
          <Space size={16} wrap style={{ marginBottom: 12 }}>
            <Tag color="green">解析成功</Tag>
            {preview.data.page_count != null && <span>页数：{preview.data.page_count}</span>}
            {preview.data.extracted_characters != null && (
              <span>提取字符数：{preview.data.extracted_characters.toLocaleString()}</span>
            )}
          </Space>
          {preview.data.truncated && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message={`仅展示前 ${preview.data.max_chars.toLocaleString()} 个字符，完整文本已存入对象存储`}
            />
          )}
          <pre
            style={{
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              background: "var(--bp-surface)",
              border: "1px solid var(--bp-border)",
              borderRadius: 6,
              padding: 16,
              fontSize: 13,
              lineHeight: 1.7,
              maxHeight: "70vh",
              overflow: "auto",
            }}
          >
            {preview.data.preview}
          </pre>
        </div>
      ) : null}
    </Drawer>
  );
}

export default function DocumentCenter({ projectId }: { projectId: string }) {
  const { message } = AntApp.useApp();
  const queryClient = useQueryClient();
  const [documentType, setDocumentType] = useState<string>("");
  const [uploadPercent, setUploadPercent] = useState<number | null>(null);
  const [previewTarget, setPreviewTarget] = useState<DocumentItem | null>(null);
  const [chunkTarget, setChunkTarget] = useState<DocumentItem | null>(null);

  const query = useQuery({
    queryKey: ["documents", projectId],
    queryFn: () => listDocuments(projectId),
    refetchInterval: (q) => {
      const items = q.state.data?.items;
      const active = items?.some((item) => {
        const chunkStatus = item.metadata_json?.chunking?.status;
        return (
          item.parse_status === "pending" ||
          item.parse_status === "processing" ||
          chunkStatus === "pending" ||
          chunkStatus === "processing"
        );
      });
      return active ? 3000 : false;
    },
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) =>
      uploadDocument(projectId, file, {
        documentType: documentType || undefined,
        onProgress: setUploadPercent,
      }),
    onSuccess: (doc) => {
      message.success(`「${doc.file_name}」上传成功，已进入解析队列`);
      queryClient.invalidateQueries({ queryKey: ["documents", projectId] });
    },
    onError: (error: Error) => {
      message.error(error.message);
    },
    onSettled: () => setUploadPercent(null),
  });

  const reparseMutation = useMutation({
    mutationFn: (documentId: string) => reparseDocument(projectId, documentId),
    onSuccess: () => {
      message.success("已重新加入解析队列");
      queryClient.invalidateQueries({ queryKey: ["documents", projectId] });
    },
    onError: (error: Error) => message.error(error.message),
  });

  const downloadMutation = useMutation({
    mutationFn: (documentId: string) => getDocumentDownload(projectId, documentId),
    onSuccess: (data) => {
      window.open(data.download_url, "_blank", "noopener");
    },
    onError: (error: Error) => message.error(error.message),
  });

  const chunkMutation = useMutation({
    mutationFn: (documentId: string) => buildDocumentChunks(projectId, documentId),
    onSuccess: () => {
      message.success("已开始构建 Chunk");
      queryClient.invalidateQueries({ queryKey: ["documents", projectId] });
    },
    onError: (error: Error) => message.error(error.message),
  });

  const columns = useMemo(
    () => [
      {
        title: "文件名",
        dataIndex: "file_name",
        ellipsis: true,
        render: (value: string) => <Typography.Text>{value}</Typography.Text>,
      },
      {
        title: "类型",
        dataIndex: "document_type",
        width: 110,
        render: (value: string) => TYPE_LABELS[value] ?? value,
      },
      {
        title: "大小",
        dataIndex: "file_size",
        width: 90,
        render: (value: number | null) => formatBytes(value),
      },
      {
        title: "页数",
        dataIndex: "page_count",
        width: 70,
        render: (value: number | null) => value ?? "-",
      },
      {
        title: "解析状态",
        dataIndex: "parse_status",
        width: 110,
        render: (value: ParseStatus, row: DocumentItem) => {
          const config = STATUS_CONFIG[value] ?? { label: value, color: "default" };
          const error = row.metadata_json?.parse_error;
          const tag = <Tag color={config.color}>{config.label}</Tag>;
          return error ? <Tooltip title={error}>{tag}</Tooltip> : tag;
        },
      },
      {
        title: "解析结果",
        key: "parse_summary",
        width: 170,
        render: (_: unknown, row: DocumentItem) => {
          if (row.parse_status === "success") {
            const chars = row.metadata_json?.extracted_characters;
            return chars != null ? `${chars.toLocaleString()} 字符` : "已提取文本";
          }
          const error = row.metadata_json?.parse_error;
          if (error) {
            return (
              <Typography.Text type="secondary" ellipsis={{ tooltip: error }} style={{ maxWidth: 160 }}>
                {error}
              </Typography.Text>
            );
          }
          return "-";
        },
      },
      {
        title: "Chunk",
        key: "chunking",
        width: 190,
        render: (_: unknown, row: DocumentItem) => {
          const chunking = row.metadata_json?.chunking;
          if (!chunking) {
            return (
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                未构建
              </Typography.Text>
            );
          }
          const config = CHUNK_STATUS_CONFIG[chunking.status] ?? {
            label: chunking.status,
            color: "default",
          };
          const tag =
            chunking.status === "failed" && chunking.error ? (
              <Tooltip title={chunking.error}>
                <Tag color={config.color}>{config.label}</Tag>
              </Tooltip>
            ) : (
              <Tag color={config.color}>{config.label}</Tag>
            );
          return (
            <Space size={4} wrap>
              {tag}
              {chunking.status === "success" && chunking.chunk_count != null && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {chunking.chunk_count} 块 / {(chunking.total_tokens ?? 0).toLocaleString()}{" "}
                  tokens
                </Typography.Text>
              )}
            </Space>
          );
        },
      },
      {
        title: "上传时间",
        dataIndex: "created_at",
        width: 160,
        render: (value: string) => formatDateTime(value),
      },
      {
        title: "操作",
        key: "actions",
        width: 280,
        render: (_: unknown, row: DocumentItem) => {
          const chunking = row.metadata_json?.chunking;
          const chunkBuilt = chunking?.status === "success";
          const chunkBusy =
            chunking?.status === "pending" || chunking?.status === "processing";
          return (
            <Space size={4} wrap>
              <Button
                type="link"
                size="small"
                icon={<EyeOutlined />}
                disabled={row.parse_status !== "success"}
                onClick={() => setPreviewTarget(row)}
              >
                预览
              </Button>
              {row.parse_status === "success" && (
                <>
                  <Button
                    type="link"
                    size="small"
                    icon={<BlockOutlined />}
                    disabled={chunkBusy}
                    loading={chunkMutation.isPending && chunkMutation.variables === row.id}
                    onClick={() => chunkMutation.mutate(row.id)}
                  >
                    {chunkBuilt ? "重新构建" : "构建 Chunk"}
                  </Button>
                  {chunkBuilt && (
                    <Button type="link" size="small" onClick={() => setChunkTarget(row)}>
                      查看 Chunk
                    </Button>
                  )}
                </>
              )}
              <Button
                type="link"
                size="small"
                icon={<DownloadOutlined />}
                loading={downloadMutation.isPending && downloadMutation.variables === row.id}
                onClick={() => downloadMutation.mutate(row.id)}
              >
                下载
              </Button>
              {(row.parse_status === "failed" || row.parse_status === "ocr_required") && (
                <Button
                  type="link"
                  size="small"
                  icon={<RedoOutlined />}
                  loading={reparseMutation.isPending && reparseMutation.variables === row.id}
                  onClick={() => reparseMutation.mutate(row.id)}
                >
                  重新解析
                </Button>
              )}
            </Space>
          );
        },
      },
    ],
    [downloadMutation, reparseMutation, chunkMutation],
  );

  return (
    <div>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 16 }}>
        <div style={{ flex: "1 1 360px", minWidth: 300 }}>
          <Upload.Dragger
            multiple={false}
            showUploadList={false}
            disabled={uploadMutation.isPending}
            accept={SUPPORTED_EXTENSIONS.map((ext) => `.${ext}`).join(",")}
            customRequest={({ file }) => {
              uploadMutation.mutate(file as File);
            }}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽文件到此处上传</p>
            <p className="ant-upload-hint">
              支持 {SUPPORTED_EXTENSIONS.join(" / ")}，单个文件不超过 {MAX_UPLOAD_MB}MB。
              DOC/WPS 与扫描件暂不支持自动解析。
            </p>
            {uploadPercent != null && (
              <div style={{ padding: "0 24px" }}>
                <Progress percent={uploadPercent} size="small" />
              </div>
            )}
          </Upload.Dragger>
        </div>
        <div style={{ width: 220 }}>
          <div className="bp-stat-label">文档类型</div>
          <Select
            value={documentType}
            onChange={setDocumentType}
            options={DOCUMENT_TYPE_OPTIONS}
            style={{ width: "100%" }}
            aria-label="选择上传文档类型"
          />
          <Button
            icon={<ReloadOutlined />}
            style={{ marginTop: 12 }}
            onClick={() => query.refetch()}
            loading={query.isFetching && !query.isLoading}
          >
            刷新列表
          </Button>
        </div>
      </div>

      {query.isSuccess ? (
        query.data.items.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            style={{ padding: "32px 0" }}
            description={
              <div>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>暂无文档</div>
                <div style={{ color: "var(--bp-text-muted)", fontSize: 13 }}>
                  上传第一份招标文件，系统会自动提取文本并显示解析状态
                </div>
              </div>
            }
          />
        ) : (
          <Table<DocumentItem>
            rowKey="id"
            dataSource={query.data.items}
            columns={columns}
            pagination={query.data.items.length > 10 ? { pageSize: 10 } : false}
            scroll={{ x: 960 }}
            size="middle"
          />
        )
      ) : query.isError ? (
        <Alert
          type="error"
          showIcon
          message="文档列表加载失败"
          description={(query.error as Error).message}
          action={
            <Button size="small" onClick={() => query.refetch()}>
              重试
            </Button>
          }
        />
      ) : (
        <Skeleton active paragraph={{ rows: 5 }} />
      )}

      <PreviewDrawer
        projectId={projectId}
        document={previewTarget}
        onClose={() => setPreviewTarget(null)}
      />
      <ChunkViewer
        projectId={projectId}
        document={chunkTarget}
        onClose={() => setChunkTarget(null)}
      />
    </div>
  );
}
