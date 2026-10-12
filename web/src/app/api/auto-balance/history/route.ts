import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function GET(req: NextRequest) {
  try {
    const limit = req.nextUrl.searchParams.get("limit") || "100";
    return NextResponse.json(await apiFetch(`/api/auto-balance/history?limit=${encodeURIComponent(limit)}`));
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: e instanceof ApiError ? e.status : 500 });
  }
}
