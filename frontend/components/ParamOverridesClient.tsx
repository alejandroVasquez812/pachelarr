"use client";

import { useCallback, useEffect, useState } from "react";
import { Card } from "@/components/Card";
import { Title } from "@/components/Title";
import { Text } from "@/components/Text";
import { Button } from "@/components/Button";
import { Input } from "@/components/Input";
import { Divider } from "@/components/Divider";
import { useToast } from "@/lib/useToast";
import type { ParamOverrides } from "@/lib/types";

interface ParamRow {
  key: string;
  value: string;
}

function paramsToRows(params: Record<string, unknown>): ParamRow[] {
  return Object.entries(params).map(([key, value]) => ({
    key,
    value: typeof value === "string" ? value : JSON.stringify(value),
  }));
}

function rowsToParams(rows: ParamRow[]): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  for (const row of rows) {
    if (!row.key.trim()) continue;
    // Try to parse JSON values (e.g. ["2000"]); fall back to string.
    const trimmed = row.value.trim();
    if (trimmed.startsWith("[") || trimmed.startsWith("{")) {
      try {
        params[row.key.trim()] = JSON.parse(trimmed);
        continue;
      } catch {
        // not valid JSON, store as string
      }
    }
    params[row.key.trim()] = trimmed;
  }
  return params;
}

export default function ParamOverridesClient({ initial }: { initial: ParamOverrides }) {
  const { toast } = useToast();
  const [overrides, setOverrides] = useState<ParamOverrides>(initial);
  const [globalRows, setGlobalRows] = useState<ParamRow[]>(paramsToRows(initial["global"] || {}));
  const [newIndexerId, setNewIndexerId] = useState("");
  const [saving, setSaving] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/admin/overrides", { cache: "no-store" });
      if (res.ok) {
        const data = (await res.json()) as ParamOverrides;
        setOverrides(data);
        setGlobalRows(paramsToRows(data["global"] || {}));
      }
    } catch {
      // ignore fetch errors
    } finally {
      setLoading(false);
    }
  }, []);

  // Keep local state in sync when the initial prop changes (e.g. after navigation).
  useEffect(() => {
    setOverrides(initial);
    setGlobalRows(paramsToRows(initial["global"] || {}));
  }, [initial]);

  const handleSaveGlobal = async () => {
    const params = rowsToParams(globalRows);
    setSaving("global");
    try {
      const res = await fetch("/api/admin/overrides", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope: "global", params }),
      });
      if (res.ok) {
        const result = await res.json();
        setOverrides(result.overrides);
        setGlobalRows(paramsToRows(result.overrides["global"] || {}));
        toast({
          title: "Global overrides saved",
          description: `${Object.keys(params).length} param(s) set.`,
          variant: "success",
        });
      } else {
        const body = await res.text();
        toast({ title: "Save failed", description: body.slice(0, 200), variant: "error" });
      }
    } catch {
      toast({ title: "Error", description: "Failed to save overrides.", variant: "error" });
    } finally {
      setSaving(null);
    }
  };

  const handleDeleteScope = async (scope: string) => {
    if (!window.confirm(`Delete overrides for ${scope}?`)) return;
    setSaving(scope);
    try {
      const res = await fetch(`/api/admin/overrides?scope=${encodeURIComponent(scope)}`, {
        method: "DELETE",
      });
      if (res.ok) {
        const result = await res.json();
        setOverrides(result.overrides);
        if (scope === "global") setGlobalRows([]);
        toast({ title: "Overrides deleted", description: scope, variant: "success" });
      } else {
        toast({ title: "Delete failed", description: "Could not delete overrides.", variant: "error" });
      }
    } catch {
      toast({ title: "Error", description: "Failed to delete overrides.", variant: "error" });
    } finally {
      setSaving(null);
    }
  };

  const handleAddIndexer = async () => {
    const id = newIndexerId.trim();
    if (!id) return;
    const scope = `indexer:${id}`;
    // Initialize with empty params (creates the row)
    setSaving(scope);
    try {
      const res = await fetch("/api/admin/overrides", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope, params: {} }),
      });
      if (res.ok) {
        const result = await res.json();
        setOverrides(result.overrides);
        setNewIndexerId("");
        toast({ title: "Indexer override created", description: scope, variant: "success" });
      } else {
        toast({ title: "Failed", description: "Could not create indexer override.", variant: "error" });
      }
    } catch {
      toast({ title: "Error", description: "Failed to create override.", variant: "error" });
    } finally {
      setSaving(null);
    }
  };

  const globalDirty = JSON.stringify(rowsToParams(globalRows)) !== JSON.stringify(overrides["global"] || {});

  return (
    <Card>
      <div className="flex items-center justify-between gap-4">
        <div>
          <Title>Indexer Param Overrides</Title>
          <Text color="subtle" className="mt-1">
            Force-add or override Torznab query params on every outgoing per-indexer search.
            Global overrides apply to all indexers; per-indexer overrides win over global.
          </Text>
        </div>
        <Button variant="secondary" onClick={refresh} disabled={loading} isLoading={loading}>
          Refresh
        </Button>
      </div>

      <Divider />

      {/* Global overrides */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium text-[var(--text)]">Global overrides (all indexers)</h3>
          <div className="flex gap-2">
            <Button
              variant="ghost"
              onClick={() => handleDeleteScope("global")}
              disabled={!overrides["global"] || saving === "global"}
            >
              Delete
            </Button>
            <Button
              variant="primary"
              onClick={handleSaveGlobal}
              disabled={!globalDirty || saving === "global"}
              isLoading={saving === "global"}
            >
              Save
            </Button>
          </div>
        </div>
        <div className="space-y-2">
          {globalRows.length === 0 && (
            <Text color="subtle" className="text-sm">
              No global overrides. Add a param below.
            </Text>
          )}
          {globalRows.map((row, i) => (
            <div key={i} className="flex items-center gap-2">
              <Input
                placeholder="param key (e.g. cat)"
                value={row.key}
                onChange={(e) => {
                  const next = [...globalRows];
                  next[i] = { ...next[i], key: e.target.value };
                  setGlobalRows(next);
                }}
                className="flex-1"
              />
              <Input
                placeholder='value (e.g. ["2000"] or 100)'
                value={row.value}
                onChange={(e) => {
                  const next = [...globalRows];
                  next[i] = { ...next[i], value: e.target.value };
                  setGlobalRows(next);
                }}
                className="flex-1"
              />
              <Button
                variant="ghost"
                onClick={() => setGlobalRows(globalRows.filter((_, j) => j !== i))}
              >
                Remove
              </Button>
            </div>
          ))}
          <Button variant="light" onClick={() => setGlobalRows([...globalRows, { key: "", value: "" }])}>
            + Add param
          </Button>
        </div>
      </div>

      <Divider />

      {/* Per-indexer overrides */}
      <div className="space-y-3">
        <h3 className="text-sm font-medium text-[var(--text)]">Per-indexer overrides</h3>
        <div className="flex items-center gap-2">
          <Input
            placeholder="Indexer ID (e.g. 5)"
            value={newIndexerId}
            onChange={(e) => setNewIndexerId(e.target.value)}
            className="w-48"
          />
          <Button variant="light" onClick={handleAddIndexer} disabled={!newIndexerId.trim() || !!saving}>
            Add indexer override
          </Button>
        </div>

        {Object.keys(overrides)
          .filter((s) => s.startsWith("indexer:"))
          .map((scope) => {
            const params = overrides[scope] || {};
            const rows = paramsToRows(params);
            return (
              <PerIndexerOverrideCard
                key={scope}
                scope={scope}
                rows={rows}
                saving={saving}
                onSave={async (newRows) => {
                  const params = rowsToParams(newRows);
                  setSaving(scope);
                  try {
                    const res = await fetch("/api/admin/overrides", {
                      method: "PUT",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ scope, params }),
                    });
                    if (res.ok) {
                      const result = await res.json();
                      setOverrides(result.overrides);
                      toast({ title: "Overrides saved", description: scope, variant: "success" });
                    } else {
                      toast({ title: "Save failed", description: "Could not save overrides.", variant: "error" });
                    }
                  } catch {
                    toast({ title: "Error", description: "Failed to save.", variant: "error" });
                  } finally {
                    setSaving(null);
                  }
                }}
                onDelete={() => handleDeleteScope(scope)}
              />
            );
          })}
      </div>
    </Card>
  );
}

function PerIndexerOverrideCard({
  scope,
  rows: initialRows,
  saving,
  onSave,
  onDelete,
}: {
  scope: string;
  rows: ParamRow[];
  saving: string | null;
  onSave: (rows: ParamRow[]) => Promise<void>;
  onDelete: () => void;
}) {
  const [rows, setRows] = useState<ParamRow[]>(initialRows);
  const [dirty, setDirty] = useState(false);

  // Sync when initial rows change after a save.
  useEffect(() => {
    setRows(initialRows);
    setDirty(false);
  }, [initialRows]);

  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-[var(--text)]">{scope}</span>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={onDelete} disabled={saving === scope}>
            Delete
          </Button>
          <Button
            variant="primary"
            onClick={() => onSave(rows)}
            disabled={!dirty || saving === scope}
            isLoading={saving === scope}
          >
            Save
          </Button>
        </div>
      </div>
      <div className="mt-3 space-y-2">
        {rows.length === 0 && (
          <Text color="subtle" className="text-sm">
            No params set. Add a param below.
          </Text>
        )}
        {rows.map((row, i) => (
          <div key={i} className="flex items-center gap-2">
            <Input
              placeholder="param key"
              value={row.key}
              onChange={(e) => {
                const next = [...rows];
                next[i] = { ...next[i], key: e.target.value };
                setRows(next);
                setDirty(true);
              }}
              className="flex-1"
            />
            <Input
              placeholder='value (e.g. ["2000"] or 100)'
              value={row.value}
              onChange={(e) => {
                const next = [...rows];
                next[i] = { ...next[i], value: e.target.value };
                setRows(next);
                setDirty(true);
              }}
              className="flex-1"
            />
            <Button
              variant="ghost"
              onClick={() => {
                setRows(rows.filter((_, j) => j !== i));
                setDirty(true);
              }}
            >
              Remove
            </Button>
          </div>
        ))}
        <Button variant="light" onClick={() => { setRows([...rows, { key: "", value: "" }]); setDirty(true); }}>
          + Add param
        </Button>
      </div>
    </div>
  );
}