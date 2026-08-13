"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/client-api";
import { hasPermission, type Role, type User } from "@/lib/types";
import { useMe } from "@/hooks/use-me";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";

export default function UsersPage() {
  const { data: me } = useMe();
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);

  const usersQ = useQuery<User[]>({ queryKey: ["users"], queryFn: () => api.get("users") });
  const rolesQ = useQuery<Role[]>({ queryKey: ["roles"], queryFn: () => api.get("roles") });

  const canCreate = me ? hasPermission(me, "user:create") : false;
  const canManageRoles = me ? hasPermission(me, "user:manage_roles") : false;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-neutral-900">Users</h1>
          <p className="text-sm text-neutral-500">Platform users and their roles.</p>
        </div>
        {canCreate && (
          <Button onClick={() => setShowCreate((v) => !v)}>
            {showCreate ? "Close" : "New user"}
          </Button>
        )}
      </div>

      {showCreate && canCreate && (
        <CreateUserCard
          roles={rolesQ.data ?? []}
          onDone={() => {
            setShowCreate(false);
            qc.invalidateQueries({ queryKey: ["users"] });
          }}
        />
      )}

      <Card>
        <CardContent className="p-0">
          {usersQ.isLoading ? (
            <p className="p-5 text-sm text-neutral-500">Loading users…</p>
          ) : usersQ.isError ? (
            <p className="p-5 text-sm text-red-600">Failed to load users.</p>
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Email</TH>
                  <TH>Name</TH>
                  <TH>Roles</TH>
                  <TH>Status</TH>
                  {canManageRoles && <TH>Actions</TH>}
                </TR>
              </THead>
              <TBody>
                {(usersQ.data ?? []).map((u) => (
                  <UserRow
                    key={u.id}
                    user={u}
                    roles={rolesQ.data ?? []}
                    canManageRoles={canManageRoles}
                  />
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function UserRow({
  user,
  roles,
  canManageRoles,
}: {
  user: User;
  roles: Role[];
  canManageRoles: boolean;
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState<string[]>(user.roles.map((r) => r.key));

  const save = useMutation({
    mutationFn: () => api.put(`users/${user.id}/roles`, { role_keys: selected }),
    onSuccess: () => {
      setEditing(false);
      qc.invalidateQueries({ queryKey: ["users"] });
    },
  });

  return (
    <TR>
      <TD className="font-medium">{user.email}</TD>
      <TD>{user.full_name ?? "—"}</TD>
      <TD>
        {editing ? (
          <div className="flex flex-wrap gap-2">
            {roles.map((r) => {
              const on = selected.includes(r.key);
              return (
                <button
                  key={r.key}
                  type="button"
                  onClick={() =>
                    setSelected((s) =>
                      on ? s.filter((k) => k !== r.key) : [...s, r.key],
                    )
                  }
                  className={
                    "rounded-full border px-2 py-0.5 text-xs " +
                    (on
                      ? "border-brand bg-brand/10 text-brand"
                      : "border-neutral-200 text-neutral-500")
                  }
                >
                  {r.key}
                </button>
              );
            })}
          </div>
        ) : user.roles.length ? (
          <div className="flex flex-wrap gap-1">
            {user.roles.map((r) => (
              <Badge key={r.id}>{r.key}</Badge>
            ))}
            {user.is_superuser && <Badge className="border-brand text-brand">superuser</Badge>}
          </div>
        ) : user.is_superuser ? (
          <Badge className="border-brand text-brand">superuser</Badge>
        ) : (
          <span className="text-neutral-400">none</span>
        )}
      </TD>
      <TD>
        {user.is_active ? (
          <span className="text-green-600">Active</span>
        ) : (
          <span className="text-neutral-400">Disabled</span>
        )}
      </TD>
      {canManageRoles && (
        <TD>
          {editing ? (
            <div className="flex gap-2">
              <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>
                Save
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setSelected(user.roles.map((r) => r.key));
                  setEditing(false);
                }}
              >
                Cancel
              </Button>
            </div>
          ) : (
            <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
              Manage roles
            </Button>
          )}
        </TD>
      )}
    </TR>
  );
}

function CreateUserCard({ roles, onDone }: { roles: Role[]; onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [roleKeys, setRoleKeys] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () =>
      api.post("users", {
        email,
        full_name: fullName || null,
        password,
        role_keys: roleKeys,
      }),
    onSuccess: onDone,
    onError: (e) =>
      setError(e instanceof ApiError ? e.message : "Failed to create user."),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>New user</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="space-y-1.5">
            <Label>Email</Label>
            <Input value={email} onChange={(e) => setEmail(e.target.value)} type="email" />
          </div>
          <div className="space-y-1.5">
            <Label>Full name</Label>
            <Input value={fullName} onChange={(e) => setFullName(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label>Password</Label>
            <Input
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              type="password"
            />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label>Roles</Label>
          <div className="flex flex-wrap gap-2">
            {roles.map((r) => {
              const on = roleKeys.includes(r.key);
              return (
                <button
                  key={r.key}
                  type="button"
                  onClick={() =>
                    setRoleKeys((s) =>
                      on ? s.filter((k) => k !== r.key) : [...s, r.key],
                    )
                  }
                  className={
                    "rounded-full border px-2 py-0.5 text-xs " +
                    (on
                      ? "border-brand bg-brand/10 text-brand"
                      : "border-neutral-200 text-neutral-500")
                  }
                >
                  {r.key}
                </button>
              );
            })}
          </div>
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex gap-2">
          <Button
            onClick={() => create.mutate()}
            disabled={create.isPending || !email || password.length < 8}
          >
            Create user
          </Button>
          <span className="self-center text-xs text-neutral-400">
            Password must be at least 8 characters.
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
