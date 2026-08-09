import { Card } from "@/components/Card";
import { Title } from "@/components/Title";
import { ProgressBar } from "@/components/ProgressBar";
import type { SettingsSnapshot, Statsz } from "@/lib/types";
import { CACHE_MAX_KEYS } from "@/lib/types";

// Fallback defaults when the settings snapshot is unavailable.
const DEFAULT_MAX: Record<string, number> = {
  scrape: 5000,
  tmdb: 5000,
  magnet: 5000,
  indexers: 1,
};

interface FillRow {
  label: string;
  size: number;
  max: number;
}

function getMax(settings: SettingsSnapshot | null, key: string, fallback: number): number {
  const entry = settings?.[key];
  if (!entry) return fallback;
  const n = typeof entry.value === "number" ? entry.value : Number(entry.value);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

export default function CacheFillBars({
  stats,
  settings,
}: {
  stats: Statsz;
  settings: SettingsSnapshot | null;
}) {
  const rows: FillRow[] = [
    {
      label: "Scrape cache",
      size: stats.scrape_cache_size,
      max: getMax(settings, CACHE_MAX_KEYS.scrape, DEFAULT_MAX.scrape),
    },
    {
      label: "TMDB lookup cache",
      size: stats.tmdb_title_cache_size,
      max: getMax(settings, CACHE_MAX_KEYS.tmdb, DEFAULT_MAX.tmdb),
    },
    {
      label: "Magnet cache",
      size: stats.magnet_cache_size,
      max: getMax(settings, CACHE_MAX_KEYS.magnet, DEFAULT_MAX.magnet),
    },
    {
      label: "Indexer listing cache",
      size: stats.indexers_cache.size,
      max: getMax(settings, CACHE_MAX_KEYS.indexers, DEFAULT_MAX.indexers),
    },
  ];

  return (
    <Card>
      <Title>Cache Fill Ratio</Title>
      <div className="mt-4 space-y-4">
        {rows.map((row) => {
          const pct = row.max > 0 ? (row.size / row.max) * 100 : 0;
          const variant = pct > 80 ? "error" : "default";
          return (
            <div key={row.label}>
              <div className="mb-1 flex items-center justify-between text-sm">
                <span style={{ color: "var(--text)" }}>{row.label}</span>
                <span
                  className="tabular-nums"
                  style={{ color: "var(--muted)" }}
                >
                  {row.size} / {row.max}
                </span>
              </div>
              <ProgressBar value={pct} variant={variant} />
            </div>
          );
        })}
      </div>
    </Card>
  );
}