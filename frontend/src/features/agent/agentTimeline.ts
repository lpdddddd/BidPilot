/** Agent timeline display helpers (Step 11). */

import type { AgentEventItem, AgentRunStatus } from "../../types/api";

export type ConnectionState =
  | "connecting"
  | "live"
  | "reconnecting"
  | "polling"
  | "disconnected"
  | "completed";

export type TimelineEvent = {
  run_id: string;
  sequence: number;
  event_type: string;
  node_name?: string | null;
  tool_name?: string | null;
  status: string;
  timestamp?: string | null;
  duration_ms?: number | null;
  safe_summary?: string | null;
  agent_step_id?: string | null;
  tool_call_id?: string | null;
  attempt?: number | null;
};

/** Main pipeline nodes (excluding optional revise loops). */
export const EXPECTED_PIPELINE_NODES = [
  "initialize_run",
  "load_project_context",
  "retrieve_evidence",
  "extract_requirements",
  "match_company_evidence",
  "run_compliance_check",
  "generate_response_draft",
  "validate_draft",
  "finalize_run",
] as const;

export const CONNECTION_LABELS: Record<ConnectionState, string> = {
  connecting: "连接中",
  live: "实时",
  reconnecting: "重连中",
  polling: "轮询",
  disconnected: "已断开",
  completed: "已结束",
};

export const EVENT_TYPE_LABELS: Record<string, string> = {
  node_started: "节点开始",
  node_completed: "节点完成",
  node_failed: "节点失败",
  tool_started: "工具开始",
  tool_completed: "工具完成",
  tool_failed: "工具失败",
  run_resumed: "运行恢复",
  run_completed: "运行完成",
  run_failed: "运行失败",
};

export const TERMINAL_RUN_STATUSES: ReadonlySet<string> = new Set([
  "completed",
  "completed_with_warnings",
  "blocked",
  "failed",
  "cancelled",
]);

export function isTerminalRunStatus(status: AgentRunStatus | string | null | undefined): boolean {
  return status != null && TERMINAL_RUN_STATUSES.has(status);
}

export function eventKey(runId: string, sequence: number): string {
  return `${runId}:${sequence}`;
}

export function toTimelineEvent(
  item: AgentEventItem | TimelineEvent,
  runId: string,
): TimelineEvent {
  return {
    run_id: "run_id" in item && item.run_id ? String(item.run_id) : runId,
    sequence: item.sequence,
    event_type: item.event_type,
    node_name: item.node_name ?? null,
    tool_name: item.tool_name ?? null,
    status: item.status || "ok",
    timestamp: item.timestamp ?? ("created_at" in item ? item.created_at : null) ?? null,
    duration_ms: item.duration_ms ?? null,
    safe_summary:
      item.safe_summary ??
      ("summary" in item ? (item.summary as string | null | undefined) : null) ??
      null,
    agent_step_id: item.agent_step_id != null ? String(item.agent_step_id) : null,
    tool_call_id: item.tool_call_id != null ? String(item.tool_call_id) : null,
    attempt: item.attempt ?? null,
  };
}

export function mergeTimelineEvents(
  existing: TimelineEvent[],
  incoming: TimelineEvent[],
): TimelineEvent[] {
  const map = new Map<string, TimelineEvent>();
  for (const ev of existing) {
    map.set(eventKey(ev.run_id, ev.sequence), ev);
  }
  for (const ev of incoming) {
    map.set(eventKey(ev.run_id, ev.sequence), ev);
  }
  return Array.from(map.values()).sort((a, b) => a.sequence - b.sequence);
}

export function lastSequence(events: TimelineEvent[]): number {
  if (events.length === 0) return -1;
  return events[events.length - 1]!.sequence;
}

/** Detect gap after appending: expected next = last+1 for contiguous stream. */
export function hasSequenceGap(
  events: TimelineEvent[],
  previousLast: number,
): boolean {
  if (events.length === 0) return false;
  const sorted = [...events].sort((a, b) => a.sequence - b.sequence);
  let prev = previousLast;
  for (const ev of sorted) {
    if (ev.sequence <= prev) continue;
    if (prev >= 0 && ev.sequence > prev + 1) return true;
    prev = ev.sequence;
  }
  return false;
}

export function isToolEvent(eventType: string): boolean {
  return eventType.startsWith("tool_");
}

export function eventTypeLabel(eventType: string): string {
  return EVENT_TYPE_LABELS[eventType] ?? eventType;
}

export function formatDurationMs(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms)) return "—";
  if (ms < 1000) return `${ms} ms`;
  const sec = ms / 1000;
  if (sec < 60) return `${sec.toFixed(sec < 10 ? 1 : 0)} s`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}m ${s}s`;
}

export function formatElapsed(
  startedAt: string | null | undefined,
  finishedAt: string | null | undefined,
  nowMs: number,
): string {
  if (!startedAt) return "—";
  const start = Date.parse(startedAt);
  if (Number.isNaN(start)) return "—";
  const end = finishedAt ? Date.parse(finishedAt) : nowMs;
  const endMs = Number.isNaN(end) ? nowMs : end;
  return formatDurationMs(Math.max(0, endMs - start));
}

export function shortRunId(runId: string | null | undefined): string {
  if (!runId) return "—";
  return runId.length > 8 ? runId.slice(0, 8) : runId;
}

export function deriveCurrentNode(events: TimelineEvent[], fallback?: string | null): string {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const ev = events[i]!;
    if (ev.event_type !== "node_started" || !ev.node_name) continue;
    const done = events.some(
      (x) =>
        (x.event_type === "node_completed" || x.event_type === "node_failed") &&
        x.sequence > ev.sequence &&
        (ev.agent_step_id
          ? x.agent_step_id === ev.agent_step_id
          : x.node_name === ev.node_name),
    );
    if (!done) return ev.node_name;
  }
  return fallback || "—";
}

export function deriveCurrentTool(events: TimelineEvent[]): string {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const ev = events[i]!;
    if (ev.event_type === "tool_started" && ev.tool_name) {
      const done = events.some(
        (x) =>
          (x.event_type === "tool_completed" || x.event_type === "tool_failed") &&
          x.sequence > ev.sequence &&
          (ev.tool_call_id
            ? x.tool_call_id === ev.tool_call_id
            : x.tool_name === ev.tool_name),
      );
      if (!done) return ev.tool_name;
    }
  }
  return "—";
}

export function countCompletedSteps(events: TimelineEvent[]): number {
  return events.filter((e) => e.event_type === "node_completed").length;
}

export function deriveProgressPercent(events: TimelineEvent[]): number {
  const completed = new Set<string>();
  for (const ev of events) {
    if (ev.event_type === "node_completed" && ev.node_name) {
      completed.add(ev.node_name);
    }
  }
  const expected = EXPECTED_PIPELINE_NODES;
  const hit = expected.filter((n) => completed.has(n)).length;
  if (events.some((e) => e.event_type === "run_completed")) return 100;
  return Math.min(100, Math.round((hit / expected.length) * 100));
}

export type TimelineGroup = {
  key: string;
  stepId: string | null;
  nodeEvents: TimelineEvent[];
  toolEvents: TimelineEvent[];
  /** Events without step (run_*) kept in sequence via flat list approach */
};

/**
 * Build display rows: run-level events alone; node steps with nested tools.
 * Ordering follows first sequence of each group / lone event.
 */
export type TimelineDisplayItem =
  | { kind: "event"; event: TimelineEvent; nested: boolean }
  | { kind: "step"; stepId: string; events: TimelineEvent[]; tools: TimelineEvent[] };

export function buildTimelineDisplay(events: TimelineEvent[]): TimelineDisplayItem[] {
  const sorted = [...events].sort((a, b) => a.sequence - b.sequence);
  const stepOrder: string[] = [];
  const stepNodes = new Map<string, TimelineEvent[]>();
  const stepTools = new Map<string, TimelineEvent[]>();
  const lone: TimelineEvent[] = [];

  for (const ev of sorted) {
    if (isToolEvent(ev.event_type) && ev.agent_step_id) {
      if (!stepTools.has(ev.agent_step_id)) {
        stepTools.set(ev.agent_step_id, []);
        if (!stepOrder.includes(ev.agent_step_id)) stepOrder.push(ev.agent_step_id);
      }
      stepTools.get(ev.agent_step_id)!.push(ev);
      continue;
    }
    if (ev.agent_step_id && ev.event_type.startsWith("node_")) {
      if (!stepNodes.has(ev.agent_step_id)) {
        stepNodes.set(ev.agent_step_id, []);
        if (!stepOrder.includes(ev.agent_step_id)) stepOrder.push(ev.agent_step_id);
      }
      stepNodes.get(ev.agent_step_id)!.push(ev);
      continue;
    }
    lone.push(ev);
  }

  type Mark =
    | { t: "lone"; seq: number; event: TimelineEvent }
    | { t: "step"; seq: number; stepId: string };

  const marks: Mark[] = [];
  for (const ev of lone) {
    marks.push({ t: "lone", seq: ev.sequence, event: ev });
  }
  for (const stepId of stepOrder) {
    const nodes = stepNodes.get(stepId) || [];
    const tools = stepTools.get(stepId) || [];
    const first = [...nodes, ...tools].sort((a, b) => a.sequence - b.sequence)[0];
    if (first) marks.push({ t: "step", seq: first.sequence, stepId });
  }
  marks.sort((a, b) => a.seq - b.seq);

  const items: TimelineDisplayItem[] = [];
  const seenSteps = new Set<string>();
  for (const m of marks) {
    if (m.t === "lone") {
      items.push({ kind: "event", event: m.event, nested: isToolEvent(m.event.event_type) });
    } else if (!seenSteps.has(m.stepId)) {
      seenSteps.add(m.stepId);
      items.push({
        kind: "step",
        stepId: m.stepId,
        events: (stepNodes.get(m.stepId) || []).sort((a, b) => a.sequence - b.sequence),
        tools: (stepTools.get(m.stepId) || []).sort((a, b) => a.sequence - b.sequence),
      });
    }
  }
  return items;
}

export function parseSseAgentEventData(raw: string, fallbackRunId: string): TimelineEvent | null {
  try {
    const data = JSON.parse(raw) as Record<string, unknown>;
    if (typeof data.sequence !== "number") return null;
    return toTimelineEvent(
      {
        event_type: String(data.event_type || ""),
        sequence: data.sequence,
        name: String(data.tool_name || data.node_name || data.event_type || ""),
        node_name: data.node_name != null ? String(data.node_name) : null,
        tool_name: data.tool_name != null ? String(data.tool_name) : null,
        status: String(data.status || "ok"),
        safe_summary: data.safe_summary != null ? String(data.safe_summary) : null,
        timestamp: data.timestamp != null ? String(data.timestamp) : null,
        duration_ms: typeof data.duration_ms === "number" ? data.duration_ms : null,
        agent_step_id: data.agent_step_id != null ? String(data.agent_step_id) : null,
        tool_call_id: data.tool_call_id != null ? String(data.tool_call_id) : null,
        attempt: typeof data.attempt === "number" ? data.attempt : null,
      },
      data.run_id != null ? String(data.run_id) : fallbackRunId,
    );
  } catch {
    return null;
  }
}
