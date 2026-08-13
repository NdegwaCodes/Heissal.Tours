"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useMe } from "@/hooks/use-me";

// KPI tiles are structural stubs — the metrics are populated in later stages
// (leads in Stage 5, quotes in Stage 2–3, payments in Stage 7). They are
// labelled as pending rather than showing fake numbers.
const KPIS: { label: string; pendingStage: string }[] = [
  { label: "Today's Leads", pendingStage: "Stage 5" },
  { label: "Open Quotes", pendingStage: "Stage 2–3" },
  { label: "Upcoming Trips", pendingStage: "Stage 7–8" },
  { label: "Outstanding Payments", pendingStage: "Stage 7" },
];

export default function DashboardPage() {
  const { data: user } = useMe();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-neutral-900">
          Welcome{user?.full_name ? `, ${user.full_name}` : ""}
        </h1>
        <p className="text-sm text-neutral-500">
          Foundation is live. Quotation, CRM, and operations modules arrive in later stages.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {KPIS.map((kpi) => (
          <Card key={kpi.label}>
            <CardHeader>
              <CardDescription>{kpi.label}</CardDescription>
              <CardTitle className="text-2xl text-neutral-300">—</CardTitle>
            </CardHeader>
            <CardContent>
              <span className="text-xs text-neutral-400">Arrives in {kpi.pendingStage}</span>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Getting started</CardTitle>
          <CardDescription>What works today in the foundation.</CardDescription>
        </CardHeader>
        <CardContent className="text-sm text-neutral-600">
          Manage platform <strong>users</strong> and their <strong>roles/permissions</strong> from
          the sidebar. Authentication, RBAC, and audit logging are active — every user and role
          change is recorded in the audit log.
        </CardContent>
      </Card>
    </div>
  );
}
