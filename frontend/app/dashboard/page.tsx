import type { Healthz, SettingsSnapshot, Statsz, StatszIndexers, StatszSearches } from "@/lib/types";
import { fetchHealth, fetchSettings, fetchStats, fetchStatsIndexers, fetchStatsSearches } from "@/lib/api";
import DashboardGrid from "@/components/DashboardGrid";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function DashboardPage() {
  const [health, stats, indexers, searches, settings] = await Promise.all([
    fetchHealth(),
    fetchStats(),
    fetchStatsIndexers(),
    fetchStatsSearches(),
    // Settings is auth-gated and throws when PACHELARR_API_KEY is unset on
    // the server; the dashboard must still render stats, so swallow it.
    fetchSettings().catch(() => null as SettingsSnapshot | null),
  ]);

  return (
    <DashboardGrid
      initial={{
        stats,
        indexers,
        searches,
        health,
        settings,
      }}
    />
  );
}
