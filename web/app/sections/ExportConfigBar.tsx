"use client";

import { Card } from "@/app/components/ui/Card";
import { Button } from "@/app/components/ui/Button";
import { Input } from "@/app/components/ui/Input";
import { useExportConfig } from "@/app/contexts/ExportConfigContext";
import {
  EXPORT_FORMAT_LABELS,
  EXPORT_FORMAT_DESCRIPTIONS,
  ALLOWED_FORMATS,
  DEFAULT_MP3_BITRATE,
} from "@/lib/constants";
import { openPath } from "@/lib/api";
import { FolderOpen, CheckCircle } from "@phosphor-icons/react";

interface ExportConfigBarProps {
  mp3TranscoderFound: boolean;
  outputDir: string;
  exportable: number;
  selected: number;
  ready: boolean;
  jobRunning: boolean;
  onExport: () => void;
}

export function ExportConfigBar({
  mp3TranscoderFound,
  outputDir,
  exportable,
  selected,
  ready,
  jobRunning,
  onExport,
}: ExportConfigBarProps) {
  const config = useExportConfig();
  const exportLabel =
    selected > 0 ? `导出已选 ${selected} 首` : `导出全部 ${exportable} 首`;

  return (
    <Card className="p-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex-1 space-y-4">
          <div>
            <label className="mb-2 block text-xs font-medium text-zinc-600">
              导出格式
            </label>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              {ALLOWED_FORMATS.map((fmt) => {
                const disabled =
                  (fmt === "mp3" || fmt === "flac") && !mp3TranscoderFound;
                const isSelected = config.outputFormat === fmt;
                return (
                  <button
                    key={fmt}
                    type="button"
                    disabled={disabled}
                    onClick={() => config.setOutputFormat(fmt)}
                    className={`relative rounded-[var(--radius-md)] border p-3 text-left transition-colors ${
                      isSelected
                        ? "border-emerald-500 bg-emerald-50/40"
                        : disabled
                        ? "border-zinc-100 bg-zinc-50 opacity-60 cursor-not-allowed"
                        : "border-zinc-200 bg-white hover:border-zinc-300"
                    }`}
                  >
                    <div className="flex items-start justify-between">
                      <span className="font-semibold text-zinc-900">
                        {EXPORT_FORMAT_LABELS[fmt]}
                      </span>
                      {isSelected && (
                        <CheckCircle
                          weight="fill"
                          size={16}
                          className="text-emerald-600"
                        />
                      )}
                    </div>
                    <p className="mt-1 text-xs text-zinc-500">
                      {EXPORT_FORMAT_DESCRIPTIONS[fmt]}
                    </p>
                    {disabled && (
                      <p className="mt-1 text-xs text-amber-600">
                        需要安装 ffmpeg
                      </p>
                    )}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-4 text-sm">
            {config.outputFormat === "mp3" && (
              <div className="flex items-center gap-2">
                <label className="text-xs font-medium text-zinc-600">
                  MP3 码率
                </label>
                <Input
                  type="number"
                  min={64}
                  max={320}
                  step={32}
                  value={config.mp3Bitrate}
                  onChange={(e) =>
                    config.setMp3Bitrate(
                      Number(e.target.value) || DEFAULT_MP3_BITRATE
                    )
                  }
                  className="w-20"
                />
                <span className="text-xs text-zinc-400">kbps</span>
              </div>
            )}

            <label className="flex cursor-pointer items-center gap-2 text-zinc-700">
              <input
                type="checkbox"
                checked={config.requireOutputMatch}
                onChange={(e) => config.setRequireOutputMatch(e.target.checked)}
                className="h-4 w-4 rounded border-zinc-300 text-emerald-600"
              />
              输出匹配
            </label>

            <label className="flex cursor-pointer items-center gap-2 text-zinc-700">
              <input
                type="checkbox"
                checked={config.overwrite}
                onChange={(e) => config.setOverwrite(e.target.checked)}
                className="h-4 w-4 rounded border-zinc-300 text-emerald-600"
              />
              覆盖已有文件
            </label>
          </div>
        </div>

        <div className="flex flex-col items-start gap-3 lg:items-end lg:pt-6">
          {outputDir && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => openPath(outputDir, true)}
            >
              <FolderOpen size={16} />
              <span className="max-w-[16rem] truncate" title={outputDir}>
                {outputDir}
              </span>
            </Button>
          )}
          <Button
            variant="primary"
            size="md"
            className="min-w-[10rem]"
            disabled={!ready || jobRunning || exportable === 0}
            isLoading={jobRunning}
            onClick={onExport}
          >
            {jobRunning ? "任务运行中" : exportLabel}
          </Button>
        </div>
      </div>
    </Card>
  );
}
