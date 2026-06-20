"use client";

import { useMemo, useState } from "react";
import { Card } from "@/app/components/ui/Card";
import { Button } from "@/app/components/ui/Button";
import { Input } from "@/app/components/ui/Input";
import { Select } from "@/app/components/ui/Select";
import { Badge } from "@/app/components/ui/Badge";
import { EmptyState } from "@/app/components/EmptyState";
import { Collapsible } from "@/app/components/ui/Collapsible";
import { formatBitrate, formatBytes, formatDuration } from "@/lib/format";
import { EXPORT_FORMAT_LABELS, ALLOWED_FORMATS, normalizeText, compareText, qualityScore } from "@/lib/constants";
import { MagnifyingGlass, Checks, X, CaretDown, CaretUp, Info } from "@phosphor-icons/react";
import type { SourceState } from "@/hooks/useSources";
import type { SourceRow } from "@/lib/api";

interface SourceListProps {
  state: SourceState;
  mp3TranscoderFound: boolean;
  jobRunning: boolean;
  defaultFormat: string;
}

export function SourceList({ state, mp3TranscoderFound, jobRunning, defaultFormat }: SourceListProps) {
  const [expanded, setExpanded] = useState(true);
  const { rows, filtered, filter, loading, error, selected, exportable } = state;

  const qualityOptions = useMemo(() => {
    const qualities = Array.from(new Set(rows.map((row) => normalizeText(row.quality || "unknown")))).sort();
    return [{ value: "all", label: "全部质量" }, ...qualities.map((q) => ({ value: q, label: q === "unknown" ? "未知" : q }))];
  }, [rows]);

  return (
    <Card className="p-5">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between"
      >
        <div className="text-left">
          <h3 className="font-semibold text-zinc-900">已选择 {selected} 首</h3>
          <p className="text-sm text-zinc-500">
            共 {exportable} 首可导出
            {selected === 0 ? "，未选择时将导出全部" : ""}
          </p>
        </div>
        <div>
          {expanded ? <CaretUp size={18} className="text-zinc-400" /> : <CaretDown size={18} className="text-zinc-400" />}
        </div>
      </button>

      <Collapsible open={expanded}>
        <div className="mt-4 border-t border-zinc-100 pt-4">
              <div className="flex flex-col gap-3 sm:flex-row">
                <div className="relative flex-1">
                  <MagnifyingGlass
                    className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400"
                    size={18}
                  />
                  <Input
                    className="pl-9"
                    placeholder="搜索歌曲或艺人"
                    value={filter.search}
                    onChange={(e) => state.setFilter({ search: e.target.value })}
                  />
                </div>
                <Select
                  value={filter.quality}
                  onChange={(e) => state.setFilter({ quality: e.target.value })}
                  className="w-full sm:w-36"
                >
                  {qualityOptions.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </Select>
                <Select
                  value={filter.sort}
                  onChange={(e) => state.setFilter({ sort: e.target.value as SourceState["filter"]["sort"] })}
                  className="w-full sm:w-36"
                >
                  <option value="artist">艺人排序</option>
                  <option value="title">歌曲排序</option>
                  <option value="quality">质量优先</option>
                  <option value="size">大小优先</option>
                </Select>
                <div className="flex gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => state.setRowsByPredicate((row) => Boolean(row.cacheUuid), true)}
                  >
                    <Checks size={16} />
                    全选
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => state.setRowsByPredicate(() => true, false)}
                  >
                    <X size={16} />
                    取消
                  </Button>
                </div>
              </div>

              <div className="mt-2 flex items-start gap-1.5 text-xs text-zinc-500">
                <Info size={14} className="mt-0.5 shrink-0" />
                <span>每首歌可单独选择缓存版本或导出格式；留空则使用上方全局设置。</span>
              </div>

              <div className="mt-3 max-h-80 overflow-auto rounded-[var(--radius-md)] border border-zinc-100">
                {loading ? (
                  <div className="space-y-2 p-4">
                    {Array.from({ length: 5 }).map((_, i) => (
                      <div key={i} className="h-12 animate-pulse rounded-lg bg-zinc-100" />
                    ))}
                  </div>
                ) : error ? (
                  <EmptyState title="扫描失败" description={error} />
                ) : filtered.length === 0 ? (
                  <EmptyState title="没有匹配结果" description="调整搜索或筛选条件后再试。" />
                ) : (
                  <ul className="divide-y divide-zinc-100">
                    {filtered.map(({ row, index }) => (
                      <SourceItem
                        key={`${row.trackId}-${index}`}
                        row={row}
                        index={index}
                        disabled={!row.cacheUuid || jobRunning}
                        mp3TranscoderFound={mp3TranscoderFound}
                        defaultFormat={defaultFormat}
                        onToggle={(checked) => state.updateRow(index, { selected: checked })}
                        onVersionChange={(cacheUuid) => {
                          const candidate = row.cachedCandidates?.find((c) => c.cacheUuid === cacheUuid);
                          if (candidate) {
                            state.updateRow(index, {
                              cacheUuid: candidate.cacheUuid,
                              quality: candidate.quality,
                              bitrate: candidate.bitrate,
                              extension: candidate.extension,
                              codecType: candidate.codecType,
                              sourceSize: candidate.sourceSize,
                              encrypted: candidate.encrypted,
                            });
                          }
                        }}
                        onFormatChange={(format) => state.updateRow(index, { format })}
                      />
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </Collapsible>
    </Card>
  );
}

function SourceItem({
  row,
  disabled,
  mp3TranscoderFound,
  defaultFormat,
  onToggle,
  onVersionChange,
  onFormatChange,
}: {
  row: SourceRow;
  index: number;
  disabled: boolean;
  mp3TranscoderFound: boolean;
  defaultFormat: string;
  onToggle: (checked: boolean) => void;
  onVersionChange: (cacheUuid: string) => void;
  onFormatChange: (format: string) => void;
}) {
  const candidates = row.cachedCandidates?.length
    ? row.cachedCandidates
    : row.cacheUuid
    ? [{
        cacheUuid: row.cacheUuid,
        quality: row.quality,
        bitrate: row.bitrate,
        extension: row.extension,
        codecType: row.codecType,
        sourceSize: row.sourceSize,
        encrypted: row.encrypted,
      }]
    : [];

  const warning = row.uncachedBest
    ? `索引有 ${row.uncachedBest.quality}${row.uncachedBest.codecType ? `/${row.uncachedBest.codecType}` : ""} 未缓存`
    : "";

  return (
    <li className={`flex items-start gap-3 p-3 ${row.selected ? "bg-emerald-50/30" : ""}`}>
      <input
        type="checkbox"
        checked={row.selected && !!row.cacheUuid}
        disabled={disabled}
        onChange={(e) => onToggle(e.target.checked)}
        className="mt-1 h-4 w-4 rounded border-zinc-300 text-emerald-600 focus:ring-emerald-500/20 disabled:opacity-40"
      />
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium text-zinc-900">{row.title || "Unknown Track"}</p>
        <p className="truncate text-xs text-zinc-500">
          {row.artists || "Unknown Artist"}
          {formatDuration(row.durationMs) ? ` · ${formatDuration(row.durationMs)}` : ""}
        </p>
        {warning && <p className="mt-0.5 text-xs text-amber-600">{warning}</p>}
      </div>
      <div className="flex flex-col items-end gap-1 sm:flex-row sm:items-center">
        <Select
          value={row.cacheUuid || ""}
          disabled={disabled || candidates.length <= 1}
          onChange={(e) => onVersionChange(e.target.value)}
          className="w-36 text-xs"
        >
          {candidates.map((c) => (
            <option key={c.cacheUuid} value={c.cacheUuid}>
              {`${c.quality} / ${c.codecType || c.extension}`}
              {c.sourceSize ? ` · ${formatBytes(c.sourceSize)}` : ""}
            </option>
          ))}
          {candidates.length === 0 && <option value="">无缓存</option>}
        </Select>
        <Select
          value={row.format || defaultFormat}
          disabled={disabled}
          onChange={(e) => onFormatChange(e.target.value)}
          className="w-28 text-xs"
        >
          {ALLOWED_FORMATS.map((fmt) => (
            <option
              key={fmt}
              value={fmt}
              disabled={(fmt === "mp3" || fmt === "flac") && !mp3TranscoderFound}
            >
              {EXPORT_FORMAT_LABELS[fmt]}
              {(fmt === "mp3" || fmt === "flac") && !mp3TranscoderFound ? "（需 ffmpeg）" : ""}
            </option>
          ))}
        </Select>
      </div>
    </li>
  );
}
