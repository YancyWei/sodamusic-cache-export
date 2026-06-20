"use client";

import { cn } from "@/lib/utils";
import { type HTMLAttributes } from "react";

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: "neutral" | "success" | "warning" | "danger";
}

export function Badge({
  className,
  tone = "neutral",
  children,
  ...props
}: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold",
        {
          "bg-zinc-100 text-zinc-700": tone === "neutral",
          "bg-emerald-100 text-emerald-800": tone === "success",
          "bg-amber-100 text-amber-800": tone === "warning",
          "bg-red-100 text-red-800": tone === "danger",
        },
        className
      )}
      {...props}
    >
      {children}
    </span>
  );
}
