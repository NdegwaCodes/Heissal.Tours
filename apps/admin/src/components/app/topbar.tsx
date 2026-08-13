"use client";

import { useRouter } from "next/navigation";
import { LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { User } from "@/lib/types";

export function Topbar({ user }: { user: User }) {
  const router = useRouter();

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.replace("/login");
    router.refresh();
  }

  return (
    <header className="flex h-14 items-center justify-between border-b border-neutral-200 bg-white px-5">
      <div className="text-sm text-neutral-500">Heissal Tours &amp; Travel — Admin</div>
      <div className="flex items-center gap-4">
        <div className="text-right">
          <div className="text-sm font-medium text-neutral-900">
            {user.full_name ?? user.email}
          </div>
          <div className="text-xs text-neutral-400">{user.email}</div>
        </div>
        <Button variant="ghost" size="icon" onClick={logout} title="Sign out">
          <LogOut className="h-4 w-4" />
        </Button>
      </div>
    </header>
  );
}
