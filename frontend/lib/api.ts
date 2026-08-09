// Server-side API helpers. Browser NEVER uses this module directly.
// Reads PACHELARR_BACKEND_URL (internal docker-network URL in prod,
// http://localhost:6800 in dev) and PACHELARR_API_KEY from server env.
//
// Browser-side polling for the unauthenticated endpoints (/healthz,
// /statsz, /statsz/indexers) goes through the Next rewrite (/api/*) —
// see DashboardGrid. Settings calls (auth-gated) go through server
// components / route handlers using THIS module so the key never
// reaches the client bundle.
//
// This module must NEVER be imported by a "use client" file. It is safe
// to import from server components and route handlers.

import type {
  Healthz,
  Statsz,
  StatszIndexers,
  SettingsSnapshot,
  PutSettingsSuccess,
  PutSettingsError,
} from "./types";

const BACKEND_URL =
  process.env.PACHELARR_BACKEND_URL || "http://localhost:6800";
const API_KEY = process.env.PACHELARR_API_KEY || "";

function backendUrl(path: string): string {
  // path starts with "/"
  return `${BACKEND_URL.replace(/\/$/, "")}${path}`;
}

function authHeaders(): HeadersInit {
  return API_KEY ? { "X-Api-Key": API_KEY } : {};
}

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(backendUrl(path), {
    ...init,
    // No-store so server components always see fresh data on each request.
    cache: "no-store",
    next: { revalidate: 0 },
  });
  if (!res.ok) {
    throw new ApiError(res.status, await safeErrorBody(res));
  }
  return (await res.json()) as T;
}

async function safeErrorBody(res: Response): Promise<string> {
  try {
    return await res.text();
  } catch {
    return `HTTP ${res.status}`;
  }
}

export class ApiError extends Error {
  status: number;
  body: string;
  constructor(status: number, body: string) {
    super(`API error ${status}: ${body.slice(0, 200)}`);
    this.status = status;
    this.body = body;
    this.name = "ApiError";
  }
}

// GET /healthz — unauthenticated
export async function fetchHealth(): Promise<Healthz> {
  return getJson<Healthz>("/healthz");
}

// GET /statsz — unauthenticated
export async function fetchStats(): Promise<Statsz> {
  return getJson<Statsz>("/statsz");
}

// GET /statsz/indexers — unauthenticated
export async function fetchStatsIndexers(): Promise<StatszIndexers> {
  return getJson<StatszIndexers>("/statsz/indexers");
}

// GET /settings — auth-gated (server-side only; key from server env)
export async function fetchSettings(): Promise<SettingsSnapshot> {
  if (!API_KEY) {
    throw new ApiError(401, "PACHELARR_API_KEY not configured on the frontend");
  }
  return getJson<SettingsSnapshot>("/settings", { headers: authHeaders() });
}

// PUT /settings body {key: value, ...} -> {applied, settings} on 200,
// or throws ApiError(400) with body containing {applied, errors}.
export async function putSettings(
  changes: Record<string, string | number | boolean | null>,
): Promise<PutSettingsSuccess> {
  if (!API_KEY) {
    throw new ApiError(401, "PACHELARR_API_KEY not configured on the frontend");
  }
  const res = await fetch(backendUrl("/settings"), {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    cache: "no-store",
    body: JSON.stringify(changes),
  });
  if (!res.ok) {
    // 400 -> body is {applied, errors}; surface it so the caller can map
    // per-field errors back to the UI.
    const err = new ApiError(res.status, await safeErrorBody(res));
    try {
      (err as ApiErrorWithJson).json = JSON.parse(err.body) as PutSettingsError;
    } catch {
      // body wasn't JSON; leave json undefined
    }
    throw err;
  }
  return (await res.json()) as PutSettingsSuccess;
}

export interface ApiErrorWithJson extends ApiError {
  json?: PutSettingsError;
}