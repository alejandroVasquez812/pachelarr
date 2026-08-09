// Server-side route handler that proxies the auth-gated /settings
// endpoints from the browser. The browser never sees PACHELARR_API_KEY;
// this handler injects it from server env using lib/api.ts.
//
// Browser -> /api/settings (this handler) -> FastAPI /settings

import { NextRequest, NextResponse } from "next/server";
import { fetchSettings, putSettings, ApiError } from "@/lib/api";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const settings = await fetchSettings();
    return NextResponse.json(settings);
  } catch (e) {
    if (e instanceof ApiError) {
      return NextResponse.json({ error: e.body }, { status: e.status });
    }
    return NextResponse.json({ error: "internal error" }, { status: 500 });
  }
}

export async function PUT(req: NextRequest) {
  try {
    const changes = await req.json();
    if (!Array.isArray(changes) && typeof changes !== "object") {
      return NextResponse.json({ error: "expected a JSON object" }, { status: 400 });
    }
    const result = await putSettings(changes as Record<string, string | number | boolean | null>);
    return NextResponse.json(result, { status: 200 });
  } catch (e) {
    if (e instanceof ApiError) {
      // Preserve the 400 + {applied, errors} shape from the backend.
      const status = e.status || 400;
      try {
        const parsed = JSON.parse(e.body);
        return NextResponse.json(parsed, { status });
      } catch {
        return NextResponse.json({ error: e.body }, { status });
      }
    }
    return NextResponse.json({ error: "internal error" }, { status: 500 });
  }
}