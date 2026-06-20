"use client";

import { Card } from "@/app/components/ui/Card";
import { ListDashes, MagnifyingGlass } from "@phosphor-icons/react";

interface ActionCenterProps {
  showTarget: boolean;
  onShowTarget: () => void;
}

export function ActionCenter({ showTarget, onShowTarget }: ActionCenterProps) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2" role="group" aria-label="操作模式">
      <Card
        role="button"
        tabIndex={0}
        aria-pressed={!showTarget}
        onClick={() => showTarget && onShowTarget()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            showTarget && onShowTarget();
          }
        }}
        className={`cursor-pointer p-4 transition-colors hover:border-emerald-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/30 focus-visible:ring-offset-2 ${
          showTarget ? "border-zinc-200" : "border-emerald-200 bg-emerald-50/20"
        }`}
      >
        <div className="flex items-center gap-3">
          <div
            className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-[var(--radius-md)] ${
              showTarget
                ? "bg-zinc-100 text-zinc-700"
                : "bg-emerald-100 text-emerald-600"
            }`}
          >
            <ListDashes weight="bold" size={20} />
          </div>
          <div>
            <h3 className="font-semibold text-zinc-900">浏览全部缓存</h3>
            <p className="text-xs text-zinc-500">查看并选择要导出的歌曲</p>
          </div>
        </div>
      </Card>

      <Card
        role="button"
        tabIndex={0}
        aria-pressed={showTarget}
        onClick={() => !showTarget && onShowTarget()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            !showTarget && onShowTarget();
          }
        }}
        className={`cursor-pointer p-4 transition-colors hover:border-emerald-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/30 focus-visible:ring-offset-2 ${
          showTarget ? "border-emerald-200 bg-emerald-50/20" : "border-zinc-200"
        }`}
      >
        <div className="flex items-center gap-3">
          <div
            className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-[var(--radius-md)] ${
              showTarget
                ? "bg-emerald-100 text-emerald-600"
                : "bg-zinc-100 text-zinc-700"
            }`}
          >
            <MagnifyingGlass weight="bold" size={20} />
          </div>
          <div>
            <h3 className="font-semibold text-zinc-900">搜索指定歌曲</h3>
            <p className="text-xs text-zinc-500">等待缓存出现后导出目标歌曲</p>
          </div>
        </div>
      </Card>
    </div>
  );
}
