import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Form,
  InputNumber,
  Select,
  Space,
  Typography,
} from "antd";
import { useQuery } from "@tanstack/react-query";
import { listModels } from "../../api/client";
import type {
  EvaluationCapabilitiesResponse,
  EvaluationRunCreatePayload,
  EvaluationSuite,
} from "../../types/api";
import {
  BASE_MODEL_ID,
  modelSelectLabel,
  pickBaseModel,
  pickCourseLora,
} from "../models/modelStatus";
import {
  capabilityOptionLabel,
  evaluationTargetLabel,
  friendlyCapabilityReason,
} from "./evaluationParams";

type Props = {
  suites: EvaluationSuite[];
  capabilities: EvaluationCapabilitiesResponse | undefined;
  submitting: boolean;
  error: string | null;
  onSubmit: (payload: EvaluationRunCreatePayload, idempotencyKey: string) => void;
};

const DEFAULT_SPLITS = ["train", "validation", "test"];
const DEFAULT_FAMILIES = [
  "rag",
  "extraction",
  "matching",
  "compliance",
  "drafting",
  "agent",
];

const MODEL_SELECT_TARGETS = new Set(["rag", "agent_pipeline"]);

export default function NewEvaluationForm({
  suites,
  capabilities,
  submitting,
  error,
  onSubmit,
}: Props) {
  const [suiteId, setSuiteId] = useState<string>(suites[0]?.id ?? "");
  const [split, setSplit] = useState<string | undefined>(undefined);
  const [taskFamily, setTaskFamily] = useState<string | undefined>(undefined);
  const [targetType, setTargetType] = useState<string | undefined>(undefined);
  const [profile, setProfile] = useState<string | undefined>(
    capabilities?.profiles?.[0]?.id ?? capabilities?.evaluator_version,
  );
  const [seed, setSeed] = useState(42);
  const [caseLimit, setCaseLimit] = useState<number | null>(null);
  const [modelId, setModelId] = useState<string>(BASE_MODEL_ID);
  const lockRef = useRef(false);

  const modelsQuery = useQuery({
    queryKey: ["models"],
    queryFn: listModels,
    retry: 0,
    enabled: MODEL_SELECT_TARGETS.has(String(targetType ?? "")),
  });

  useEffect(() => {
    if (!suiteId && suites[0]?.id) {
      setSuiteId(suites[0].id);
    }
  }, [suiteId, suites]);

  useEffect(() => {
    if (!profile) {
      const next = capabilities?.profiles?.[0]?.id ?? capabilities?.evaluator_version;
      if (next) setProfile(next);
    }
  }, [capabilities, profile]);

  const caps = capabilities?.items ?? [];
  const splits = capabilities?.splits?.length ? capabilities.splits : DEFAULT_SPLITS;
  const families = capabilities?.task_families?.length
    ? capabilities.task_families
    : DEFAULT_FAMILIES;
  const profiles = capabilities?.profiles ?? [];

  const showModelSelect = MODEL_SELECT_TARGETS.has(String(targetType ?? ""));
  const baseModel = modelsQuery.data ? pickBaseModel(modelsQuery.data.items) : undefined;
  const loraModel = modelsQuery.data ? pickCourseLora(modelsQuery.data.items) : undefined;
  const selectedModel = useMemo(() => {
    const items = modelsQuery.data?.items ?? [];
    return items.find((m) => m.model_id === modelId) ?? baseModel;
  }, [modelsQuery.data, modelId, baseModel]);

  const modelOptions = useMemo(() => {
    const opts: { value: string; label: string; disabled?: boolean }[] = [];
    if (baseModel) {
      opts.push({
        value: baseModel.model_id,
        label: modelSelectLabel(baseModel),
        disabled: !baseModel.served,
      });
    } else {
      opts.push({ value: BASE_MODEL_ID, label: "Qwen3-8B Base", disabled: true });
    }
    if (loraModel) {
      opts.push({
        value: loraModel.model_id,
        label: modelSelectLabel(loraModel),
        disabled: !loraModel.served,
      });
    }
    return opts;
  }, [baseModel, loraModel]);

  useEffect(() => {
    if (!showModelSelect) return;
    if (selectedModel && !selectedModel.served) {
      const fallback = baseModel?.served
        ? baseModel.model_id
        : modelsQuery.data?.default_model_id || BASE_MODEL_ID;
      if (fallback !== modelId) setModelId(fallback);
    }
  }, [showModelSelect, selectedModel, baseModel, modelId, modelsQuery.data?.default_model_id]);

  const selectedCap = caps.find((c) => String(c.target_type) === targetType);
  const modelReady = !showModelSelect || Boolean(selectedModel?.served);
  const canSubmit =
    Boolean(suiteId && targetType && selectedCap?.available) &&
    modelReady &&
    !submitting &&
    !lockRef.current;

  const handleSubmit = () => {
    if (!suiteId || !targetType || !selectedCap?.available) return;
    if (showModelSelect && !selectedModel?.served) return;
    if (lockRef.current || submitting) return;
    lockRef.current = true;
    const key =
      typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : `eval-${Date.now()}`;
    const payload: EvaluationRunCreatePayload = {
      suite_id: suiteId,
      target: targetType,
      split: split || null,
      task_family: taskFamily || null,
      evaluator_profile: profile || null,
      seed,
      case_limit: caseLimit,
      ...(showModelSelect
        ? { target_config: { model_id: modelId } }
        : {}),
    };
    onSubmit(payload, key);
    // Unlock after a short debounce window; parent also gates on submitting.
    window.setTimeout(() => {
      lockRef.current = false;
    }, 800);
  };

  return (
    <div data-testid="eval-new-form" style={{ maxWidth: 640 }}>
      <Typography.Paragraph type="secondary">
        选择套件与目标后启动评测。暂未开放的目标会保持禁用并给出简要说明；不会在此展示 test
        reference。RAG / Agent 可选择 Base 或 Course LoRA（须已在线）。
      </Typography.Paragraph>

      {error && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message="创建评测失败"
          description={error}
          data-testid="eval-new-error"
        />
      )}

      <Form layout="vertical">
        <Form.Item label="评测套件" required>
          <Select
            data-testid="eval-suite-select"
            value={suiteId || undefined}
            placeholder="选择 suite"
            options={suites.map((s) => ({
              value: s.id,
              label: `${s.name} · v${s.version}`,
            }))}
            onChange={setSuiteId}
          />
        </Form.Item>

        <Form.Item label="Split">
          <Select
            allowClear
            data-testid="eval-split-select"
            value={split}
            placeholder="全部 split"
            options={splits.map((s) => ({ value: s, label: s }))}
            onChange={setSplit}
          />
        </Form.Item>

        <Form.Item label="Task Family">
          <Select
            allowClear
            data-testid="eval-family-select"
            value={taskFamily}
            placeholder="全部 task family"
            options={families.map((f) => ({ value: f, label: f }))}
            onChange={setTaskFamily}
          />
        </Form.Item>

        <Form.Item label="Target" required>
          <Select
            data-testid="eval-target-select"
            value={targetType}
            placeholder="选择评测目标"
            options={caps.map((c) => ({
              value: String(c.target_type),
              label: capabilityOptionLabel(c),
              disabled: !c.available,
            }))}
            onChange={setTargetType}
          />
          {selectedCap && !selectedCap.available && (
            <Typography.Text
              type="secondary"
              data-testid="eval-target-unavailable-reason"
              style={{ display: "block", marginTop: 8 }}
            >
              该目标暂不可用：{friendlyCapabilityReason(selectedCap)}
            </Typography.Text>
          )}
          {caps.length === 0 && (
            <Typography.Text type="secondary">
              暂无能力数据；请确认后端 evaluation-capabilities 可用。
            </Typography.Text>
          )}
        </Form.Item>

        {showModelSelect && (
          <Form.Item label="评测模型" required>
            <Select
              data-testid="eval-model-select"
              value={modelId}
              loading={modelsQuery.isLoading}
              options={modelOptions}
              onChange={setModelId}
            />
            {loraModel && !loraModel.served && (
              <Typography.Text
                type="secondary"
                data-testid="eval-lora-unserved-hint"
                style={{ display: "block", marginTop: 8 }}
              >
                模型尚未启动在线服务。请先用 Base，或启动带 --enable-lora 的 vLLM 后再选 Course
                LoRA。
              </Typography.Text>
            )}
            {showModelSelect && selectedModel && !selectedModel.served && (
              <Typography.Text type="danger" style={{ display: "block", marginTop: 8 }}>
                所选模型未在线，无法启动评测。
              </Typography.Text>
            )}
          </Form.Item>
        )}

        <Form.Item label="Evaluator Profile">
          <Select
            allowClear
            data-testid="eval-profile-select"
            value={profile}
            placeholder={capabilities?.evaluator_version || "默认 profile"}
            options={
              profiles.length
                ? profiles.map((p) => ({
                    value: p.id,
                    label: `${p.name || p.id} · ${p.version}`,
                  }))
                : capabilities?.evaluator_version
                  ? [
                      {
                        value: capabilities.evaluator_version,
                        label: capabilities.evaluator_version,
                      },
                    ]
                  : []
            }
            onChange={setProfile}
          />
        </Form.Item>

        <Space size="large" wrap>
          <Form.Item label="Seed">
            <InputNumber
              data-testid="eval-seed-input"
              min={0}
              value={seed}
              onChange={(v) => setSeed(typeof v === "number" ? v : 42)}
            />
          </Form.Item>
          <Form.Item label="Case 数量限制">
            <InputNumber
              data-testid="eval-limit-input"
              min={1}
              placeholder="全部"
              value={caseLimit ?? undefined}
              onChange={(v) => setCaseLimit(typeof v === "number" ? v : null)}
            />
          </Form.Item>
        </Space>

        <Space>
          <Button
            type="primary"
            data-testid="eval-start-btn"
            disabled={!canSubmit}
            loading={submitting}
            onClick={handleSubmit}
          >
            启动评测
          </Button>
          {targetType && (
            <Typography.Text type="secondary">
              目标：{evaluationTargetLabel(targetType)}
              {showModelSelect && modelId ? ` · 模型 ${modelId}` : ""}
            </Typography.Text>
          )}
        </Space>
      </Form>
    </div>
  );
}
