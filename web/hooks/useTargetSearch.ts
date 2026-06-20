"use client";

import { useCallback, useState } from "react";
import { searchTargets, type TargetSummary } from "@/lib/api";

export type TargetSearchState = {
  matches: TargetSummary[];
  selectedTrackId: string;
  loading: boolean;
  error: string | null;
  hint: string;
  search: (payload: {
    cacheDir: string;
    query?: string;
    artist?: string;
    target: string;
  }) => Promise<void>;
  select: (trackId: string) => void;
  reset: () => void;
};

export function useTargetSearch(): TargetSearchState {
  const [matches, setMatches] = useState<TargetSummary[]>([]);
  const [selectedTrackId, setSelectedTrackId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hint, setHint] = useState("");

  const search = useCallback(
    async (payload: {
      cacheDir: string;
      query?: string;
      artist?: string;
      target: string;
    }) => {
      if (!payload.query && !payload.artist) {
        setHint("先输入歌曲关键词或艺人，必要时用 trackId。");
        setError(null);
        return;
      }
      setLoading(true);
      setError(null);
      setHint("正在搜索本地索引。");
      setSelectedTrackId("");
      try {
        const result = await searchTargets({
          ...payload,
          limit: 20,
        });
        setMatches(result.matches);
        if (result.matches.length === 1) {
          setSelectedTrackId(result.matches[0].trackId);
          setHint("已锁定唯一匹配。");
        } else if (result.matches.length > 1) {
          setHint("请选择一首目标歌曲，避免同名误匹配。");
        } else {
          setHint("没有匹配本地索引；先在官方客户端搜索或播放目标歌曲。");
        }
      } catch (err) {
        setMatches([]);
        setSelectedTrackId("");
        setError(err instanceof Error ? err.message : "搜索失败");
        setHint("");
      } finally {
        setLoading(false);
      }
    },
    []
  );

  const select = useCallback((trackId: string) => {
    setSelectedTrackId(trackId);
  }, []);

  const reset = useCallback(() => {
    setMatches([]);
    setSelectedTrackId("");
    setError(null);
    setHint("");
  }, []);

  return {
    matches,
    selectedTrackId,
    loading,
    error,
    hint,
    search,
    select,
    reset,
  };
}
