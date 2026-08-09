"use client";

import { Card } from "@/components/Card";
import { Title } from "@/components/Title";
import { Text } from "@/components/Text";
import { AreaChart } from "@/components/AreaChart";

export default function LatencySparkline({
  samples,
}: {
  samples: (number | null)[];
}) {
  const data = samples.map((v, i) => ({ i, ms: v ?? 0 }));
  const nonNullSamples = samples.filter((v): v is number => v !== null && v !== undefined);
  const hasData = nonNullSamples.length > 0;
  const current = nonNullSamples.length > 0 ? nonNullSamples[nonNullSamples.length - 1] : null;

  if (!hasData) {
    return (
      <Card>
        <Title>Search Latency (ms)</Title>
        <div className="flex h-40 items-center justify-center">
          <Text>No searches yet</Text>
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <div className="flex items-start justify-between gap-4">
        <Title>Search Latency</Title>
        <div className="text-right">
          <div
            className="text-2xl font-medium leading-none tabular-nums"
            style={{ color: "var(--accent)" }}
          >
            {current} ms
          </div>
          <Text color="subtle" className="mt-1 text-xs">
            current
          </Text>
        </div>
      </div>
      <AreaChart
        className="mt-4 h-40"
        data={data}
        index="i"
        categories={["ms"]}
        showLegend={false}
        showYAxis={false}
        showXAxis={false}
        showGridLines={false}
        startEndOnly
        colors={["blue"]}
        valueFormatter={(v) => `${Math.round(v)} ms`}
      />
    </Card>
  );
}