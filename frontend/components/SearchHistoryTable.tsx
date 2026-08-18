"use client";

import { Card } from "@/components/Card";
import { Title } from "@/components/Title";
import { Text } from "@/components/Text";
import {
  DataTable,
  type DataTableColumn,
} from "@/components/DataTable";
import type { SearchRecord } from "@/lib/types";

function formatRelativeUpdated(ts: number): string {
  const diffMs = Date.now() - ts;
  const secs = Math.max(0, Math.floor(diffMs / 1000));
  if (secs < 5) return "just now";
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function formatValue(value: number | null): string {
  return value == null ? "—" : String(value);
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div className="text-xs text-[var(--muted)]">{label}</div>
      <div className="text-sm tabular-nums text-[var(--text)]">{value}</div>
    </div>
  );
}

export default function SearchHistoryTable({ searches }: { searches: SearchRecord[] }) {
  return (
    <Card>
      <Title>Recent Searches</Title>
      {searches.length === 0 ? (
        <div className="mt-4">
          <Text>No searches recorded yet</Text>
        </div>
      ) : (
        <>
          {/* Mobile card list */}
          <div className="mt-4 space-y-2 md:hidden">
            {searches.map((s, i) => (
              <div
                key={`${s.ts}-${i}`}
                className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4 shadow-[var(--shadow-card)]"
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-medium text-[var(--text)]">
                      {s.query ?? "—"}
                    </div>
                    <div className="text-xs text-[var(--muted)]">
                      {s.search_type ?? "—"} · {formatRelativeUpdated(s.ts * 1000)}
                    </div>
                  </div>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-3">
                  <Metric label="Latency" value={`${formatValue(s.latency_ms)} ms`} />
                  <Metric label="Cached" value={formatValue(s.torbox_cached)} />
                  <Metric label="Uncached" value={formatValue(s.torbox_uncached)} />
                  <Metric label="Indexers" value={formatValue(s.indexer_count)} />
                </div>
              </div>
            ))}
          </div>

          {/* Desktop table */}
          <div className="mt-4 hidden md:block">
            <DataTable
              rows={searches}
              getRowId={(s, i) => `${s.ts}-${i}`}
              searchPlaceholder="Search recent searches…"
              ariaLabel="Recent searches"
              filters={{
                search_type: Array.from(
                  new Set(searches.map((s) => s.search_type).filter(Boolean) as string[]),
                ).map((t) => ({ value: t, label: t })),
              }}
              columns={
                [
                  {
                    key: "query",
                    header: "Query",
                    sortable: true,
                    cell: (s) => s.query ?? "—",
                    cellClassName: "font-medium",
                  },
                  {
                    key: "search_type",
                    header: "Type",
                    sortable: true,
                    filterable: true,
                    cell: (s) => s.search_type ?? "—",
                  },
                  {
                    key: "latency_ms",
                    header: "Latency (ms)",
                    sortable: true,
                    cellClassName: "text-right tabular-nums",
                    cell: (s) => formatValue(s.latency_ms),
                  },
                  {
                    key: "torbox_cached",
                    header: "Cached",
                    sortable: true,
                    cellClassName: "text-right tabular-nums",
                    cell: (s) => formatValue(s.torbox_cached),
                  },
                  {
                    key: "torbox_uncached",
                    header: "Uncached",
                    sortable: true,
                    cellClassName: "text-right tabular-nums",
                    cell: (s) => formatValue(s.torbox_uncached),
                  },
                  {
                    key: "indexer_count",
                    header: "Indexers",
                    sortable: true,
                    cellClassName: "text-right tabular-nums",
                    cell: (s) => formatValue(s.indexer_count),
                  },
                  {
                    key: "ts",
                    header: "When",
                    sortable: true,
                    accessor: (s) => s.ts,
                    cell: (s) => formatRelativeUpdated(s.ts * 1000),
                    cellClassName: "text-right tabular-nums",
                  },
                ] satisfies DataTableColumn<SearchRecord>[]
              }
            />
          </div>
        </>
      )}
    </Card>
  );
}
