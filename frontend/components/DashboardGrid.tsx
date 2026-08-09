"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { Title } from "@/components/Title";
import type { Healthz, SettingsSnapshot, Statsz, StatszIndexers } from "@/lib/types";
import { fetchHealthClient, fetchStatsClient, fetchStatsIndexersClient } from "@/lib/client";
import { RingBuffer } from "@/lib/history";
import HealthCard from "@/components/HealthCard";
import CacheFillBars from "@/components/CacheFillBars";
import TorboxRatioDonut from "@/components/TorboxRatioDonut";
import LatencySparkline from "@/components/LatencySparkline";
import IndexerCacheFreshness from "@/components/IndexerCacheFreshness";
import IndexerTable from "@/components/IndexerTable";

const POLL_INTERVAL_MS = 10_000;
const MAX_LATENCY_SAMPLES = 60;

export interface DashboardInitial {
  stats: Statsz;
  indexers: StatszIndexers;
  health: Healthz;
  settings: SettingsSnapshot | null;
}

export default function DashboardGrid({ initial }: { initial: DashboardInitial }) {
  // Unauthenticated endpoints polled from the browser via SWR.
  const { data: stats } = useSWR<Statsz>("/api/statsz", fetchStatsClient, {
    refreshInterval: POLL_INTERVAL_MS,
    fallbackData: initial.stats,
  });
  const { data: indexers } = useSWR<StatszIndexers>(
    "/api/statsz/indexers",
    fetchStatsIndexersClient,
    { refreshInterval: POLL_INTERVAL_MS, fallbackData: initial.indexers },
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
  const currentHealth = healthError ? undefined : (health ?? initial.health);

  return (
    <div className="space-y-6">
      <Title>Pachelarr Dashboard</Title>
      {/* Tremor Raw has no Grid/Col components — use Tailwind grid utilities. */}
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <div className="md:col-span-2">
          <IndexerCacheFreshness ageSeconds={currentStats.indexers_cache.age_seconds} />
        </div>
        <HealthCard health={currentHealth} lastChecked={lastChecked} />
        <TorboxRatioDonut
          hits={currentStats.torbox_hits}
          misses={currentStats.torbox_misses}
        />
        <LatencySparkline samples={samples} />
        <CacheFillBars stats={currentStats} settings={initial.settings} />
        <div className="md:col-span-2">
          <IndexerTable indexers={currentIndexers.indexers} />
        </div>
      </div>
    </div>
  );
}