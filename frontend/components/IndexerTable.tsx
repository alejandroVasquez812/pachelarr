import { Card } from "@/components/Card";
import { Title } from "@/components/Title";
import { Badge } from "@/components/Badge";
import { Text } from "@/components/Text";
import {
  Table,
  TableHead,
  TableHeaderCell,
  TableBody,
  TableRow,
  TableCell,
} from "@/components/Table";
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
  return (
    <Card>
      <div className="flex items-center gap-3">
        <Title>Per-Indexer Analysis</Title>
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
              </div>
            ))}
          </div>

          {/* Desktop table */}
          <div className="mt-4 hidden md:block">
            <Table>
              <TableHead>
                <TableRow>
                  <TableHeaderCell>Name</TableHeaderCell>
                  <TableHeaderCell>Protocol</TableHeaderCell>
                  <TableHeaderCell>Enabled</TableHeaderCell>
                  <TableHeaderCell className="text-right">Requests</TableHeaderCell>
                  <TableHeaderCell className="text-right">Avg Latency (ms)</TableHeaderCell>
                  <TableHeaderCell className="text-right">Last Latency (ms)</TableHeaderCell>
                  <TableHeaderCell className="text-right">Cached</TableHeaderCell>
                  <TableHeaderCell className="text-right">Uncached</TableHeaderCell>
                  <TableHeaderCell className="text-right">Errors</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {indexers.map((ix) => (
                  <TableRow key={String(ix.id)}>
                    <TableCell className="font-medium">{ix.name}</TableCell>
                    <TableCell>{ix.protocol}</TableCell>
                    <TableCell>
                      <Badge variant={ix.enabled ? "success" : "error"}>
                        {ix.enabled ? "Yes" : "No"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right">{ix.requests}</TableCell>
                    <TableCell className="text-right">{formatLatency(ix.avg_latency_ms)}</TableCell>
                    <TableCell className="text-right">{formatLatency(ix.last_latency_ms)}</TableCell>
                    <TableCell className="text-right">{ix.cached}</TableCell>
                    <TableCell className="text-right">{ix.uncached}</TableCell>
                    <TableCell className="text-right">{ix.errors}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </>
      )}
    </Card>
  );
}
