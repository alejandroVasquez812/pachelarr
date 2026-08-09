import { Card } from "@/components/Card";
import { Title } from "@/components/Title";
import { Text } from "@/components/Text";
import { DonutChart } from "@/components/DonutChart";

export default function TorboxRatioDonut({
  hits,
  misses,
}: {
  hits: number;
  misses: number;
}) {
  const total = hits + misses;
  const ratio = total > 0 ? Math.round((hits / total) * 1000) / 10 : null;

  if (total === 0) {
    return (
      <Card>
        <Title>Torbox Cache Hits</Title>
        <div className="flex h-40 items-center justify-center">
          <Text>No Torbox data yet</Text>
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <Title>Torbox Cache Hits</Title>
      <DonutChart
        className="mt-4 h-40"
        data={[
          { name: "Cached", value: hits },
          { name: "Uncached", value: misses },
        ]}
        category="name"
        value="value"
        colors={["emerald", "gray"]}
        showLabel={false}
      />
      <Text className="mt-2 text-center">
        Hit ratio: {ratio !== null ? `${ratio}%` : "—"}
      </Text>
    </Card>
  );
}