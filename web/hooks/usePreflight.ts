"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getPreflightStatus, type PreflightStatusPayload } from "@/lib/api";

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
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const callbackRef = useRef<(() => void) | undefined>(undefined);

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

  callbackRef.current = refetch;

  useEffect(() => {
    callbackRef.current?.();
  }, []);

  useEffect(() => {
    function startTimer() {
      if (timerRef.current) return;
      timerRef.current = setInterval(() => callbackRef.current?.(), pollInterval);
    }

    function stopTimer() {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }

    function handleVisibility() {
      if (document.visibilityState === "visible") {
        callbackRef.current?.();
        startTimer();
      } else {
        stopTimer();
      }
    }

    if (document.visibilityState === "visible") {
      startTimer();
    }

    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      stopTimer();
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [pollInterval]);

  return { data, loading, error, refetch };
}
