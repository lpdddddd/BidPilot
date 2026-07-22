import { useCallback, useEffect, useRef, useState } from "react";
import {
  buildAgentEventsStreamUrl,
  getAgentEvents,
} from "../../api/agentRuns";
import type { AgentRunStatus } from "../../types/api";
import {
  type ConnectionState,
  type TimelineEvent,
  hasSequenceGap,
  isTerminalRunStatus,
  lastSequence,
  mergeTimelineEvents,
  parseSseAgentEventData,
  toTimelineEvent,
} from "./agentTimeline";

const POLL_INTERVAL_MS = 2000;

type Options = {
  projectId: string;
  runId: string | null | undefined;
  runStatus?: AgentRunStatus | string | null;
  streamPath?: string | null;
  /** Bump to force reconnect without clearing events (e.g. after resume). */
  reconnectToken?: number;
  enabled?: boolean;
  onRunStatus?: (status: string) => void;
};

export function useAgentEventStream({
  projectId,
  runId,
  runStatus,
  streamPath,
  reconnectToken = 0,
  enabled = true,
  onRunStatus,
}: Options) {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [connection, setConnection] = useState<ConnectionState>("disconnected");
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const eventsRef = useRef<TimelineEvent[]>([]);
  const esRef = useRef<EventSource | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const closedRef = useRef(false);
  const onRunStatusRef = useRef(onRunStatus);
  onRunStatusRef.current = onRunStatus;

  const setEventsSafe = useCallback((updater: (prev: TimelineEvent[]) => TimelineEvent[]) => {
    setEvents((prev) => {
      const next = updater(prev);
      eventsRef.current = next;
      return next;
    });
  }, []);

  const clearTimers = useCallback(() => {
    if (pollRef.current != null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
  }, []);

  const ingest = useCallback(
    (incoming: TimelineEvent[], opts?: { checkGap?: boolean }) => {
      const prevLast = lastSequence(eventsRef.current);
      setEventsSafe((prev) => mergeTimelineEvents(prev, incoming));
      if (opts?.checkGap && hasSequenceGap(incoming, prevLast)) {
        return true;
      }
      return false;
    },
    [setEventsSafe],
  );

  const refetchAfter = useCallback(
    async (after: number) => {
      if (!runId) return;
      try {
        const res = await getAgentEvents(projectId, runId, after);
        const mapped = res.items.map((item) => toTimelineEvent(item, runId));
        ingest(mapped);
      } catch (err) {
        setError((err as Error)?.message || "拉取事件失败");
      }
    },
    [ingest, projectId, runId],
  );

  const startPolling = useCallback(() => {
    if (!runId || closedRef.current) return;
    clearTimers();
    setConnection("polling");
    const tick = () => {
      void (async () => {
        if (closedRef.current || !runId) return;
        const after = lastSequence(eventsRef.current);
        try {
          const res = await getAgentEvents(projectId, runId, after);
          const mapped = res.items.map((item) => toTimelineEvent(item, runId));
          ingest(mapped);
        } catch (err) {
          setError((err as Error)?.message || "轮询事件失败");
        }
      })();
    };
    tick();
    pollRef.current = setInterval(tick, POLL_INTERVAL_MS);
  }, [clearTimers, ingest, projectId, runId]);

  const connectSse = useCallback(() => {
    if (!runId || closedRef.current) return;
    clearTimers();
    setConnection((c) => (c === "live" ? "reconnecting" : "connecting"));

    const after = lastSequence(eventsRef.current);
    const url = buildAgentEventsStreamUrl(projectId, runId, {
      afterSequence: after,
      streamPath,
    });

    let es: EventSource;
    try {
      es = new EventSource(url);
    } catch {
      startPolling();
      return;
    }
    esRef.current = es;

    const onAgentEvent = (msg: MessageEvent) => {
      const parsed = parseSseAgentEventData(String(msg.data || ""), runId);
      if (!parsed) return;
      // Heartbeat never carries sequence into this handler.
      const prevLast = lastSequence(eventsRef.current);
      const gap = prevLast >= 0 && parsed.sequence > prevLast + 1;
      ingest([parsed]);
      setConnection("live");
      setError(null);
      if (gap) {
        void refetchAfter(prevLast);
      }
    };

    const onHeartbeat = () => {
      // Ignored for sequence tracking.
      setConnection((c) => (c === "polling" ? c : "live"));
    };

    const onRunStatus = (msg: MessageEvent) => {
      try {
        const data = JSON.parse(String(msg.data || "{}")) as { status?: string };
        if (data.status) onRunStatusRef.current?.(data.status);
        if (isTerminalRunStatus(data.status)) {
          setConnection("completed");
        }
      } catch {
        /* ignore */
      }
    };

    const onDone = () => {
      setConnection("completed");
      clearTimers();
    };

    const onErrorEvent = () => {
      // Named SSE error event from server — fall back to polling.
      if (closedRef.current) return;
      startPolling();
    };

    es.addEventListener("agent_event", onAgentEvent as EventListener);
    es.addEventListener("heartbeat", onHeartbeat as EventListener);
    es.addEventListener("run_status", onRunStatus as EventListener);
    es.addEventListener("done", onDone as EventListener);
    es.addEventListener("error", onErrorEvent as EventListener);

    es.onopen = () => {
      setConnection("live");
      setError(null);
    };

    es.onerror = () => {
      if (closedRef.current) return;
      // Transport failure → polling fallback
      if (esRef.current === es) {
        es.close();
        esRef.current = null;
      }
      startPolling();
    };
  }, [
    clearTimers,
    ingest,
    projectId,
    refetchAfter,
    runId,
    startPolling,
    streamPath,
  ]);

  // Reset when run changes
  useEffect(() => {
    closedRef.current = false;
    eventsRef.current = [];
    setEvents([]);
    setHistoryLoaded(false);
    setError(null);
    setConnection("disconnected");
    clearTimers();
  }, [runId, clearTimers]);

  // Load history then SSE / complete. reconnectToken re-subscribes without clearing events.
  useEffect(() => {
    if (!enabled || !runId) {
      setConnection("disconnected");
      return;
    }

    closedRef.current = false;
    let cancelled = false;
    const isReconnect = reconnectToken > 0 && eventsRef.current.length > 0;

    void (async () => {
      setConnection(isReconnect ? "reconnecting" : "connecting");
      try {
        const after = isReconnect ? lastSequence(eventsRef.current) : undefined;
        const res = await getAgentEvents(projectId, runId, after);
        if (cancelled || closedRef.current) return;
        const mapped = res.items.map((item) => toTimelineEvent(item, runId));
        if (isReconnect) {
          ingest(mapped);
        } else {
          setEventsSafe(() => mapped);
        }
        setHistoryLoaded(true);
        setError(null);

        if (isTerminalRunStatus(runStatus)) {
          setConnection("completed");
          return;
        }
        connectSse();
      } catch (err) {
        if (cancelled || closedRef.current) return;
        setHistoryLoaded(true);
        setError((err as Error)?.message || "加载事件失败");
        if (!isTerminalRunStatus(runStatus)) {
          startPolling();
        } else {
          setConnection("disconnected");
        }
      }
    })();

    return () => {
      cancelled = true;
      closedRef.current = true;
      clearTimers();
    };
    // runStatus read at subscribe time; terminal updates handled below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, runId, projectId, reconnectToken]);

  // When status becomes terminal while streaming, mark completed
  useEffect(() => {
    if (isTerminalRunStatus(runStatus) && historyLoaded) {
      setConnection("completed");
      clearTimers();
    }
  }, [runStatus, historyLoaded, clearTimers]);

  return {
    events,
    connection,
    historyLoaded,
    error,
  };
}
