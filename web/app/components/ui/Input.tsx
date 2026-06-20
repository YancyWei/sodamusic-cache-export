"use client";

import { cn } from "@/lib/utils";
import { forwardRef, type InputHTMLAttributes } from "react";

export const Input = forwardRef<
  HTMLInputElement,
  InputHTMLAttributes<HTMLInputElement>
>(({ className, ...props }, ref) => {
  return (
    <input
      ref={ref}
      className={cn(
        "w-full h-10 px-4 text-sm rounded-[var(--radius-md)]",
        "bg-white border border-zinc-200 text-zinc-900 placeholder:text-zinc-400",
        "transition-colors",
        "focus-visible:outline-none focus-visible:border-emerald-500/60 focus-visible:ring-4 focus-visible:ring-emerald-500/10",
        "disabled:opacity-50 disabled:cursor-not-allowed",
        className
      )}
      {...props}
    />
  );
});
Input.displayName = "Input";
