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

/** Exported for tests. */
export const MAX_SSE_RECONNECT_ATTEMPTS = 3;
export const SSE_BACKOFF_MS = [500, 1000, 2000] as const;
export const POLL_INTERVAL_MS = 2000;
export const SSE_RECOVERY_EVERY_N_POLLS = 5;

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
  const backoffRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closedRef = useRef(false);
  const reconnectAttemptRef = useRef(0);
  const pollTickRef = useRef(0);
  const onRunStatusRef = useRef(onRunStatus);
  onRunStatusRef.current = onRunStatus;

  // Latest callbacks via refs to avoid stale closures in timers / ES handlers.
  const connectSseRef = useRef<() => void>(() => {});
  const startPollingRef = useRef<() => void>(() => {});
  const handleTransportErrorRef = useRef<() => void>(() => {});

  const setEventsSafe = useCallback((updater: (prev: TimelineEvent[]) => TimelineEvent[]) => {
    const next = updater(eventsRef.current);
    eventsRef.current = next;
    setEvents(next);
  }, []);

  const clearTimers = useCallback(() => {
    if (backoffRef.current != null) {
      clearTimeout(backoffRef.current);
      backoffRef.current = null;
    }
    if (pollRef.current != null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    if (esRef.current) {
      const es = esRef.current;
      esRef.current = null;
      es.close();
    }
  }, []);

  const markCompleted = useCallback(() => {
    clearTimers();
    setConnection("completed");
  }, [clearTimers]);

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
        setError((err as Error)?.message || "补齐事件失败");
      }
    },
    [ingest, projectId, runId],
  );

  const catchUp = useCallback(async () => {
    if (!runId || closedRef.current) return;
    const after = lastSequence(eventsRef.current);
    try {
      const res = await getAgentEvents(projectId, runId, after >= 0 ? after : undefined);
      if (closedRef.current) return;
      const mapped = res.items.map((item) => toTimelineEvent(item, runId));
      ingest(mapped);
    } catch (err) {
      if (!closedRef.current) {
        setError((err as Error)?.message || "补齐事件失败");
      }
    }
  }, [ingest, projectId, runId]);

  const startPolling = useCallback(() => {
    if (!runId || closedRef.current) return;
    clearTimers();
    reconnectAttemptRef.current = 0;
    pollTickRef.current = 0;
    setConnection("polling");

    const tick = () => {
      void (async () => {
        if (closedRef.current || !runId) return;
        pollTickRef.current += 1;
        if (pollTickRef.current % SSE_RECOVERY_EVERY_N_POLLS === 0) {
          // Periodic SSE recovery — connectSse clears poll loop on entry.
          connectSseRef.current();
          return;
        }
        const after = lastSequence(eventsRef.current);
        try {
          const res = await getAgentEvents(projectId, runId, after >= 0 ? after : undefined);
          if (closedRef.current) return;
          const mapped = res.items.map((item) => toTimelineEvent(item, runId));
          ingest(mapped);
        } catch (err) {
          if (!closedRef.current) {
            setError((err as Error)?.message || "轮询事件失败");
          }
        }
      })();
    };

    tick();
    pollRef.current = setInterval(tick, POLL_INTERVAL_MS);
  }, [clearTimers, ingest, projectId, runId]);

  const scheduleReconnect = useCallback(() => {
    if (closedRef.current || !runId) return;
    const attempt = reconnectAttemptRef.current;
    const delay =
      SSE_BACKOFF_MS[Math.min(attempt - 1, SSE_BACKOFF_MS.length - 1)] ??
      SSE_BACKOFF_MS[SSE_BACKOFF_MS.length - 1]!;
    if (backoffRef.current != null) {
      clearTimeout(backoffRef.current);
      backoffRef.current = null;
    }
    backoffRef.current = setTimeout(() => {
      backoffRef.current = null;
      if (closedRef.current) return;
      connectSseRef.current();
    }, delay);
  }, [runId]);

  const handleTransportError = useCallback(() => {
    if (closedRef.current || !runId) return;
    // Idempotent: onerror + named "error" may both fire; only the first owns the ES.
    if (!esRef.current) return;
    const es = esRef.current;
    esRef.current = null;
    es.close();

    if (backoffRef.current != null) {
      clearTimeout(backoffRef.current);
      backoffRef.current = null;
    }
    if (pollRef.current != null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }

    setConnection("reconnecting");

    void (async () => {
      await catchUp();
      if (closedRef.current) return;

      if (reconnectAttemptRef.current < MAX_SSE_RECONNECT_ATTEMPTS) {
        reconnectAttemptRef.current += 1;
        scheduleReconnect();
      } else {
        startPollingRef.current();
      }
    })();
  }, [catchUp, runId, scheduleReconnect]);

  const connectSse = useCallback(() => {
    if (!runId || closedRef.current) return;
    clearTimers();

    const isRetry = reconnectAttemptRef.current > 0;
    setConnection(isRetry ? "reconnecting" : "connecting");

    const after = lastSequence(eventsRef.current);
    const url = buildAgentEventsStreamUrl(projectId, runId, {
      afterSequence: after >= 0 ? after : undefined,
      streamPath,
    });

    let es: EventSource;
    try {
      es = new EventSource(url);
    } catch {
      startPollingRef.current();
      return;
    }
    esRef.current = es;

    const onAgentEvent = (msg: MessageEvent) => {
      const parsed = parseSseAgentEventData(String(msg.data || ""), runId);
      if (!parsed) return;
      const prevLast = lastSequence(eventsRef.current);
      const gap = prevLast >= 0 && parsed.sequence > prevLast + 1;
      ingest([parsed]);
      setConnection("live");
      setError(null);
      reconnectAttemptRef.current = 0;
      if (gap) {
        void refetchAfter(prevLast);
      }
    };

    const onHeartbeat = () => {
      setConnection((c) => (c === "polling" ? c : "live"));
      reconnectAttemptRef.current = 0;
    };

    const onRunStatus = (msg: MessageEvent) => {
      try {
        const data = JSON.parse(String(msg.data || "{}")) as { status?: string };
        if (data.status) onRunStatusRef.current?.(data.status);
        if (isTerminalRunStatus(data.status)) {
          markCompleted();
        }
      } catch {
        /* ignore */
      }
    };

    const onDone = () => {
      markCompleted();
    };

    const onErrorEvent = () => {
      // Named SSE error event from server — same reconnect path as transport failure.
      handleTransportErrorRef.current();
    };

    es.addEventListener("agent_event", onAgentEvent as EventListener);
    es.addEventListener("heartbeat", onHeartbeat as EventListener);
    es.addEventListener("run_status", onRunStatus as EventListener);
    es.addEventListener("done", onDone as EventListener);
    es.addEventListener("error", onErrorEvent as EventListener);

    es.onopen = () => {
      if (closedRef.current) return;
      setConnection("live");
      setError(null);
    };

    es.onerror = () => {
      handleTransportErrorRef.current();
    };
  }, [
    clearTimers,
    ingest,
    markCompleted,
    projectId,
    refetchAfter,
    runId,
    streamPath,
  ]);

  connectSseRef.current = connectSse;
  startPollingRef.current = startPolling;
  handleTransportErrorRef.current = handleTransportError;

  // Reset when run changes
  useEffect(() => {
    closedRef.current = false;
    eventsRef.current = [];
    reconnectAttemptRef.current = 0;
    pollTickRef.current = 0;
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
      reconnectAttemptRef.current = 0;
      setConnection(isReconnect ? "reconnecting" : "connecting");
      try {
        const after = isReconnect ? lastSequence(eventsRef.current) : undefined;
        const res = await getAgentEvents(
          projectId,
          runId,
          after != null && after >= 0 ? after : undefined,
        );
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
      markCompleted();
    }
  }, [runStatus, historyLoaded, markCompleted]);

  return {
    events,
    connection,
    historyLoaded,
    error,
  };
}
