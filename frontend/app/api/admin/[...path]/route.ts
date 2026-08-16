// Server-side proxy for auth-gated admin action endpoints (browser -> here -> FastAPI).
// The browser never sees PACHELARR_API_KEY; this handler injects it from
// server env using lib/api.ts helpers.
//
// Handles: /overrides (GET/PUT/DELETE), /cache/indexers/invalidate (POST),
// /statsz/reset* (POST).

import { NextRequest, NextResponse } from "next/server";
import {
  fetchOverrides,
  putOverride,
  deleteOverride,
  invalidateIndexersCache,
  resetAllStats,
  resetIndexerStatsAll,
  resetIndexerStatsOne,
  resetSearchHistory,
  ApiError,
} from "@/lib/api";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ path?: string[] }> };

async function getSegments(ctx: Ctx): Promise<string[]> {
  const { path } = await ctx.params;
  return path ?? [];
}

function errorResponse(e: unknown): NextResponse {
  if (e instanceof ApiError) {
    try {
      const parsed = JSON.parse(e.body);
      return NextResponse.json(parsed, { status: e.status });
    } catch {
      return NextResponse.json({ error: e.body }, { status: e.status });
    }
  }
  return NextResponse.json({ error: "internal error" }, { status: 500 });
}

// GET /api/admin/overrides
export async function GET(_req: NextRequest, ctx: Ctx) {
  const segments = await getSegments(ctx);
  if (segments.length === 1 && segments[0] === "overrides") {
    try {
      const overrides = await fetchOverrides();
      return NextResponse.json(overrides);
    } catch (e) {
      return errorResponse(e);
    }
  }
  return NextResponse.json({ error: "not found" }, { status: 404 });
}

// PUT /api/admin/overrides
export async function PUT(req: NextRequest, ctx: Ctx) {
  const segments = await getSegments(ctx);
  if (segments.length === 1 && segments[0] === "overrides") {
    try {
      const body = await req.json();
      const { scope, params } = body;
      if (!scope || typeof scope !== "string") {
        return NextResponse.json({ error: "missing scope" }, { status: 400 });
      }
      if (!params || typeof params !== "object") {
        return NextResponse.json({ error: "missing params" }, { status: 400 });
      }
      const result = await putOverride(scope, params);
      return NextResponse.json(result, { status: 200 });
    } catch (e) {
      return errorResponse(e);
    }
  }
  return NextResponse.json({ error: "not found" }, { status: 404 });
}

// DELETE /api/admin/overrides?scope=...
export async function DELETE(req: NextRequest, ctx: Ctx) {
  const segments = await getSegments(ctx);
  if (segments.length === 1 && segments[0] === "overrides") {
    const scope = req.nextUrl.searchParams.get("scope");
    if (!scope) {
      return NextResponse.json({ error: "missing scope" }, { status: 400 });
    }
    try {
      const result = await deleteOverride(scope);
      return NextResponse.json(result, { status: 200 });
    } catch (e) {
      return errorResponse(e);
    }
  }
  return NextResponse.json({ error: "not found" }, { status: 404 });
}

// POST /api/admin/<path...>
export async function POST(_req: NextRequest, ctx: Ctx) {
  const segments = await getSegments(ctx);
  const path = segments.join("/");

  try {
    if (path === "cache/indexers/invalidate") {
      const result = await invalidateIndexersCache();
      return NextResponse.json(result, { status: 200 });
    }
    if (path === "statsz/reset") {
      const result = await resetAllStats();
      return NextResponse.json(result, { status: 200 });
    }
    if (path === "statsz/reset/indexers") {
      const result = await resetIndexerStatsAll();
      return NextResponse.json(result, { status: 200 });
    }
    if (path === "statsz/reset/searches") {
      const result = await resetSearchHistory();
      return NextResponse.json(result, { status: 200 });
    }
    // statsz/reset/indexers/{id}
    const match = path.match(/^statsz\/reset\/indexers\/(\d+)$/);
    if (match) {
      const indexerId = parseInt(match[1], 10);
      const result = await resetIndexerStatsOne(indexerId);
      return NextResponse.json(result, { status: 200 });
    }
  } catch (e) {
    return errorResponse(e);
  }

  return NextResponse.json({ error: "not found" }, { status: 404 });
}