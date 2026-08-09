import {
  Card,
  Title,
  Badge,
  Table,
  TableHead,
  TableHeaderCell,
  TableBody,
  TableRow,
  TableCell,
  Text,
} from "@tremor/react";
import type { IndexerStat } from "@/lib/types";

function formatLatency(ms: number): string {
  return String(Math.round(ms));
}

export default function IndexerTable({ indexers }: { indexers: IndexerStat[] }) {
  return (
    <Card>
      <div className="flex items-center gap-3">
        <Title>Per-Indexer Analysis</Title>
        <Badge color="gray">Awaiting instrumentation</Badge>
      </div>
      {indexers.length === 0 ? (
        <div className="mt-4">
          <Text>No indexer listing cached yet — a search will populate it.</Text>
        </div>
      ) : (
        <Table className="mt-4">
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
                  <Badge color={ix.enabled ? "emerald" : "rose"}>
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
      )}
    </Card>
  );
}
