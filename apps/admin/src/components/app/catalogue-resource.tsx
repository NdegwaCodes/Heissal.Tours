"use client";

// Generic catalogue screen (Stage 2.9): list + create for any CatalogueSpec.
// Permission-aware — the create form only renders with the spec's manage
// permission, and the server enforces it regardless (this is the UI mirror).

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/client-api";
import { hasPermission } from "@/lib/types";
import { useMe } from "@/hooks/use-me";
import type { CatalogueRow, CatalogueSpec, FieldSpec } from "@/lib/catalogue";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";

/** Initial form state from the spec's declared defaults. */
function initialValues(spec: CatalogueSpec): Record<string, string | boolean> {
  const out: Record<string, string | boolean> = {};
  for (const f of spec.fields) {
    if (f.inForm === false) continue;
    if (f.type === "boolean") out[f.name] = f.default === undefined ? true : Boolean(f.default);
    else out[f.name] = f.default === undefined ? "" : String(f.default);
  }
  return out;
}

/** Turn form strings into the JSON the API expects (blank optional -> omitted). */
function toPayload(
  spec: CatalogueSpec,
  values: Record<string, string | boolean>,
): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  for (const f of spec.fields) {
    if (f.inForm === false) continue;
    const raw = values[f.name];
    if (f.type === "boolean") {
      body[f.name] = Boolean(raw);
      continue;
    }
    const text = String(raw ?? "").trim();
    if (text === "") continue; // let the API apply its own default
    // Decimals stay strings so no float ever touches a money value.
    body[f.name] = f.type === "number" ? Number(text) : text;
  }
  return body;
}

export function CatalogueResource({ spec }: { spec: CatalogueSpec }) {
  const { data: me } = useMe();
  const [showCreate, setShowCreate] = useState(false);
  const qc = useQueryClient();

  const rowsQ = useQuery<CatalogueRow[]>({
    queryKey: [spec.path],
    queryFn: () => api.get(spec.path),
  });

  const canManage = me ? hasPermission(me, spec.managePermission) : false;
  const tableFields = spec.fields.filter((f) => f.inTable !== false);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-neutral-900">{spec.title}</h1>
          <p className="text-sm text-neutral-500">{spec.subtitle}</p>
        </div>
        {canManage && (
          <Button onClick={() => setShowCreate((v) => !v)}>
            {showCreate ? "Close" : `New ${spec.singular}`}
          </Button>
        )}
      </div>

      {showCreate && canManage && (
        <CreateCard
          spec={spec}
          onDone={() => {
            setShowCreate(false);
            qc.invalidateQueries({ queryKey: [spec.path] });
          }}
        />
      )}

      <Card>
        <CardContent className="p-0">
          {rowsQ.isLoading ? (
            <p className="p-5 text-sm text-neutral-500">Loading {spec.title.toLowerCase()}…</p>
          ) : rowsQ.isError ? (
            <p className="p-5 text-sm text-red-600">
              {rowsQ.error instanceof ApiError && rowsQ.error.status === 403
                ? `You do not have permission to view ${spec.title.toLowerCase()}.`
                : `Failed to load ${spec.title.toLowerCase()}.`}
            </p>
          ) : (rowsQ.data ?? []).length === 0 ? (
            <p className="p-5 text-sm text-neutral-500">
              No {spec.title.toLowerCase()} yet
              {canManage ? ` — create the first ${spec.singular}.` : "."}
            </p>
          ) : (
            <Table>
              <THead>
                <TR>
                  {tableFields.map((f) => (
                    <TH key={f.name}>{f.label}</TH>
                  ))}
                </TR>
              </THead>
              <TBody>
                {(rowsQ.data ?? []).map((row) => (
                  <TR key={row.id}>
                    {tableFields.map((f) => (
                      <TD key={f.name} className={f.name === "name" ? "font-medium" : undefined}>
                        <Cell field={f} row={row} />
                      </TD>
                    ))}
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

/** One table cell: booleans as state text, lookups resolved to a name. */
function Cell({ field, row }: { field: FieldSpec; row: CatalogueRow }) {
  const value = row[field.name];

  if (field.type === "boolean") {
    return value ? (
      <span className="text-green-600">Yes</span>
    ) : (
      <span className="text-neutral-400">No</span>
    );
  }
  if (field.type === "lookup" && field.lookup) {
    return <LookupName collection={field.lookup} id={value as string | null} />;
  }
  if (field.type === "select") {
    return value ? <Badge>{String(value)}</Badge> : <span className="text-neutral-400">—</span>;
  }
  if (value === null || value === undefined || value === "") {
    return <span className="text-neutral-400">—</span>;
  }
  return <>{String(value)}</>;
}

/** Resolve a foreign id to its name. Cached per collection by react-query. */
function LookupName({ collection, id }: { collection: string; id: string | null }) {
  const q = useQuery<CatalogueRow[]>({
    queryKey: [collection],
    queryFn: () => api.get(collection),
  });
  if (!id) return <span className="text-neutral-400">—</span>;
  const match = (q.data ?? []).find((r) => r.id === id);
  return <>{match?.name ?? "…"}</>;
}

function CreateCard({ spec, onDone }: { spec: CatalogueSpec; onDone: () => void }) {
  const [values, setValues] = useState(() => initialValues(spec));
  const [error, setError] = useState<string | null>(null);
  const formFields = useMemo(() => spec.fields.filter((f) => f.inForm !== false), [spec]);

  const create = useMutation({
    mutationFn: () => api.post(spec.path, toPayload(spec, values)),
    onSuccess: onDone,
    onError: (e) =>
      setError(e instanceof ApiError ? e.message : `Failed to create ${spec.singular}.`),
  });

  const missingRequired = formFields.some(
    (f) => f.required && String(values[f.name] ?? "").trim() === "",
  );

  const set = (name: string, value: string | boolean) =>
    setValues((v) => ({ ...v, [name]: value }));

  return (
    <Card>
      <CardHeader>
        <CardTitle>New {spec.singular}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-3">
          {formFields.map((f) => (
            <div key={f.name} className="space-y-1.5">
              <Label>
                {f.label}
                {f.required && <span className="ml-0.5 text-red-500">*</span>}
              </Label>
              <FieldInput field={f} value={values[f.name]} onChange={(v) => set(f.name, v)} />
            </div>
          ))}
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex gap-2">
          <Button onClick={() => create.mutate()} disabled={create.isPending || missingRequired}>
            Create {spec.singular}
          </Button>
          {missingRequired && (
            <span className="self-center text-xs text-neutral-400">
              Fill the required fields marked *.
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function FieldInput({
  field,
  value,
  onChange,
}: {
  field: FieldSpec;
  value: string | boolean | undefined;
  onChange: (v: string | boolean) => void;
}) {
  const selectClass =
    "h-9 w-full rounded-md border border-neutral-200 bg-white px-3 text-sm " +
    "focus:border-brand focus:outline-none";

  if (field.type === "boolean") {
    return (
      <label className="flex h-9 items-center gap-2 text-sm text-neutral-600">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
          className="size-4 accent-brand"
        />
        {field.label}
      </label>
    );
  }

  if (field.type === "select") {
    return (
      <select
        className={selectClass}
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
      >
        {(field.options ?? []).map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    );
  }

  if (field.type === "lookup" && field.lookup) {
    return <LookupSelect collection={field.lookup} field={field} value={value} onChange={onChange} />;
  }

  return (
    <Input
      value={String(value ?? "")}
      placeholder={field.placeholder}
      // Decimals use text, not number: the value is sent as a string so it
      // reaches the API as an exact decimal rather than a float.
      type={field.type === "number" ? "number" : "text"}
      inputMode={field.type === "decimal" ? "decimal" : undefined}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

function LookupSelect({
  collection,
  field,
  value,
  onChange,
}: {
  collection: string;
  field: FieldSpec;
  value: string | boolean | undefined;
  onChange: (v: string) => void;
}) {
  const q = useQuery<CatalogueRow[]>({
    queryKey: [collection],
    queryFn: () => api.get(collection),
  });
  const selectClass =
    "h-9 w-full rounded-md border border-neutral-200 bg-white px-3 text-sm " +
    "focus:border-brand focus:outline-none";

  return (
    <select
      className={selectClass}
      value={String(value ?? "")}
      onChange={(e) => onChange(e.target.value)}
      disabled={q.isLoading}
    >
      <option value="">{q.isLoading ? "Loading…" : `Select ${field.label.toLowerCase()}`}</option>
      {(q.data ?? []).map((r) => (
        <option key={r.id} value={r.id}>
          {r.name}
        </option>
      ))}
    </select>
  );
}
