import { Card, Title, Badge, Text } from "@tremor/react";
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

export default function HealthCard({
  health,
  lastChecked,
}: {
  health: Healthz | undefined;
  lastChecked: number | null;
}) {
  const ok = health?.status === "ok";

  return (
    <Card>
      <div className="flex items-center justify-between">
        <Title>Health</Title>
        <Badge color={ok ? "emerald" : "rose"}>
          {ok ? "OK" : health ? health.status : "Down"}
        </Badge>
      </div>
      <Text className="mt-2">
        Last checked: {lastChecked ? formatRelative(lastChecked) : "—"}
      </Text>
    </Card>
  );
}
