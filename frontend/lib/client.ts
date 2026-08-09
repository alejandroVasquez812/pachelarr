// Client-side helpers for browser polling. These hit the Next rewrite
// (/api/*) which proxies to the FastAPI backend (see next.config.ts).
//
// ONLY used for the UNAUTHENTICATED endpoints: /healthz, /statsz,
// /statsz/indexers. The auth-gated /settings PUT goes through the
// server-side route handler at app/api/settings/route.ts which injects
// the key from server env.

import type { Healthz, Statsz, StatszIndexers, StatszSearches } from "./types";

export async function fetchHealthClient(): Promise<Healthz> {
  const res = await fetch("/api/healthz", { cache: "no-store" });
  if (!res.ok) throw new Error(`/api/healthz ${res.status}`);
  return res.json();
}

export async function fetchStatsClient(): Promise<Statsz> {
  const res = await fetch("/api/statsz", { cache: "no-store" });
  if (!res.ok) throw new Error(`/api/statsz ${res.status}`);
  return res.json();
}

export async function fetchStatsIndexersClient(): Promise<StatszIndexers> {
  const res = await fetch("/api/statsz/indexers", { cache: "no-store" });
  if (!res.ok) throw new Error(`/api/statsz/indexers ${res.status}`);
  return res.json();
}

export async function fetchStatsSearchesClient(): Promise<StatszSearches> {
  const res = await fetch("/api/statsz/searches", { cache: "no-store" });
  if (!res.ok) throw new Error(`/api/statsz/searches ${res.status}`);
  return res.json();
}