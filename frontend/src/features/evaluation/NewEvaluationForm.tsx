import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Form,
  InputNumber,
  Select,
  Space,
  Typography,
} from "antd";
import type {
  EvaluationCapabilitiesResponse,
  EvaluationRunCreatePayload,
  EvaluationSuite,
} from "../../types/api";
import { capabilityOptionLabel, evaluationTargetLabel } from "./evaluationParams";

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
  const lockRef = useRef(false);

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

  const selectedCap = caps.find((c) => String(c.target_type) === targetType);
  const canSubmit =
    Boolean(suiteId && targetType && selectedCap?.available) && !submitting && !lockRef.current;

  const handleSubmit = () => {
    if (!suiteId || !targetType || !selectedCap?.available) return;
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
        选择套件与目标后启动评测。不可用目标已禁用并显示原因；不会在此展示 test reference。
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
            <Typography.Text type="danger" data-testid="eval-target-unavailable-reason">
              {selectedCap.reason || "当前不可用"}
            </Typography.Text>
          )}
          {caps.length === 0 && (
            <Typography.Text type="secondary">
              暂无能力数据；请确认后端 evaluation-capabilities 可用。
            </Typography.Text>
          )}
        </Form.Item>

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
            </Typography.Text>
          )}
        </Space>
      </Form>
    </div>
  );
}
