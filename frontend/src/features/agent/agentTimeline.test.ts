import { describe, expect, it } from "vitest";
import {
  buildTimelineDisplay,
  eventKey,
  hasSequenceGap,
  mergeTimelineEvents,
  parseSseAgentEventData,
  toTimelineEvent,
  type TimelineEvent,
} from "./agentTimeline";

function ev(partial: Partial<TimelineEvent> & { sequence: number }): TimelineEvent {
  return {
    run_id: "run-1",
    event_type: "node_started",
    status: "ok",
    ...partial,
  };
}

describe("agentTimeline helpers", () => {
  it("dedupes by run_id+sequence via merge", () => {
    const a = [
      ev({ sequence: 1, event_type: "node_started", node_name: "a" }),
      ev({ sequence: 2, event_type: "node_completed", node_name: "a", duration_ms: 10 }),
    ];
    const b = [
      ev({ sequence: 2, event_type: "node_completed", node_name: "a", duration_ms: 99 }),
      ev({ sequence: 3, event_type: "run_completed" }),
    ];
    const merged = mergeTimelineEvents(a, b);
    expect(merged.map((x) => x.sequence)).toEqual([1, 2, 3]);
    expect(merged[1]!.duration_ms).toBe(99);
    expect(eventKey("run-1", 2)).toBe("run-1:2");
  });

  it("detects sequence gaps", () => {
    expect(hasSequenceGap([ev({ sequence: 5 })], 3)).toBe(true);
    expect(hasSequenceGap([ev({ sequence: 4 })], 3)).toBe(false);
    expect(hasSequenceGap([ev({ sequence: 1 })], -1)).toBe(false);
  });

  it("nests tool events under agent_step_id", () => {
    const items = buildTimelineDisplay([
      ev({
        sequence: 1,
        event_type: "node_started",
        node_name: "retrieve",
        agent_step_id: "step-1",
      }),
      ev({
        sequence: 2,
        event_type: "tool_started",
        tool_name: "search",
        agent_step_id: "step-1",
      }),
      ev({
        sequence: 3,
        event_type: "tool_completed",
        tool_name: "search",
        agent_step_id: "step-1",
        duration_ms: 40,
      }),
      ev({
        sequence: 4,
        event_type: "node_completed",
        node_name: "retrieve",
        agent_step_id: "step-1",
        duration_ms: 100,
      }),
      ev({ sequence: 5, event_type: "run_completed" }),
    ]);
    expect(items[0]?.kind).toBe("step");
    if (items[0]?.kind === "step") {
      expect(items[0].tools).toHaveLength(2);
      expect(items[0].events).toHaveLength(2);
    }
    expect(items[1]?.kind).toBe("event");
  });

  it("parses SSE agent_event JSON without sensitive payload", () => {
    const parsed = parseSseAgentEventData(
      JSON.stringify({
        run_id: "run-1",
        sequence: 7,
        event_type: "tool_completed",
        tool_name: "search",
        status: "ok",
        safe_summary: "ok",
        duration_ms: 12,
        agent_step_id: "s1",
      }),
      "fallback",
    );
    expect(parsed?.sequence).toBe(7);
    expect(parsed?.duration_ms).toBe(12);
    expect(parsed?.run_id).toBe("run-1");
  });

  it("maps REST event items to timeline events", () => {
    const t = toTimelineEvent(
      {
        event_type: "node_failed",
        sequence: 9,
        name: "match",
        node_name: "match",
        status: "failed",
        summary: "boom",
        duration_ms: 3,
      },
      "run-x",
    );
    expect(t.run_id).toBe("run-x");
    expect(t.safe_summary).toBe("boom");
  });
});
