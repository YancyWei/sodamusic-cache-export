"use client";

import { useCallback, useEffect, useState } from "react";
import { getPreflightStatus, type PreflightStatusPayload } from "@/lib/api";
import { useInterval } from "./useInterval";

export type PreflightState = {
  data: PreflightStatusPayload | null;
  loading: boolean;
  error: string | null;
  refetch: (force?: boolean) => Promise<void>;
};

export function usePreflight(pollInterval = 5000): PreflightState {
  const [data, setData] = useState<PreflightStatusPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async (force = false) => {
    try {
      setLoading(true);
      const payload = await getPreflightStatus(force);
      setData(payload);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "预检失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refetch();
  }, [refetch]);

  useInterval(() => {
    refetch();
  }, pollInterval);

  return { data, loading, error, refetch };
}
