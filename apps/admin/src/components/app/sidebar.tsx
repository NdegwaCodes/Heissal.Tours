"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { NAV_ITEMS } from "./nav";
import { hasPermission, type User } from "@/lib/types";
import { cn } from "@/lib/utils";

export function Sidebar({ user }: { user: User }) {
  const pathname = usePathname();
  const items = NAV_ITEMS.filter((i) => !i.permission || hasPermission(user, i.permission));

  return (
    <aside className="hidden w-60 shrink-0 flex-col border-r border-neutral-200 bg-white md:flex">
      <div className="flex h-14 items-center gap-2 border-b border-neutral-200 px-5">
        <div className="h-6 w-6 rounded bg-brand" />
        <span className="font-semibold text-neutral-900">Heissal</span>
      </div>
      <nav className="flex-1 space-y-1 p-3">
        {items.map((item, index) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          const Icon = item.icon;
          // Print a group heading the first time a section appears.
          const heading =
            item.section && item.section !== items[index - 1]?.section ? item.section : null;
          return (
            <div key={`${item.href}-group`}>
              {heading && (
                <p className="px-3 pb-1 pt-4 text-xs font-medium uppercase tracking-wide text-neutral-400">
                  {heading}
                </p>
              )}
              <Link
                href={item.href}
                className={cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  active
                    ? "bg-brand/10 text-brand"
                    : "text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900",
                )}
              >
                <Icon className="h-4 w-4" />
                {item.label}
              </Link>
            </div>
          );
        })}
      </nav>
      <div className="border-t border-neutral-200 p-3 text-xs text-neutral-400">
        Stage 1 · Foundation
      </div>
    </aside>
  );
}
