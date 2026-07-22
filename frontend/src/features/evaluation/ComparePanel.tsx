import { Alert, Button, Select, Space, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { EvaluationCompareResponse, EvaluationRun } from "../../types/api";
import {
  compareMismatchWarnings,
  evaluationTargetLabel,
  formatPercent,
  formatScore,
  shortHash,
} from "./evaluationParams";

type Props = {
  runs: EvaluationRun[];
  leftId?: string;
  rightId?: string;
  onSelect: (left?: string, right?: string) => void;
  comparing: boolean;
  result: EvaluationCompareResponse | undefined;
  error: string | null;
  onCompare: () => void;
};

export default function ComparePanel({
  runs,
  leftId,
  rightId,
  onSelect,
  comparing,
  result,
  error,
  onCompare,
}: Props) {
  const completed = runs.filter(
    (r) => r.status === "completed" || r.status === "partial",
  );
  const options = completed.map((r) => ({
    value: r.id,
    label: `${shortHash(r.id, 8)} · ${evaluationTargetLabel(String(r.target_type))} · ${formatScore(r.overall_score)}`,
  }));

  const warnings = compareMismatchWarnings(result?.warnings);
  const showMismatchBanner =
    Boolean(result) &&
    (warnings.datasetHashMismatch ||
      warnings.evaluatorVersionMismatch ||
      warnings.messages.length > 0);

  const deltaColumns: ColumnsType<{ key: string; delta: number | null }> = [
    { title: "项", dataIndex: "key" },
    {
      title: "Delta",
      dataIndex: "delta",
      render: (v: number | null) => (v == null ? "—" : formatScore(v)),
    },
  ];

  const caseColumns: ColumnsType<{ case_key: string; left_score?: number | null; right_score?: number | null; delta?: number | null }> = [
    { title: "Case", dataIndex: "case_key" },
    {
      title: "Left",
      dataIndex: "left_score",
      render: (v: number | null | undefined) => formatScore(v),
    },
    {
      title: "Right",
      dataIndex: "right_score",
      render: (v: number | null | undefined) => formatScore(v),
    },
    {
      title: "Delta",
      dataIndex: "delta",
      render: (v: number | null | undefined) => formatScore(v),
    },
  ];

  return (
    <div data-testid="eval-compare" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
        选择两个已完成或部分完成的 Run 进行对比。数据集 hash 或评估器版本不一致时将显示警告，避免误导性结论。
      </Typography.Paragraph>

      <Space wrap>
        <Select
          style={{ minWidth: 280 }}
          placeholder="左侧 Run"
          data-testid="eval-compare-left"
          value={leftId}
          options={options}
          onChange={(v) => onSelect(v, rightId)}
          showSearch
          optionFilterProp="label"
        />
        <Select
          style={{ minWidth: 280 }}
          placeholder="右侧 Run"
          data-testid="eval-compare-right"
          value={rightId}
          options={options}
          onChange={(v) => onSelect(leftId, v)}
          showSearch
          optionFilterProp="label"
        />
        <Button
          type="primary"
          loading={comparing}
          disabled={!leftId || !rightId || leftId === rightId}
          data-testid="eval-compare-btn"
          onClick={onCompare}
        >
          对比
        </Button>
      </Space>

      {error && (
        <Alert type="error" showIcon message="对比失败" description={error} data-testid="eval-compare-error" />
      )}

      {showMismatchBanner && (
        <Alert
          type="warning"
          showIcon
          data-testid="eval-compare-mismatch-warning"
          message="数据版本或评估器不一致"
          description={
            <div>
              <p style={{ marginTop: 0 }}>
                两侧 Run 的 dataset hash 或 evaluator version 可能不同，请勿直接下结论。
              </p>
              <ul style={{ marginBottom: 0 }}>
                {warnings.messages.map((m, i) => (
                  <li key={i}>{m}</li>
                ))}
              </ul>
            </div>
          }
        />
      )}

      {result && (
        <div data-testid="eval-compare-result" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <Space wrap size="large">
            <div className="bp-panel" style={{ padding: 12 }}>
              <Typography.Text type="secondary">Overall delta</Typography.Text>
              <div style={{ fontSize: 20, fontWeight: 600 }}>
                {formatScore(result.overall_score_delta)}
              </div>
            </div>
            <div className="bp-panel" style={{ padding: 12 }}>
              <Typography.Text type="secondary">Pass rate delta</Typography.Text>
              <div style={{ fontSize: 20, fontWeight: 600 }}>
                {formatPercent(result.pass_rate_delta)}
              </div>
            </div>
          </Space>

          <div className="bp-tech-grid">
            <span>
              Left hash {shortHash(result.left.dataset_hash)} · eval {result.left.evaluator_version}
            </span>
            <span>
              Right hash {shortHash(result.right.dataset_hash)} · eval {result.right.evaluator_version}
            </span>
            <span>
              Left {evaluationTargetLabel(String(result.left.target_type))} / Right{" "}
              {evaluationTargetLabel(String(result.right.target_type))}
            </span>
          </div>

          {result.task_family_deltas && (
            <Table
              size="small"
              pagination={false}
              rowKey="key"
              columns={deltaColumns}
              dataSource={Object.entries(result.task_family_deltas).map(([key, delta]) => ({
                key,
                delta: delta ?? null,
              }))}
              title={() => "Task Family Delta"}
            />
          )}

          {result.metric_deltas && (
            <Table
              size="small"
              pagination={false}
              rowKey="key"
              columns={deltaColumns}
              dataSource={Object.entries(result.metric_deltas).map(([key, delta]) => ({
                key,
                delta: delta ?? null,
              }))}
              title={() => "Metric Delta"}
            />
          )}

          <Table
            size="small"
            rowKey="case_key"
            columns={caseColumns}
            dataSource={result.improved_cases ?? []}
            title={() => "Improved cases"}
            pagination={{ pageSize: 10 }}
            locale={{ emptyText: "无" }}
          />
          <Table
            size="small"
            rowKey="case_key"
            columns={caseColumns}
            dataSource={result.regressed_cases ?? []}
            title={() => "Regressed cases"}
            pagination={{ pageSize: 10 }}
            locale={{ emptyText: "无" }}
          />
          <Table
            size="small"
            rowKey="case_key"
            columns={caseColumns}
            dataSource={result.unchanged_cases ?? []}
            title={() => "Unchanged cases"}
            pagination={{ pageSize: 10 }}
            locale={{ emptyText: "无" }}
          />

          <div className="bp-panel-quiet">
            <div className="bp-section-title">仅一侧存在</div>
            <Typography.Text>
              Left only: {(result.left_only_cases ?? []).join(", ") || "—"}
            </Typography.Text>
            <br />
            <Typography.Text>
              Right only: {(result.right_only_cases ?? []).join(", ") || "—"}
            </Typography.Text>
          </div>
        </div>
      )}
    </div>
  );
}
