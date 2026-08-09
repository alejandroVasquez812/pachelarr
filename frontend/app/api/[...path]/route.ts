// Runtime catch-all proxy for unauthenticated browser endpoints
// (/api/healthz, /api/statsz, /api/statsz/indexers, /api/statsz/searches).
//
// Unlike a next.config.ts rewrite, this route handler reads
// PACHELARR_BACKEND_URL at request time, so the value is NOT inlined at
// build time and the docker-compose env var takes effect in the runner.
//
// Precedence: the more specific app/api/settings/route.ts handles
// /api/settings (auth-gated, injects PACHELARR_API_KEY); everything else
// under /api/* falls through to this catch-all.

import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

function backendUrl(): string {
  return (process.env.PACHELARR_BACKEND_URL || "http://localhost:6800").replace(/\/$/, "");
}

function forwardHeaders(req: NextRequest): Headers {
  const h = new Headers(req.headers);
  // Drop hop-by-hop / origin-specific headers so the upstream sees a
  // clean server-side request.
  h.delete("host");
  return h;
}

async function proxy(req: NextRequest, segments: string[]): Promise<NextResponse> {
  const path = segments.join("/");
  const target = new URL(`${backendUrl()}/${path}`);
  // Preserve query string.
  req.nextUrl.searchParams.forEach((v, k) => target.searchParams.append(k, v));

  const init: RequestInit = {
    method: req.method,
    headers: forwardHeaders(req),
    cache: "no-store",
  };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.text();
  }

  const upstream = await fetch(target, init);
  const headers = new Headers(upstream.headers);
  // fetch may have decompressed; let Next re-compress if appropriate.
  headers.delete("content-encoding");
  headers.delete("content-length");
  return new NextResponse(upstream.body, { status: upstream.status, headers });
}

type Ctx = { params: Promise<{ path?: string[] }> };

async function segmentsFrom(ctx: Ctx): Promise<string[]> {
  const { path } = await ctx.params;
  return path ?? [];
}

export async function GET(req: NextRequest, ctx: Ctx) {
  return proxy(req, await segmentsFrom(ctx));
}

export async function HEAD(req: NextRequest, ctx: Ctx) {
  return proxy(req, await segmentsFrom(ctx));
}

export async function POST(req: NextRequest, ctx: Ctx) {
  return proxy(req, await segmentsFrom(ctx));
}

export async function PUT(req: NextRequest, ctx: Ctx) {
  return proxy(req, await segmentsFrom(ctx));
}

export async function DELETE(req: NextRequest, ctx: Ctx) {
  return proxy(req, await segmentsFrom(ctx));
}

export async function PATCH(req: NextRequest, ctx: Ctx) {
  return proxy(req, await segmentsFrom(ctx));
}