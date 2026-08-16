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

// GET /statsz/searches
export interface SearchRecord {
  ts: number;
  query: string | null;
  search_type: string | null;
  latency_ms: number | null;
  torbox_cached: number | null;
  torbox_uncached: number | null;
  indexer_count: number | null;
}

export interface StatszSearches {
  generated_at: number; // unix epoch seconds (float)
  searches: SearchRecord[];
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

export interface SettingsGroupKey {
  key: string;
  label: string;
}

export interface SettingsGroup {
  name: string;
  tab?: string;
  keys: SettingsGroupKey[];
}

export interface SettingsTabConfig {
  id: string;
  label: string;
  groupNames: string[];
}

// Tabs each collect one or more settings groups for high-level navigation.
export const SETTINGS_TABS: SettingsTabConfig[] = [
  {
    id: "connection",
    label: "Connection",
    groupNames: ["Prowlarr Connection", "Torbox"],
  },
  {
    id: "app",
    label: "App",
    groupNames: ["Pachelarr", "Statistics"],
  },
  {
    id: "metadata",
    label: "Metadata",
    groupNames: ["TMDB", "TVDB"],
  },
  {
    id: "search",
    label: "Search & Cache",
    groupNames: ["Tracker Scraping", "Prowlarr Search / Cache"],
  },
];

export const SETTINGS_TAB_ID_DEFAULT = "connection";

// Settings grouping (mirrors pachelarr/settings.py SETTINGS registry order)
export const SETTINGS_GROUPS: SettingsGroup[] = [
  {
    name: "Prowlarr Connection",
    tab: "connection",
    keys: [
      { key: "PROWLARR_URL", label: "Prowlarr URL" },
      { key: "PROWLARR_API_KEY", label: "API key" },
    ],
  },
  {
    name: "Torbox",
    tab: "connection",
    keys: [
      { key: "TORBOX_API_KEY", label: "API key" },
      { key: "TORBOX_CHECK_URL", label: "Cache check URL" },
      { key: "TORBOX_CHUNK_SIZE", label: "Chunk size" },
      { key: "TORBOX_MAX_RETRIES", label: "Max retries" },
      { key: "TORBOX_RETRY_BACKOFF", label: "Retry backoff" },
      { key: "TORBOX_CACHE_MAX", label: "Known-cached hash cap" },
    ],
  },
  {
    name: "Pachelarr",
    tab: "app",
    keys: [
      { key: "PACHELARR_API_KEY", label: "API key" },
      { key: "PACHELARR_SEEDERS_BOOST", label: "Seeders boost" },
      { key: "PACHELARR_TEST_FALLBACK_QUERY", label: "Test fallback query" },
      { key: "PACHELARR_DATA_DIR", label: "Data directory" },
      { key: "PACHELARR_LOG_LEVEL", label: "Log level" },
    ],
  },
  {
    name: "TMDB",
    tab: "metadata",
    keys: [
      { key: "TMDB_API_KEY", label: "API key" },
      { key: "TMDB_TITLE_LOOKUP_ENABLED", label: "Title lookup enabled" },
      { key: "TMDB_TITLE_LOOKUP_CACHE_TTL", label: "Title cache TTL" },
      { key: "TMDB_TITLE_LOOKUP_CACHE_MAX", label: "Title cache max" },
    ],
  },
  {
    name: "TVDB",
    tab: "metadata",
    keys: [
      { key: "TVDB_API_KEY", label: "API key" },
      { key: "TVDB_API_PIN", label: "Subscriber PIN (optional)" },
    ],
  },
  {
    name: "Tracker Scraping",
    tab: "search",
    keys: [
      { key: "TRACKER_SCRAPE_ENABLED", label: "Tracker scraping" },
      { key: "TRACKER_SCRAPE_CONCURRENCY", label: "Concurrency" },
      { key: "TRACKER_SCRAPE_TIMEOUT", label: "Timeout" },
      { key: "TRACKER_SCRAPE_BATCH_SIZE", label: "Batch size" },
      { key: "TRACKER_SCRAPE_CACHE_TTL", label: "Cache TTL" },
      { key: "TRACKER_SCRAPE_CACHE_MAX", label: "Cache max" },
    ],
  },
  {
    name: "Prowlarr Search / Cache",
    tab: "search",
    keys: [
      { key: "PROWLARR_INDEXERS_CACHE_TTL", label: "Indexers cache TTL" },
      { key: "PROWLARR_INDEXERS_CACHE_MAX", label: "Indexers cache max" },
      { key: "PROWLARR_PARALLEL_INDEXER_CONCURRENCY", label: "Parallel indexer concurrency" },
      { key: "PROWLARR_INDEXER_SEARCH_TIMEOUT", label: "Indexer search timeout" },
    ],
  },
  {
    name: "Statistics",
    tab: "app",
    keys: [
      { key: "STATS_ENABLED", label: "Stats collection" },
      { key: "STATS_GLOBAL_ENABLED", label: "Global stats" },
      { key: "STATS_PER_INDEXER_ENABLED", label: "Per-indexer stats" },
      { key: "STATS_PER_SEARCH_ENABLED", label: "Per-search stats" },
      { key: "STATS_PER_SEARCH_MAX", label: "Per-search history cap" },
    ],
  },
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

// --------------------------------------------------------------------------- //
// Admin action types (param overrides, cache invalidation, stats reset)
// --------------------------------------------------------------------------- //

// Param overrides: {scope: params_dict, ...} where scope = "global" or "indexer:<id>"
export type ParamOverrides = Record<string, Record<string, unknown>>;

export interface PutOverrideBody {
  scope: string;
  params: Record<string, unknown>;
}

export interface PutOverrideResponse {
  applied: string;
  overrides: ParamOverrides;
}

export interface DeleteOverrideResponse {
  deleted: string;
  overrides: ParamOverrides;
}

export interface ActionResponse {
  status: "ok";
  message: string;
}