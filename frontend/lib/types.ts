// TypeScript types for the Pachelarr backend API.
// Mirrors the actual JSON returned by pachelarr/app.py routes.

// GET /healthz
export interface Healthz {
  status: "ok";
}

// GET /statsz  (pachelarr/app.py statsz())
export interface Statsz {
  status: "ok";
  scrape_cache_size: number;
  tmdb_title_cache_size: number;
  magnet_cache_size: number;
  indexers_cache: {
    size: number;
    age_seconds: number | null; // null when no listing cached
  };
  last_search_latency_ms: number | null;
  last_search_at: number | null; // unix epoch seconds, null if never
  torbox_hits: number;
  torbox_misses: number;
}

// GET /statsz/indexers
export interface IndexerStat {
  id: number | string;
  name: string;
  protocol: string;
  enabled: boolean;
  supportsSearch: boolean;
  requests: number;
  avg_latency_ms: number;
  last_latency_ms: number;
  cached: number;
  uncached: number;
  errors: number;
}

export interface StatszIndexers {
  generated_at: number; // unix epoch seconds (float)
  indexers: IndexerStat[];
}

// GET /settings -> settings.snapshot()
// type is "str" | "int" | "float" | "bool"
export type SettingType = "str" | "int" | "float" | "bool";

export interface SettingEntry {
  value: string | number | boolean | null;
  type: SettingType;
  secret: boolean;
  default: string | number | boolean | null;
  restart_required: boolean;
}

// { key: SettingEntry, ... }
export type SettingsSnapshot = Record<string, SettingEntry>;

// PUT /settings success (200) -> { applied, settings }
// PUT /settings failure (400) -> { applied, errors }  (errors: {key: message})
export interface PutSettingsSuccess {
  applied: Record<string, string | number | boolean | null>;
  settings: SettingsSnapshot;
}

export interface PutSettingsError {
  applied: Record<string, string | number | boolean | null>;
  errors: Record<string, string>;
}

// Settings grouping (mirrors pachelarr/settings.py SETTINGS registry order)
export const SETTINGS_GROUPS: { name: string; keys: string[] }[] = [
  { name: "Prowlarr Connection", keys: ["PROWLARR_URL", "PROWLARR_API_KEY"] },
  { name: "Torbox", keys: ["TORBOX_API_KEY", "TORBOX_CHECK_URL", "TORBOX_CHUNK_SIZE", "TORBOX_MAX_RETRIES", "TORBOX_RETRY_BACKOFF"] },
  { name: "Pachelarr", keys: ["PACHELARR_API_KEY", "PACHELARR_SEEDERS_BOOST", "PACHELARR_TEST_FALLBACK_QUERY", "PACHELARR_DATA_DIR", "PACHELARR_LOG_LEVEL"] },
  { name: "TMDB", keys: ["TMDB_API_KEY", "TMDB_TITLE_LOOKUP_ENABLED", "TMDB_TITLE_LOOKUP_CACHE_TTL", "TMDB_TITLE_LOOKUP_CACHE_MAX"] },
  { name: "Tracker Scraping", keys: ["TRACKER_SCRAPE_ENABLED", "TRACKER_SCRAPE_CONCURRENCY", "TRACKER_SCRAPE_TIMEOUT", "TRACKER_SCRAPE_BATCH_SIZE", "TRACKER_SCRAPE_CACHE_TTL", "TRACKER_SCRAPE_CACHE_MAX"] },
  { name: "Prowlarr Search / Cache", keys: ["PROWLARR_INDEXERS_CACHE_TTL", "PROWLARR_INDEXERS_CACHE_MAX", "PROWLARR_PARALLEL_INDEXER_CONCURRENCY", "PROWLARR_INDEXER_SEARCH_TIMEOUT"] },
];

// Required secrets that must not be blank-saved without confirmation.
export const REQUIRED_SECRETS = new Set(["PROWLARR_API_KEY", "TORBOX_API_KEY", "PACHELARR_API_KEY"]);

// Cache-max setting keys for fill-ratio bars.
export const CACHE_MAX_KEYS = {
  scrape: "TRACKER_SCRAPE_CACHE_MAX",
  tmdb: "TMDB_TITLE_LOOKUP_CACHE_MAX",
  magnet: "TRACKER_SCRAPE_CACHE_MAX", // magnet cache cap defaults to TRACKER_SCRAPE_CACHE_MAX
  indexers: "PROWLARR_INDEXERS_CACHE_MAX",
} as const;