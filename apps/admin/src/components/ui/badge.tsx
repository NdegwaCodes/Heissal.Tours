import * as React from "react";
import { cn } from "@/lib/utils";

export function Badge({ className, ...props }: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border border-neutral-200 bg-neutral-50 px-2 py-0.5 text-xs font-medium text-neutral-700",
        className,
      )}
      {...props}
    />
  );
}
