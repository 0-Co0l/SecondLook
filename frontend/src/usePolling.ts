import { useEffect, useRef, useState } from "react";

import { api, ApiError, Job, JobStatus } from "./api";

/** Polls a job until it reaches one of `until`, a failure, or the component unmounts. */
export function useJobPolling(jobId: string | undefined, until: JobStatus[], intervalMs = 1500) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const untilKey = until.join(",");
  const timer = useRef<number>();

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    const stopAt = new Set<JobStatus>([...(untilKey.split(",") as JobStatus[]), "failed"]);

    async function tick() {
      try {
        const current = await api.getJob(jobId!);
        if (cancelled) return;
        setJob(current);
        setError(null);
        if (!stopAt.has(current.status)) timer.current = window.setTimeout(tick, intervalMs);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.code : "network_error");
        if (!(err instanceof ApiError) || err.status >= 500) timer.current = window.setTimeout(tick, intervalMs * 2);
      }
    }

    void tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer.current);
    };
  }, [jobId, untilKey, intervalMs]);

  return { job, error, setJob };
}
