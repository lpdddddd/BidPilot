import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Alert,
  Button,
  Collapse,
  Empty,
  Input,
  InputNumber,
  Select,
  Space,
  Tag,
  Typography,
} from "antd";
import { SendOutlined, StopOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import { askProjectStream, getLlmHealth, listDocuments, listModels } from "../../api/client";
import { ApiError } from "../../api/http";
import type { AskResponse, CitationItem, RagRetrievalTrace } from "../../types/api";
import {
  BASE_MODEL_ID,
  CAP_GROUNDED_QA,
  formatAskGenerationModelLine,
  modelHasCapability,
  modelSelectLabel,
  pickBaseModel,
} from "../models/modelStatus";

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

function pageRangeLabel(item: { page_start: number | null; page_end: number | null }): string {
  if (item.page_start == null || item.page_end == null) return "无可靠页码";
  return item.page_start === item.page_end
    ? `第 ${item.page_start} 页`
    : `第 ${item.page_start}-${item.page_end} 页`;
}

function splitWithCitations(text: string, onCite: (id: string) => void): ReactNode[] {
  const parts = text.split(/(\[S\d+\])/g);
  return parts.map((part, i) => {
    const m = part.match(/^\[(S\d+)\]$/);
    if (m) {
      return (
        <button
          key={`${part}-${i}`}
          type="button"
          className="bp-cite-chip"
          onClick={() => onCite(m[1])}
        >
          {part}
        </button>
      );
    }
    return <span key={i}>{part}</span>;
  });
}

function AnswerMarkdown({
  text,
  onCite,
}: {
  text: string;
  onCite: (sourceId: string) => void;
}) {
  return (
    <div className="bp-answer-md">
      <ReactMarkdown
        components={{
          p: ({ children }) => {
            const flat = flattenText(children);
            if (flat.includes("[S")) {
              return <p>{splitWithCitations(flat, onCite)}</p>;
            }
            return <p>{children}</p>;
          },
          li: ({ children }) => {
            const flat = flattenText(children);
            if (flat.includes("[S")) {
              return <li>{splitWithCitations(flat, onCite)}</li>;
            }
            return <li>{children}</li>;
          },
          a: ({ children }) => <span>{children}</span>,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

function flattenText(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(flattenText).join("");
  if (typeof node === "object" && "props" in node) {
    return flattenText((node as { props: { children?: ReactNode } }).props.children);
  }
  return "";
}

function SourceCard({
  item,
  active,
  onOpenSource,
}: {
  item: CitationItem;
  active: boolean;
  onOpenSource?: (documentId: string, chunkId?: string) => void;
}) {
  return (
    <article
      id={`bp-source-${item.source_id}`}
      className={`bp-evidence-card${active ? " is-active" : ""}`}
    >
      <div className="bp-evidence-head">
        <span className="bp-rank">{item.source_id}</span>
        <div className="bp-evidence-main">
          <div className="bp-evidence-title-row">
            <span className="bp-evidence-filename">{item.file_name ?? "未知文件"}</span>
            {item.document_type && (
              <Tag bordered={false}>{TYPE_LABELS[item.document_type] ?? item.document_type}</Tag>
            )}
          </div>
          <div className="bp-evidence-source">
            {item.section && <span>章节 {item.section}</span>}
            {item.clause_id && <span>条款 {item.clause_id}</span>}
            <span>{pageRangeLabel(item)}</span>
          </div>
          <Typography.Paragraph className="bp-evidence-excerpt" ellipsis={{ rows: 4, expandable: true, symbol: "展开" }}>
            {item.excerpt}
          </Typography.Paragraph>
          {onOpenSource && item.document_id && (
            <Button
              type="link"
              size="small"
              onClick={() => onOpenSource(item.document_id, item.chunk_id)}
            >
              在文档中心查看
            </Button>
          )}
        </div>
      </div>
    </article>
  );
}

type Phase = "idle" | "retrieving" | "verifying" | "done" | "error";

export default function GroundedAsk({
  projectId,
  onOpenSource,
}: {
  projectId: string;
  onOpenSource?: (documentId: string, chunkId?: string) => void;
}) {
  const [question, setQuestion] = useState("");
  const [topK, setTopK] = useState(8);
  const [documentTypes, setDocumentTypes] = useState<string[]>([]);
  const [documentIds, setDocumentIds] = useState<string[]>([]);
  const [modelId, setModelId] = useState<string>(BASE_MODEL_ID);
  const [phase, setPhase] = useState<Phase>("idle");
  const [answerText, setAnswerText] = useState("");
  const [sources, setSources] = useState<CitationItem[]>([]);
  const [citations, setCitations] = useState<CitationItem[]>([]);
  const [trace, setTrace] = useState<RagRetrievalTrace | null>(null);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeSource, setActiveSource] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const sessionRef = useRef(0);

  const llmHealth = useQuery({
    queryKey: ["system", "llm-health"],
    queryFn: getLlmHealth,
    retry: 0,
    refetchInterval: 60_000,
  });

  const modelsQuery = useQuery({
    queryKey: ["models"],
    queryFn: listModels,
    retry: 0,
    refetchInterval: 60_000,
  });

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

  const baseModel = modelsQuery.data ? pickBaseModel(modelsQuery.data.items) : undefined;
  const groundedModels = (modelsQuery.data?.items ?? []).filter((m) =>
    modelHasCapability(m, CAP_GROUNDED_QA),
  );
  const selectedModel =
    groundedModels.find((m) => m.model_id === modelId) ?? baseModel;
  const modelOptions = useMemo(() => {
    if (!groundedModels.length) {
      return [{ value: BASE_MODEL_ID, label: "Qwen3-8B Base", disabled: false }];
    }
    return groundedModels.map((m) => ({
      value: m.model_id,
      label: modelSelectLabel(m),
      disabled: !m.served,
    }));
  }, [groundedModels]);

  useEffect(() => {
    if (selectedModel && !modelHasCapability(selectedModel, CAP_GROUNDED_QA)) {
      setModelId(baseModel?.model_id || modelsQuery.data?.default_model_id || BASE_MODEL_ID);
    }
  }, [selectedModel, baseModel, modelsQuery.data?.default_model_id]);

  useEffect(() => {
    const incompatible = modelsQuery.data?.items.find(
      (m) => m.model_id === modelId && !modelHasCapability(m, CAP_GROUNDED_QA),
    );
    if (incompatible) {
      setModelId(baseModel?.model_id || modelsQuery.data?.default_model_id || BASE_MODEL_ID);
    }
  }, [modelId, modelsQuery.data, baseModel]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const cancel = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    sessionRef.current += 1;
    setAnswerText("");
    setPhase((p) => (p === "retrieving" || p === "verifying" ? "idle" : p));
  };

  const submit = async () => {
    const q = question.trim();
    if (!q) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const session = ++sessionRef.current;

    setPhase("retrieving");
    setAnswerText("");
    setSources([]);
    setCitations([]);
    setTrace(null);
    setResult(null);
    setError(null);
    setActiveSource(null);

    try {
      await askProjectStream(
        projectId,
        {
          question: q,
          top_k: topK,
          document_types: documentTypes,
          document_ids: documentIds,
          model_id: modelId,
          allow_base_fallback: false,
          stream: true,
        },
        {
          onRetrieval: (data) => {
            if (session !== sessionRef.current) return;
            setSources(data.sources);
            setTrace(data.retrieval_trace);
            if (data.status === "insufficient_evidence") {
              setPhase("done");
            } else {
              setPhase("verifying");
            }
          },
          onGenerationStarted: () => {
            if (session !== sessionRef.current) return;
            setPhase("verifying");
            // Do not render model text until final citation validation.
            setAnswerText("");
          },
          onDelta: () => {
            // Intentionally ignored: unvalidated tokens must not become UI answer.
          },
          onFinal: (finalResult) => {
            if (session !== sessionRef.current) return;
            setResult(finalResult);
            setAnswerText(finalResult.answer);
            setCitations(finalResult.citations);
            setSources(finalResult.sources.length ? finalResult.sources : finalResult.citations);
            setTrace(finalResult.retrieval_trace);
            setPhase("done");
          },
          onError: (err) => {
            if (session !== sessionRef.current) return;
            setAnswerText("");
            setCitations([]);
            setResult(null);
            setError(err.message);
            setPhase("error");
          },
        },
        controller.signal,
      );
    } catch (err) {
      if (session !== sessionRef.current) return;
      if (controller.signal.aborted) return;
      const message = err instanceof ApiError ? err.message : (err as Error).message;
      setAnswerText("");
      setCitations([]);
      setResult(null);
      setError(message);
      setPhase("error");
    }
  };

  const busy = phase === "retrieving" || phase === "verifying";
  const displayCitations = citations.length ? citations : [];

  const scrollToSource = (sourceId: string) => {
    setActiveSource(sourceId);
    const el = document.getElementById(`bp-source-${sourceId}`);
    el?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  const modelLabel = llmHealth.data
    ? llmHealth.data.enabled
      ? llmHealth.data.reachable
        ? `${llmHealth.data.model} · 已连通`
        : `${llmHealth.data.model} · 未连通`
      : "问答未启用"
    : "检查模型状态…";

  return (
    <div className="bp-search-shell">
      <div className="bp-search-head">
        <h2 className="bp-page-title" style={{ fontSize: 22, marginBottom: 0 }}>
          带来源问答
        </h2>
        <p className="bp-page-subtitle">
          先混合检索本项目资料，再由模型仅依据本轮证据生成回答。
        </p>
        <div className="bp-search-hint">
          <span className="bp-search-hint-dot" aria-hidden="true" />
          <span>回答仅依据本次检索到的项目资料生成；回答将在来源校验完成后显示。</span>
        </div>
        <div className="bp-model-status" aria-live="polite">
          模型：{modelLabel}
          {llmHealth.data?.detail && !llmHealth.data.reachable && (
            <span className="bp-faint"> · {llmHealth.data.detail}</span>
          )}
        </div>
      </div>

      {llmHealth.isSuccess && (!llmHealth.data.enabled || !llmHealth.data.reachable) && (
        <Alert
          type="warning"
          showIcon
          message={
            llmHealth.data.enabled
              ? "大模型服务当前不可用"
              : "带来源问答未启用（LLM_ENABLED=false）"
          }
          description={
            llmHealth.data.enabled
              ? `无法连接推理服务（${llmHealth.data.base_url}）。请在「系统状态」确认服务后重试。`
              : "在 .env 中设置 LLM_ENABLED=true，并启动 Qwen3-8B（见 scripts/serve_qwen3_vllm.sh）。"
          }
        />
      )}

      <div className="bp-command">
        <Input
          size="large"
          placeholder="例如：投标人需要具备哪些资质？"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onPressEnter={() => !busy && question.trim() && submit()}
          allowClear
          disabled={busy}
        />
        {busy ? (
          <Button className="bp-command-btn" size="large" icon={<StopOutlined />} onClick={cancel}>
            停止
          </Button>
        ) : (
          <Button
            className="bp-command-btn"
            type="primary"
            size="large"
            icon={<SendOutlined />}
            disabled={!question.trim()}
            onClick={() => void submit()}
          >
            生成回答
          </Button>
        )}
      </div>

      <div className="bp-search-filters">
        <span className="bp-search-filters-label">筛选</span>
        <Select
          data-testid="ask-model-select"
          style={{ minWidth: 260 }}
          placeholder="选择模型"
          loading={modelsQuery.isLoading}
          options={modelOptions}
          value={modelId}
          onChange={setModelId}
          disabled={busy}
        />
        <Select
          mode="multiple"
          allowClear
          placeholder="文档类型"
          style={{ minWidth: 160 }}
          options={DOCUMENT_TYPE_OPTIONS}
          value={documentTypes}
          onChange={setDocumentTypes}
          maxTagCount="responsive"
          disabled={busy}
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
          disabled={busy}
        />
        <Space size={6}>
          <span className="bp-search-filters-label">证据数</span>
          <InputNumber
            min={1}
            max={20}
            value={topK}
            onChange={(v) => setTopK(v ?? 8)}
            disabled={busy}
          />
        </Space>
        <div className="bp-search-tags">
          <span className="bp-pill">证据约束</span>
          <span className="bp-pill">须带来源</span>
        </div>
      </div>
      <Typography.Text type="secondary" data-testid="ask-capability-hint" style={{ display: "block", marginBottom: 8 }}>
        带来源问答仅支持具备问答能力的基础模型。领域适配模型用于「要求」页的条款结构化分析，不在此可选。
      </Typography.Text>
      {selectedModel && !selectedModel.served && selectedModel.model_type === "base" && (
        <Typography.Text type="secondary" style={{ display: "block", marginBottom: 8 }}>
          基础模型当前不可用，请在「系统状态」确认推理服务后再提问。
        </Typography.Text>
      )}

      {phase === "retrieving" && (
        <div className="bp-ask-progress">正在检索项目资料…</div>
      )}
      {phase === "verifying" && (
        <div className="bp-ask-progress">
          正在生成并核验引用。确认前不会展示未经验证的模型正文。
        </div>
      )}

      {error && (
        <Alert
          type="error"
          showIcon
          message="问答失败"
          description={error}
          action={
            <Button size="small" onClick={() => void submit()}>
              重试
            </Button>
          }
        />
      )}

      {(answerText || result) && (
        <section className="bp-answer-panel">
          <div className="bp-search-summary">
            <span className="bp-search-summary-title">回答</span>
            {result?.generation_trace && (
              <span className="bp-search-summary-meta" data-testid="ask-generation-trace">
                {formatAskGenerationModelLine(result.generation_trace)}
                {" · "}
                {result.generation_trace.latency_ms.toFixed(0)} ms · 上下文{" "}
                {result.generation_trace.context_chunk_count} 条
              </span>
            )}
          </div>
          <AnswerMarkdown text={answerText} onCite={scrollToSource} />
        </section>
      )}

      {sources.length > 0 && (
        <section>
          <div className="bp-search-summary" style={{ marginTop: 8 }}>
            <span className="bp-search-summary-title">
              {displayCitations.length ? "引用的来源" : "本轮检索来源"}
            </span>
            <span className="bp-search-summary-meta">{sources.length} 条证据</span>
          </div>
          {(displayCitations.length ? displayCitations : sources).map((item) => (
            <SourceCard
              key={item.source_id}
              item={item}
              active={activeSource === item.source_id}
              onOpenSource={onOpenSource}
            />
          ))}
          {displayCitations.length > 0 &&
            sources.length > displayCitations.length &&
            sources
              .filter((s) => !displayCitations.some((c) => c.source_id === s.source_id))
              .map((item) => (
                <SourceCard
                  key={`ctx-${item.source_id}`}
                  item={item}
                  active={activeSource === item.source_id}
                  onOpenSource={onOpenSource}
                />
              ))}
        </section>
      )}

      {trace && (
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
                  <span>Dense {trace.dense_candidate_count}</span>
                  <span>BM25 {trace.bm25_candidate_count}</span>
                  <span>融合 {trace.fused_candidate_count}</span>
                  <span>上下文 {trace.context_chunk_count} / {trace.context_token_count} tok</span>
                  <span>低分过滤 {trace.filtered_by_min_score}</span>
                  <span>检索 {trace.latency.total_ms.toFixed(0)} ms</span>
                  {trace.degraded.length > 0 && (
                    <Tag bordered={false} color="warning">
                      降级：{trace.degraded.join(", ")}
                    </Tag>
                  )}
                </div>
              ),
            },
          ]}
        />
      )}

      {phase === "idle" && !answerText && !error && (
        <div className="bp-empty-block">
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <div>
                <div className="bp-empty-title">提出问题，获取带来源的回答</div>
                <div className="bp-empty-desc">
                  系统会先检索本项目证据，再生成带 [S1] 引用的回答；不会编造未检索到的条款。
                </div>
              </div>
            }
          />
        </div>
      )}
    </div>
  );
}
