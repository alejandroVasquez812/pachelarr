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
  const hasData = samples.some((v) => v !== null && v !== undefined);

  if (!hasData || samples.length === 0) {
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
      <Title>Search Latency (ms)</Title>
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