"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { Title } from "@/components/Title";
import { Text } from "@/components/Text";
import type { Healthz, SettingsSnapshot, Statsz, StatszIndexers, StatszSearches } from "@/lib/types";
import { fetchHealthClient, fetchStatsClient, fetchStatsIndexersClient, fetchStatsSearchesClient } from "@/lib/client";
import { RingBuffer } from "@/lib/history";
import HealthLatencyCard from "@/components/HealthLatencyCard";
import CacheFillBars from "@/components/CacheFillBars";
import TorboxRatioDonut from "@/components/TorboxRatioDonut";
import IndexerCacheFreshness from "@/components/IndexerCacheFreshness";
import IndexerTable from "@/components/IndexerTable";
import SearchHistoryTable from "@/components/SearchHistoryTable";

const POLL_INTERVAL_MS = 10_000;
const MAX_LATENCY_SAMPLES = 60;

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

export interface DashboardInitial {
  stats: Statsz;
  indexers: StatszIndexers;
  searches: StatszSearches;
  health: Healthz;
  settings: SettingsSnapshot | null;
}

export default function DashboardGrid({ initial }: { initial: DashboardInitial }) {
  // Unauthenticated endpoints polled from the browser via SWR.
  const { data: stats, mutate: mutateStats } = useSWR<Statsz>("/api/statsz", fetchStatsClient, {
    refreshInterval: POLL_INTERVAL_MS,
    fallbackData: initial.stats,
  });
  const { data: indexers } = useSWR<StatszIndexers>(
    "/api/statsz/indexers",
    fetchStatsIndexersClient,
    { refreshInterval: POLL_INTERVAL_MS, fallbackData: initial.indexers },
  );
  const { data: searches } = useSWR<StatszSearches>(
    "/api/statsz/searches",
    fetchStatsSearchesClient,
    { refreshInterval: POLL_INTERVAL_MS, fallbackData: initial.searches },
  );
  const { data: health, error: healthError } = useSWR<Healthz>(
    "/api/healthz",
    fetchHealthClient,
    { refreshInterval: POLL_INTERVAL_MS, fallbackData: initial.health },
  );

  // Ring buffer holding the last N latency samples across polls.
  const bufferRef = useRef<RingBuffer<number | null>>(
    new RingBuffer<number | null>(MAX_LATENCY_SAMPLES),
  );

  // Seed the buffer once from the server-rendered initial stats.
  const seeded = useRef(false);
  if (!seeded.current) {
    bufferRef.current.push(initial.stats.last_search_latency_ms);
    seeded.current = true;
  }

  // Push a fresh sample each time a stats poll succeeds.
  useEffect(() => {
    if (stats) {
      bufferRef.current.push(stats.last_search_latency_ms);
    }
  }, [stats]);

  // lastChecked tracks when we last successfully heard from the backend.
  const [lastChecked, setLastChecked] = useState<number | null>(
    health ? Date.now() : null,
  );
  useEffect(() => {
    if (health) setLastChecked(Date.now());
  }, [health]);

  const samples = useMemo(() => bufferRef.current.toArray(), [stats]);

  const currentStats = stats ?? initial.stats;
  const currentIndexers = indexers ?? initial.indexers;
  const currentSearches = searches ?? initial.searches;
  const currentHealth = healthError ? undefined : (health ?? initial.health);

  // Prefer the last successful health check as the dashboard-level freshness
  // timestamp; fall back to the last search at time reported by the backend.
  const updatedTs = lastChecked ?? (currentStats.last_search_at ? currentStats.last_search_at * 1000 : null);

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-4">
        <Title>Pachelarr Dashboard</Title>
        <Text color="subtle" className="tabular-nums">
          updated {updatedTs ? formatRelativeUpdated(updatedTs) : "—"}
        </Text>
      </div>
      {/* Tremor Raw has no Grid/Col components — use Tailwind grid utilities. */}
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <div className="md:col-span-2">
          <IndexerCacheFreshness
            ageSeconds={currentStats.indexers_cache.age_seconds}
            onInvalidated={() => mutateStats()}
          />
        </div>
        <HealthLatencyCard
          health={currentHealth}
          lastChecked={lastChecked}
          samples={samples}
        />
        <TorboxRatioDonut
          hits={currentStats.torbox_hits}
          misses={currentStats.torbox_misses}
        />
        <CacheFillBars stats={currentStats} settings={initial.settings} />
        <div className="md:col-span-2">
          <IndexerTable indexers={currentIndexers.indexers} />
        </div>
        <div className="md:col-span-2">
          <SearchHistoryTable searches={currentSearches.searches} />
        </div>
      </div>
    </div>
  );
}