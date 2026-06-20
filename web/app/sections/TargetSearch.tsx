"use client";

import { useMemo } from "react";
import { Card } from "@/app/components/ui/Card";
import { Button } from "@/app/components/ui/Button";
import { Input } from "@/app/components/ui/Input";
import { Select } from "@/app/components/ui/Select";
import { Badge } from "@/app/components/ui/Badge";
import { formatDuration } from "@/lib/format";
import { sortedTargetVersions, targetVersionLabel } from "@/lib/constants";
import { MagnifyingGlass, PlayCircle } from "@phosphor-icons/react";
import type { TargetSearchState } from "@/hooks/useTargetSearch";
import type { TargetSummary } from "@/lib/api";

interface TargetSearchProps {
  state: TargetSearchState;
  indexedQualities: string[];
  cacheDir: string;
  query: string;
  artist: string;
  target: string;
  timeout: number;
  jobRunning: boolean;
  onQueryChange: (value: string) => void;
  onArtistChange: (value: string) => void;
  onTargetChange: (value: string) => void;
  onTimeoutChange: (value: number) => void;
  onSearch: () => void;
  onStart: () => void;
}

export function TargetSearch({
  state,
  indexedQualities,
  cacheDir,
  query,
  artist,
  target,
  timeout,
  jobRunning,
  onQueryChange,
  onArtistChange,
  onTargetChange,
  onTimeoutChange,
  onSearch,
  onStart,
}: TargetSearchProps) {
  const versionOptions = useMemo(() => {
    const versions = sortedTargetVersions([
      ...indexedQualities,
      "lossless/flac",
      "hi_res/aac",
      "spatial/aac",
      "highest/aac",
      "higher/aac",
      "medium/aac",
    ]);
    return versions.map((v) => ({ value: v, label: targetVersionLabel(v) }));
  }, [indexedQualities]);

  return (
    <div className="animate-fade-in-up">
      <Card className="p-5">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-zinc-600">歌曲关键词</label>
            <Input
              placeholder="歌名 / trackId"
              value={query}
              onChange={(e) => onQueryChange(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && onSearch()}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-zinc-600">艺人（可选）</label>
            <Input
              placeholder="艺人名"
              value={artist}
              onChange={(e) => onArtistChange(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && onSearch()}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-zinc-600">目标版本</label>
            <Select value={target} onChange={(e) => onTargetChange(e.target.value)}>
              {versionOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-zinc-600">等待超时</label>
            <Select value={timeout} onChange={(e) => onTimeoutChange(Number(e.target.value))}>
              <option value={0}>一直等待</option>
              <option value={60}>1 分钟</option>
              <option value={300}>5 分钟</option>
              <option value={900}>15 分钟</option>
            </Select>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Button variant="secondary" onClick={onSearch} disabled={state.loading || jobRunning || !cacheDir} isLoading={state.loading}>
            <MagnifyingGlass size={18} />
            搜索匹配
          </Button>
          <Button
            variant="primary"
            onClick={onStart}
            disabled={jobRunning || !state.selectedTrackId || !cacheDir}
          >
            <PlayCircle weight="fill" size={18} />
            等待并导出
          </Button>
          {state.hint && <span className="text-xs text-zinc-500">{state.hint}</span>}
        </div>

        {state.error && <p className="mt-3 text-sm text-red-600">{state.error}</p>}

        {state.matches.length > 0 && (
          <div className="mt-4 space-y-2 animate-fade-in-up">
            <p className="text-xs font-medium text-zinc-500">匹配到 {state.matches.length} 首</p>
            {state.matches.map((match) => (
              <TargetMatchCard
                key={match.trackId}
                match={match}
                selected={state.selectedTrackId === match.trackId}
                onSelect={() => state.select(match.trackId)}
              />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

function TargetMatchCard({
  match,
  selected,
  onSelect,
}: {
  match: TargetSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  const status = match.targetCached
    ? { label: "已缓存", tone: "success" as const }
    : match.targetIndexed
    ? { label: "索引有", tone: "warning" as const }
    : { label: "未见目标品质", tone: "neutral" as const };

  return (
    <button
      onClick={onSelect}
      className={`w-full rounded-[var(--radius-md)] border p-3 text-left transition-colors ${
        selected
          ? "border-emerald-400 bg-emerald-50/50"
          : "border-zinc-200 bg-white hover:border-zinc-300"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-medium text-zinc-900">
            {match.artists || "Unknown Artist"} - {match.title || match.trackId}
          </p>
          <p className="text-xs text-zinc-500">
            {match.trackId}
            {formatDuration(match.durationMs) ? ` · ${formatDuration(match.durationMs)}` : ""}
          </p>
        </div>
        <Badge tone={status.tone}>{status.label}</Badge>
      </div>
      <p className="mt-1.5 truncate text-xs text-zinc-400">
        索引: {(match.indexedLabels || []).join(", ") || "none"} · 缓存: {(match.cachedLabels || []).join(", ") || "none"}
      </p>
    </button>
  );
}
