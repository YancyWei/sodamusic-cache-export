"use client";

import { useCallback, useState } from "react";
import { getJob, type JobSnapshot } from "@/lib/api";
import { useInterval } from "./useInterval";

export type JobState = {
  job: JobSnapshot | null;
  loading: boolean;
  error: string | null;
  start: (snapshot: JobSnapshot) => void;
  clear: () => void;
};

export function useJob(pollInterval = 1200): JobState {
  const [job, setJob] = useState<JobSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const start = useCallback((snapshot: JobSnapshot) => {
    setJob(snapshot);
    setError(null);
  }, []);

  const clear = useCallback(() => {
    setJob(null);
    setLoading(false);
    setError(null);
  }, []);

  const poll = useCallback(async () => {
    if (!job || job.status !== "running") return;
    try {
      setLoading(true);
      const snapshot = await getJob(job.id);
      setJob(snapshot);
    } catch (err) {
      setError(err instanceof Error ? err.message : "任务轮询失败");
    } finally {
      setLoading(false);
    }
  }, [job?.id, job?.status]);

  useInterval(poll, job?.status === "running" ? pollInterval : null);

  return { job, loading, error, start, clear };
}
