import { Alert, Empty, Space, Spin, Typography } from "antd";
import type {
  EvaluationCapabilitiesResponse,
  EvaluationRun,
  EvaluationSuite,
} from "../../types/api";
import ScoreBar from "./ScoreBar";
import {
  errorRate,
  evaluationTargetLabel,
  formatPercent,
  formatScore,
  passRate,
  referenceCoverage,
  shortHash,
  taskFamilyScores,
} from "./evaluationParams";

function StatCard({
  label,
  value,
  testId,
}: {
  label: string;
  value: string | number;
  testId?: string;
}) {
  return (
    <div className="bp-panel" style={{ padding: 16, minWidth: 140 }} data-testid={testId}>
      <Typography.Text type="secondary">{label}</Typography.Text>
      <div style={{ fontSize: 24, fontWeight: 600, marginTop: 4 }}>{value}</div>
    </div>
  );
}

type Props = {
  loading: boolean;
  error: string | null;
  capabilities: EvaluationCapabilitiesResponse | undefined;
  suites: EvaluationSuite[];
  runs: EvaluationRun[];
  onOpenRun: (runId: string) => void;
};

export default function OverviewPanel({
  loading,
  error,
  capabilities,
  suites,
  runs,
  onOpenRun,
}: Props) {
  if (loading) {
    return (
      <div style={{ padding: 48, textAlign: "center" }} data-testid="eval-overview-loading">
        <Spin />
      </div>
    );
  }

  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        message="无法加载评测概览"
        description={error}
        data-testid="eval-overview-error"
      />
    );
  }

  const latest = runs[0] ?? null;
  const dataset = capabilities?.dataset;
  const suite = suites[0];
  const hash = dataset?.dataset_hash || suite?.dataset_hash;
  const version = dataset?.version || suite?.version;
  const availableTargets = (capabilities?.items ?? []).filter((c) => c.available);
  const trendRuns = runs.slice(0, 8).reverse();

  if (!latest && !dataset && suites.length === 0) {
    return (
      <div className="bp-empty-block" data-testid="eval-overview-empty">
        <div className="bp-empty-title">尚无评测记录</div>
        <div className="bp-empty-desc">
          选择项目后可查看评测套件与可用目标；创建首次评测后将在此展示得分与趋势。
        </div>
      </div>
    );
  }

  const familyScores = latest ? taskFamilyScores(latest) : {};

  return (
    <div data-testid="eval-overview" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="bp-tech-grid">
        <span>
          数据集 {dataset?.name || suite?.name || "—"} · v{version || "—"}
        </span>
        <span>hash {shortHash(hash)}</span>
        <span>评估器 {capabilities?.evaluator_version || suite?.evaluator_profile_version || "—"}</span>
        <span data-testid="eval-human-gold-count">
          人工金标准 {dataset?.human_gold_count ?? 0}（当前均为自动参考）
        </span>
      </div>

      {!latest ? (
        <Empty description="尚无评测运行，可在「新建评测」发起" data-testid="eval-overview-no-runs" />
      ) : (
        <>
          <Space wrap size="middle">
            <StatCard
              label="最近 Run"
              value={evaluationTargetLabel(String(latest.target_type))}
              testId="eval-stat-latest"
            />
            <StatCard
              label="Overall Score"
              value={formatScore(latest.overall_score ?? latest.summary_json?.overall_score)}
              testId="eval-stat-score"
            />
            <StatCard
              label="Pass Rate"
              value={formatPercent(passRate(latest))}
              testId="eval-stat-pass"
            />
            <StatCard
              label="Error Rate"
              value={formatPercent(errorRate(latest))}
              testId="eval-stat-error"
            />
            <StatCard
              label="Direct Reference 覆盖"
              value={formatPercent(referenceCoverage(latest) ?? dataset?.direct_reference_coverage)}
              testId="eval-stat-ref"
            />
          </Space>

          <Typography.Link onClick={() => onOpenRun(latest.id)} data-testid="eval-open-latest">
            查看最近 Run · {latest.id.slice(0, 8)}…
          </Typography.Link>
        </>
      )}

      {Object.keys(familyScores).length > 0 && (
        <div className="bp-panel-quiet" data-testid="eval-family-scores">
          <div className="bp-section-title">Task Family 得分</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {Object.entries(familyScores).map(([family, score]) => (
              <ScoreBar
                key={family}
                label={family}
                value={score}
                testId={`eval-family-bar-${family}`}
              />
            ))}
          </div>
        </div>
      )}

      {trendRuns.length > 0 && (
        <div className="bp-panel-quiet" data-testid="eval-trend">
          <div className="bp-section-title">最近 Run 趋势</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {trendRuns.map((run) => (
              <ScoreBar
                key={run.id}
                label={`${run.status} · ${shortHash(run.id, 6)} · ${evaluationTargetLabel(String(run.target_type))}`}
                value={run.overall_score}
                testId={`eval-trend-${run.id}`}
              />
            ))}
          </div>
        </div>
      )}

      <div className="bp-panel-quiet" data-testid="eval-available-targets">
        <div className="bp-section-title">可用 Target</div>
        {availableTargets.length === 0 ? (
          <Typography.Text type="secondary">当前无可用目标</Typography.Text>
        ) : (
          <Space wrap>
            {availableTargets.map((t) => (
              <span key={String(t.target_type)} className="bp-metric-chip">
                <strong>{t.label || evaluationTargetLabel(String(t.target_type))}</strong>
              </span>
            ))}
          </Space>
        )}
      </div>
    </div>
  );
}
