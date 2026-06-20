"use client";

import { useEffect, useRef, useState } from "react";
import { Card } from "@/app/components/ui/Card";
import { Button } from "@/app/components/ui/Button";
import { Badge } from "@/app/components/ui/Badge";
import { Collapsible } from "@/app/components/ui/Collapsible";
import { formatElapsed, formatPercent } from "@/lib/format";
import { openPath } from "@/lib/api";
import { FolderOpen, FileText, XCircle, CheckCircle, CaretDown, CaretUp } from "@phosphor-icons/react";
import type { JobState } from "@/hooks/useJob";

interface JobProgressProps {
  state: JobState;
}

export function JobProgress({ state }: JobProgressProps) {
  const { job } = state;
  const logsRef = useRef<HTMLPreElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const [logsOpen, setLogsOpen] = useState(false);
  const scrolledRef = useRef(false);

  useEffect(() => {
    if (logsRef.current && logsOpen) {
      logsRef.current.scrollTop = logsRef.current.scrollHeight;
    }
  }, [job?.logs, logsOpen]);

  useEffect(() => {
    if (job && !scrolledRef.current && cardRef.current) {
      scrolledRef.current = true;
      cardRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    if (!job) {
      scrolledRef.current = false;
    }
  }, [job]);

  if (!job) return null;

  const elapsed =
    job.status === "running"
      ? formatElapsed(Date.now() / 1000 - job.startedAt)
      : job.finishedAt
      ? formatElapsed(job.finishedAt - job.startedAt)
      : "0s";

  return (
    <div
      ref={cardRef}
      className="animate-fade-in-up"
    >
      <Card className="p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <h3 className="font-semibold text-zinc-900">任务进度</h3>
              <StatusBadge status={job.status} />
            </div>
            <p className="mt-0.5 text-sm text-zinc-500">{job.metrics.message || "处理中"}</p>
          </div>
          {job.outputDir && (
            <Button variant="ghost" size="sm" onClick={() => openPath(job.outputDir, true)}>
              <FolderOpen size={16} />
              打开目录
            </Button>
          )}
        </div>

        <div className="mt-4">
          <div className="flex items-center justify-between text-sm">
            <span className="text-zinc-600">{formatPercent(job.metrics.current, job.metrics.total)}</span>
            <span className="font-mono text-xs text-zinc-400">
              {job.metrics.current} / {job.metrics.total}
            </span>
          </div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-zinc-100" role="progressbar" aria-valuenow={job.metrics.current} aria-valuemin={0} aria-valuemax={job.metrics.total} aria-label="导出进度">
            <div
              className="h-full origin-left rounded-full bg-emerald-500 transition-transform duration-500 ease-out"
              style={{
                transform: `scaleX(${job.metrics.total > 0 ? job.metrics.current / job.metrics.total : 0})`,
              }}
            />
          </div>
        </div>

        <div className="mt-4 grid grid-cols-4 gap-3">
          <Metric label="已导出" value={String(job.metrics.exported)} />
          <Metric label="跳过" value={String(job.metrics.skipped)} />
          <Metric label="总计" value={String(job.metrics.total)} />
          <Metric label="耗时" value={elapsed} />
        </div>

        <div className="mt-4">
          <button
            onClick={() => setLogsOpen((v) => !v)}
            aria-expanded={logsOpen}
            className="flex items-center gap-1 text-xs font-medium text-zinc-500 hover:text-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/30 focus-visible:ring-offset-2"
          >
            <FileText size={14} />
            {logsOpen ? "隐藏日志" : "查看日志"}
            {logsOpen ? <CaretUp size={14} /> : <CaretDown size={14} />}
          </button>
          <Collapsible open={logsOpen}>
            <pre
              ref={logsRef}
              role="log"
              aria-live="polite"
              className="mt-2 h-48 overflow-auto rounded-[var(--radius-md)] bg-zinc-950 p-3 font-mono text-xs leading-relaxed text-zinc-300"
            >
              {job.logs?.length ? job.logs.join("\n") : "暂无日志。"}
              {job.error && <span className="block text-red-400">{job.error}</span>}
            </pre>
          </Collapsible>
        </div>
      </Card>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  if (status === "running") {
    return <Badge tone="success">运行中</Badge>;
  }
  if (status === "completed") {
    return (
      <Badge tone="success">
        <CheckCircle weight="fill" size={12} className="mr-1" />
        完成
      </Badge>
    );
  }
  return (
    <Badge tone="danger">
      <XCircle weight="fill" size={12} className="mr-1" />
      失败
    </Badge>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[var(--radius-md)] bg-zinc-50 px-3 py-2 text-center">
      <p className="text-xs text-zinc-500">{label}</p>
      <p className="mt-0.5 font-mono text-sm font-semibold text-zinc-900">{value}</p>
    </div>
  );
}
