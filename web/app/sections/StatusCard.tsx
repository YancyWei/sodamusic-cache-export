"use client";

import { Card } from "@/app/components/ui/Card";
import { Badge } from "@/app/components/ui/Badge";
import { Button } from "@/app/components/ui/Button";
import { FolderOpen, CheckCircle, WarningCircle } from "@phosphor-icons/react";
import type { PreflightStatusPayload } from "@/lib/api";

interface StatusCardProps {
  data: PreflightStatusPayload | null;
  loading: boolean;
  onOpenOutput: () => void;
}

export function StatusCard({ data, loading, onOpenOutput }: StatusCardProps) {
  const ready = data?.ready ?? false;
  const exportable = data?.sources?.exportable ?? 0;
  const outputDir = data?.outputDir ?? "";
  const firstError = data?.errors?.[0] || data?.warnings?.[0];
  const failedCheck = data?.checks?.find((c) => !c.ok);

  return (
    <Card className="p-6">
      <div className="flex items-start gap-4">
        <div
          className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-[var(--radius-md)] ${
            ready ? "bg-emerald-50 text-emerald-600" : "bg-amber-50 text-amber-600"
          }`}
        >
          {ready ? (
            <CheckCircle weight="fill" size={26} />
          ) : (
            <WarningCircle weight="fill" size={26} />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold text-zinc-900">
              {loading ? "正在检测环境…" : ready ? "已就绪" : "未就绪"}
            </h2>
            <Badge tone={ready ? "success" : loading ? "neutral" : "warning"}>
              {loading ? "检测中" : ready ? "可导出" : "需处理"}
            </Badge>
          </div>
          <p className="mt-1 text-sm text-zinc-500">
            {loading
              ? "正在校验缓存目录与解密模块。"
              : ready
              ? `检测到 ${exportable} 首可导出歌曲。`
              : firstError || failedCheck?.detail || "请检查环境依赖是否安装完整。"}
          </p>
          {outputDir && (
            <div className="mt-3 flex items-center gap-2">
              <Button variant="ghost" size="sm" onClick={onOpenOutput}>
                <FolderOpen size={16} />
                打开输出目录
              </Button>
              <span className="truncate text-xs text-zinc-400" title={outputDir}>
                {outputDir}
              </span>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}
