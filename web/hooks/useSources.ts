"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  scanSources,
  type SourceRow,
  type SourcesResponse,
} from "@/lib/api";
import {
  compareText,
  normalizeText,
  qualityScore,
} from "@/lib/constants";

export type SourceFilter = {
  search: string;
  quality: string;
  sourceFormat: string;
  selected: "all" | "selected" | "unselected" | "exportable" | "uncached";
  sort: "artist" | "title" | "quality" | "size";
};

export type SourceState = {
  rows: SourceRow[];
  filtered: { row: SourceRow; index: number }[];
  filter: SourceFilter;
  loading: boolean;
  error: string | null;
  refetch: (cacheDir: string) => Promise<void>;
  setFilter: (patch: Partial<SourceFilter>) => void;
  updateRow: (index: number, patch: Partial<SourceRow>) => void;
  setRowsByPredicate: (
    predicate: (row: SourceRow, index: number) => boolean,
    selected: boolean
  ) => void;
  applyPreferredVersion: (version: string, indexes: Set<number>) => void;
  total: number;
  exportable: number;
  selected: number;
  uncached: number;
};

function sourceFormatKey(row: SourceRow): string {
  const extension = normalizeText(row.extension || "source");
  const codec = normalizeText(row.codecType);
  return codec ? `${extension}/${codec}` : extension;
}

function sourceQualityKey(row: SourceRow): string {
  return normalizeText(row.quality || "unknown");
}

function sourceSearchText(row: SourceRow): string {
  const uncached = row.uncachedBest
    ? `索引有 ${row.uncachedBest.quality}${
        row.uncachedBest.codecType
          ? `/${row.uncachedBest.codecType}`
          : ""
      } 未缓存`
    : "";
  return [
    row.title,
    row.artists,
    row.album,
    row.quality,
    row.extension,
    row.codecType,
    uncached,
  ]
    .map(normalizeText)
    .join(" ");
}

export function useSources(): SourceState {
  const [response, setResponse] = useState<SourcesResponse | null>(null);
  const [rows, setRows] = useState<SourceRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilterState] = useState<SourceFilter>({
    search: "",
    quality: "all",
    sourceFormat: "all",
    selected: "all",
    sort: "artist",
  });

  const refetch = useCallback(async (cacheDir: string) => {
    try {
      setLoading(true);
      const payload = await scanSources(cacheDir);
      const initialized = payload.rows.map((row) => ({
        ...row,
        selected: false,
        format: row.format || "",
      }));
      setResponse(payload);
      setRows(initialized);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "扫描失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const setFilter = useCallback((patch: Partial<SourceFilter>) => {
    setFilterState((prev) => ({ ...prev, ...patch }));
  }, []);

  const updateRow = useCallback((index: number, patch: Partial<SourceRow>) => {
    setRows((prev) => {
      const next = [...prev];
      next[index] = { ...next[index], ...patch };
      return next;
    });
  }, []);

  const setRowsByPredicate = useCallback(
    (predicate: (row: SourceRow, index: number) => boolean, selected: boolean) => {
      setRows((prev) =>
        prev.map((row, index) =>
          predicate(row, index)
            ? { ...row, selected: Boolean(selected && row.cacheUuid) }
            : row
        )
      );
    },
    []
  );

  const applyPreferredVersion = useCallback(
    (version: string, indexes: Set<number>) => {
      const [quality, codec] = version.split("/");
      setRows((prev) =>
        prev.map((row, index) => {
          if (!indexes.has(index) || !row.cachedCandidates) return row;
          const candidate = row.cachedCandidates.find(
            (c) =>
              normalizeText(c.quality) === normalizeText(quality) &&
              normalizeText(c.codecType || c.extension) === normalizeText(codec)
          );
          if (!candidate || candidate.cacheUuid === row.cacheUuid) return row;
          return {
            ...row,
            cacheUuid: candidate.cacheUuid,
            quality: candidate.quality,
            bitrate: candidate.bitrate,
            extension: candidate.extension,
            codecType: candidate.codecType,
            sourceSize: candidate.sourceSize,
            encrypted: candidate.encrypted,
          };
        })
      );
    },
    []
  );

  const filtered = useMemo(() => {
    const query = normalizeText(filter.search);
    const entries = rows
      .map((row, index) => ({ row, index }))
      .filter(({ row }) => {
        if (query && !sourceSearchText(row).includes(query)) return false;
        if (filter.quality !== "all" && sourceQualityKey(row) !== filter.quality)
          return false;
        if (
          filter.sourceFormat !== "all" &&
          sourceFormatKey(row) !== filter.sourceFormat
        )
          return false;
        if (filter.selected === "selected" && !(row.selected && row.cacheUuid))
          return false;
        if (filter.selected === "unselected" && row.selected && row.cacheUuid)
          return false;
        if (filter.selected === "exportable" && !row.cacheUuid) return false;
        if (filter.selected === "uncached" && !row.uncachedBest) return false;
        return true;
      });

    entries.sort((left, right) => {
      const a = left.row;
      const b = right.row;
      if (filter.sort === "title") {
        return compareText(a.title, b.title) || compareText(a.artists, b.artists);
      }
      if (filter.sort === "quality") {
        return (
          qualityScore(b.quality) - qualityScore(a.quality) ||
          Number(b.bitrate || 0) - Number(a.bitrate || 0) ||
          Number(b.sourceSize || 0) - Number(a.sourceSize || 0) ||
          compareText(a.artists, b.artists)
        );
      }
      if (filter.sort === "size") {
        return (
          Number(b.sourceSize || 0) - Number(a.sourceSize || 0) ||
          Number(b.bitrate || 0) - Number(a.bitrate || 0) ||
          compareText(a.artists, b.artists)
        );
      }
      return compareText(a.artists, b.artists) || compareText(a.title, b.title);
    });

    return entries;
  }, [rows, filter]);

  const selected = useMemo(
    () => rows.filter((row) => row.selected && row.cacheUuid).length,
    [rows]
  );

  return {
    rows,
    filtered,
    filter,
    loading,
    error,
    refetch,
    setFilter,
    updateRow,
    setRowsByPredicate,
    applyPreferredVersion,
    total: response?.total ?? 0,
    exportable: response?.exportable ?? 0,
    selected,
    uncached: response?.uncachedHigher ?? 0,
  };
}
