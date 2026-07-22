/** CSS score / progress bar (no chart library). */

export default function ScoreBar({
  value,
  max = 1,
  label,
  testId,
}: {
  value: number | null | undefined;
  max?: number;
  label?: string;
  testId?: string;
}) {
  const safeMax = max > 0 ? max : 1;
  const raw = value == null || Number.isNaN(value) ? null : value;
  const ratio = raw == null ? 0 : Math.max(0, Math.min(1, raw / safeMax));
  const pct = Math.round(ratio * 100);

  return (
    <div className="bp-score-bar" data-testid={testId}>
      {label != null && (
        <div className="bp-score-bar-label">
          <span>{label}</span>
          <span className="bp-faint">{raw == null ? "—" : `${pct}%`}</span>
        </div>
      )}
      <div className="bp-score-bar-track" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className="bp-score-bar-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
