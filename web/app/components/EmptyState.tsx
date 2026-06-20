"use client";

import { cn } from "@/lib/utils";

interface EmptyStateProps {
  title: string;
  description?: string;
  className?: string;
}

export function EmptyState({ title, description, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center py-12 text-center",
        className
      )}
    >
      <div className="mb-4 h-16 w-16 rounded-full bg-slate-100" />
      <h4 className="text-base font-semibold text-zinc-900">{title}</h4>
      {description && (
        <p className="mt-1 max-w-[40ch] text-sm text-zinc-500">{description}</p>
      )}
    </div>
  );
}
