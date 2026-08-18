"use client";

import { useState } from "react";
import { Card } from "@/components/Card";
import { Title } from "@/components/Title";
import { Badge } from "@/components/Badge";
import { Text } from "@/components/Text";
import { Button } from "@/components/Button";
import { useToast } from "@/lib/useToast";
import {
  DataTable,
  type DataTableColumn,
} from "@/components/DataTable";
import type { IndexerStat } from "@/lib/types";

function formatLatency(ms: number): string {
  return String(Math.round(ms));
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div className="text-xs text-[var(--muted)]">{label}</div>
      <div className="text-sm tabular-nums text-[var(--text)]">{value}</div>
    </div>
  );
}

export default function IndexerTable({ indexers }: { indexers: IndexerStat[] }) {
  const { toast } = useToast();
  const [resetting, setResetting] = useState<number | "all" | null>(null);

  const handleReset = async (id: number | "all") => {
    const msg = id === "all"
      ? "Reset ALL per-indexer stats? This cannot be undone."
      : `Reset stats for indexer ${id}?`;
    if (!window.confirm(msg)) return;
    setResetting(id);
    try {
      const endpoint = id === "all" ? "statsz/reset/indexers" : `statsz/reset/indexers/${id}`;
      const res = await fetch(`/api/admin/${endpoint}`, { method: "POST" });
      if (res.ok) {
        toast({ title: "Stats reset", description: id === "all" ? "All indexer stats cleared." : `Indexer ${id} stats cleared.`, variant: "success" });
      } else {
        toast({ title: "Failed", description: "Could not reset stats.", variant: "error" });
      }
    } catch {
      toast({ title: "Error", description: "Request failed.", variant: "error" });
    } finally {
      setResetting(null);
    }
  };

  return (
    <Card>
      <div className="flex items-center justify-between gap-3">
        <Title>Per-Indexer Analysis</Title>
        {indexers.length > 0 && (
          <Button
            variant="ghost"
            onClick={() => handleReset("all")}
            disabled={resetting !== null}
            isLoading={resetting === "all"}
          >
            Reset all
          </Button>
        )}
      </div>
      {indexers.length === 0 ? (
        <div className="mt-4">
          <Text>No indexer listing cached yet — a search will populate it.</Text>
        </div>
      ) : (
        <>
          {/* Mobile card list */}
          <div className="mt-4 space-y-2 md:hidden">
            {indexers.map((ix) => (
              <div
                key={String(ix.id)}
                className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4 shadow-[var(--shadow-card)]"
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-medium text-[var(--text)]">{ix.name}</div>
                    <div className="text-xs text-[var(--muted)]">{ix.protocol}</div>
                  </div>
                  <Badge variant={ix.enabled ? "success" : "error"}>
                    {ix.enabled ? "Enabled" : "Disabled"}
                  </Badge>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-3">
                  <Metric label="Requests" value={ix.requests} />
                  <Metric label="Avg latency" value={`${formatLatency(ix.avg_latency_ms)} ms`} />
                  <Metric label="Last latency" value={`${formatLatency(ix.last_latency_ms)} ms`} />
                  <Metric label="Cached" value={ix.cached} />
                  <Metric label="Uncached" value={ix.uncached} />
                  <Metric label="Errors" value={ix.errors} />
                </div>
                <div className="mt-3">
                  <Button
                    variant="ghost"
                    onClick={() => handleReset(ix.id as number)}
                    disabled={resetting !== null}
                    isLoading={resetting === ix.id}
                  >
                    Reset stats
                  </Button>
                </div>
              </div>
            ))}
          </div>

          {/* Desktop table */}
          <div className="mt-4 hidden md:block">
            <DataTable
              rows={indexers}
              getRowId={(ix) => String(ix.id)}
              searchPlaceholder="Search indexers…"
              ariaLabel="Per-indexer statistics"
              filters={{
                enabled: [
                  { value: "true", label: "Enabled" },
                  { value: "false", label: "Disabled" },
                ],
                protocol: Array.from(
                  new Set(indexers.map((ix) => ix.protocol)),
                ).map((p) => ({ value: p, label: p })),
              }}
              columns={
                [
                  {
                    key: "name",
                    header: "Name",
                    sortable: true,
                    cellClassName: "font-medium",
                  },
                  {
                    key: "protocol",
                    header: "Protocol",
                    sortable: true,
                    filterable: true,
                  },
                  {
                    key: "enabled",
                    header: "Enabled",
                    sortable: true,
                    filterable: true,
                    cell: (ix) => (
                      <Badge variant={ix.enabled ? "success" : "error"}>
                        {ix.enabled ? "Yes" : "No"}
                      </Badge>
                    ),
                  },
                  {
                    key: "requests",
                    header: "Requests",
                    sortable: true,
                    cellClassName: "text-right",
                  },
                  {
                    key: "avg_latency_ms",
                    header: "Avg Latency (ms)",
                    sortable: true,
                    accessor: (ix) => ix.avg_latency_ms,
                    cell: (ix) => formatLatency(ix.avg_latency_ms),
                    cellClassName: "text-right",
                  },
                  {
                    key: "last_latency_ms",
                    header: "Last Latency (ms)",
                    sortable: true,
                    accessor: (ix) => ix.last_latency_ms,
                    cell: (ix) => formatLatency(ix.last_latency_ms),
                    cellClassName: "text-right",
                  },
                  {
                    key: "cached",
                    header: "Cached",
                    sortable: true,
                    cellClassName: "text-right",
                  },
                  {
                    key: "uncached",
                    header: "Uncached",
                    sortable: true,
                    cellClassName: "text-right",
                  },
                  {
                    key: "errors",
                    header: "Errors",
                    sortable: true,
                    cellClassName: "text-right",
                  },
                  {
                    key: "actions",
                    header: "Actions",
                    searchable: false,
                    sortable: false,
                    cell: (ix) => (
                      <div className="text-right">
                        <Button
                          variant="ghost"
                          onClick={() => handleReset(ix.id as number)}
                          disabled={resetting !== null}
                          isLoading={resetting === ix.id}
                        >
                          Reset
                        </Button>
                      </div>
                    ),
                    cellClassName: "text-right",
                  },
                ] satisfies DataTableColumn<IndexerStat>[]
              }
            />
          </div>
        </>
      )}
    </Card>
  );
}
