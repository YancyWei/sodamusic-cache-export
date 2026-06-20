"use client";

import { cn } from "@/lib/utils";
import { type ReactNode } from "react";
import { MusicNotesSimple } from "@phosphor-icons/react";

interface EmptyStateProps {
  title: string;
  description?: string;
  icon?: ReactNode;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({ title, description, icon, action, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center py-12 text-center",
        className
      )}
    >
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-zinc-100 text-zinc-400">
        {icon || <MusicNotesSimple size={28} weight="light" />}
      </div>
      <h4 className="text-base font-semibold text-zinc-900">{title}</h4>
      {description && (
        <p className="mt-1 max-w-[40ch] text-sm text-zinc-500">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
