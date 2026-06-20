"use client";

import { cn } from "@/lib/utils";
import { type ReactNode } from "react";

interface CollapsibleProps {
  open: boolean;
  children: ReactNode;
  className?: string;
}

export function Collapsible({ open, children, className }: CollapsibleProps) {
  return (
    <div
      className={cn(
        "grid transition-[grid-template-rows] duration-300 ease-in-out",
        open ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
        className
      )}
    >
      <div className="overflow-hidden">{children}</div>
    </div>
  );
}
