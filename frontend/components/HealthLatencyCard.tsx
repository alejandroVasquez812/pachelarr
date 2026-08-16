"use client";

import { Card } from "@/components/Card";
import { Title } from "@/components/Title";
import { Badge } from "@/components/Badge";
import { Text } from "@/components/Text";
import { AreaChart } from "@/components/AreaChart";
import type { Healthz } from "@/lib/types";

function formatRelative(ts: number): string {
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

export default function HealthLatencyCard({
  health,
  lastChecked,
  samples,
}: {
  health: Healthz | undefined;
  lastChecked: number | null;
  samples: (number | null)[];
}) {
  const ok = health?.status === "ok";
  const data = samples.map((v, i) => ({ i, ms: v ?? 0 }));
  const nonNullSamples = samples.filter((v): v is number => v !== null && v !== undefined);
  const hasData = nonNullSamples.length > 0;
  const current = hasData ? nonNullSamples[nonNullSamples.length - 1] : null;

  // Pad a flat series so Recharts doesn't collapse the sparkline to a single pixel.
  const minMs = hasData ? Math.min(...nonNullSamples) : 0;
  const maxMs = hasData ? Math.max(...nonNullSamples) : 0;
  const paddingMs = maxMs === minMs ? Math.max(1, Math.round(maxMs * 0.1)) : 0;

  return (
    <Card>
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Title>Health</Title>
          <Badge variant={ok ? "success" : "error"}>
            {ok ? "OK" : health ? health.status : "Down"}
          </Badge>
        </div>
        <div className="text-right">
          <div
            className="text-2xl font-medium leading-none tabular-nums"
            style={{ color: "var(--accent)" }}
          >
            {current?.toFixed(0) ?? "—"} ms
          </div>
          <Text color="subtle" className="mt-1 text-xs">
            current latency
          </Text>
        </div>
      </div>
      <Text className="mt-2">
        Last checked: {lastChecked ? formatRelative(lastChecked) : "—"}
      </Text>
      {hasData ? (
        <AreaChart
          className="mt-4 h-32"
          data={data}
          index="i"
          categories={["ms"]}
          showLegend={false}
          showYAxis={false}
          showXAxis={false}
          showGridLines={false}
          startEndOnly
          colors={["blue"]}
          autoMinValue
          minValue={Math.max(0, minMs - paddingMs)}
          maxValue={maxMs + paddingMs}
          valueFormatter={(v) => `${Math.round(v)} ms`}
        />
      ) : (
        <div className="mt-4 flex h-32 items-center justify-center">
          <Text>No searches yet</Text>
        </div>
      )}
    </Card>
  );
}
