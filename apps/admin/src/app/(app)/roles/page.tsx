"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/client-api";
import { hasPermission, type Permission, type Role } from "@/lib/types";
import { useMe } from "@/hooks/use-me";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function RolesPage() {
  const { data: me } = useMe();
  const rolesQ = useQuery<Role[]>({ queryKey: ["roles"], queryFn: () => api.get("roles") });
  const permsQ = useQuery<Permission[]>({
    queryKey: ["permissions"],
    queryFn: () => api.get("permissions"),
  });
  const canEdit = me ? hasPermission(me, "role:update") : false;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-neutral-900">Roles &amp; permissions</h1>
        <p className="text-sm text-neutral-500">
          Roles bundle fine-grained permissions. Assign roles to users on the Users page.
        </p>
      </div>

      {rolesQ.isLoading ? (
        <p className="text-sm text-neutral-500">Loading roles…</p>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {(rolesQ.data ?? []).map((role) => (
            <RoleCard
              key={role.id}
              role={role}
              allPermissions={permsQ.data ?? []}
              canEdit={canEdit}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function RoleCard({
  role,
  allPermissions,
  canEdit,
}: {
  role: Role;
  allPermissions: Permission[];
  canEdit: boolean;
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState<string[]>(role.permissions.map((p) => p.key));

  const save = useMutation({
    mutationFn: () => api.put(`roles/${role.id}/permissions`, { permission_keys: selected }),
    onSuccess: () => {
      setEditing(false);
      qc.invalidateQueries({ queryKey: ["roles"] });
    },
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              {role.name}
              {role.is_system && <Badge>system</Badge>}
            </CardTitle>
            <CardDescription>{role.description ?? role.key}</CardDescription>
          </div>
          {canEdit &&
            (editing ? (
              <div className="flex gap-2">
                <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>
                  Save
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setSelected(role.permissions.map((p) => p.key));
                    setEditing(false);
                  }}
                >
                  Cancel
                </Button>
              </div>
            ) : (
              <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
                Edit
              </Button>
            ))}
        </div>
      </CardHeader>
      <CardContent>
        {editing ? (
          <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
            {allPermissions.map((p) => {
              const on = selected.includes(p.key);
              return (
                <label key={p.key} className="flex items-center gap-2 text-sm text-neutral-700">
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() =>
                      setSelected((s) =>
                        on ? s.filter((k) => k !== p.key) : [...s, p.key],
                      )
                    }
                  />
                  <span className="font-mono text-xs">{p.key}</span>
                </label>
              );
            })}
          </div>
        ) : role.permissions.length ? (
          <div className="flex flex-wrap gap-1">
            {role.permissions.map((p) => (
              <Badge key={p.id} className="font-mono">
                {p.key}
              </Badge>
            ))}
          </div>
        ) : (
          <span className="text-sm text-neutral-400">No permissions</span>
        )}
      </CardContent>
    </Card>
  );
}
