"use client";

import { cn } from "@/lib/utils";
import { forwardRef, type HTMLAttributes } from "react";

export const Card = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => {
    return (
      <div
        ref={ref}
        className={cn(
          "rounded-[var(--radius-md)] bg-white border border-zinc-200 shadow-sm",
          className
        )}
        {...props}
      />
    );
  }
);
Card.displayName = "Card";
