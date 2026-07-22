import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getEvaluationRun } from "../../api/evaluation";
import {
  EVAL_POLL_INTERVAL_MS,
  isActiveEvaluationStatus,
  isTerminalEvaluationStatus,
} from "./evaluationParams";

type Options = {
  projectId: string;
  runId: string | null | undefined;
  enabled?: boolean;
  intervalMs?: number;
};

/**
 * Poll evaluation run while queued/running.
 * Explicit interval — cleared on terminal status and unmount.
 */
export function useEvaluationRunPoll({
  projectId,
  runId,
  enabled = true,
  intervalMs = EVAL_POLL_INTERVAL_MS,
}: Options) {
  const queryClient = useQueryClient();
  const [isPolling, setIsPolling] = useState(false);

  const query = useQuery({
    queryKey: ["evaluation-run", projectId, runId],
    queryFn: () => getEvaluationRun(projectId, runId!),
    enabled: Boolean(enabled && projectId && runId),
    retry: 0,
  });

  useEffect(() => {
    if (!enabled || !projectId || !runId) {
      setIsPolling(false);
      return;
    }

    const status = query.data?.status;
    if (status && isTerminalEvaluationStatus(status)) {
      setIsPolling(false);
      return;
    }
    if (status && !isActiveEvaluationStatus(status)) {
      setIsPolling(false);
      return;
    }

    setIsPolling(true);
    const timerId = setInterval(() => {
      void queryClient.invalidateQueries({
        queryKey: ["evaluation-run", projectId, runId],
      });
    }, intervalMs);

    return () => {
      clearInterval(timerId);
    };
  }, [enabled, projectId, runId, intervalMs, query.data?.status, queryClient]);

  return {
    ...query,
    isPolling,
  };
}

export { EVAL_POLL_INTERVAL_MS };
