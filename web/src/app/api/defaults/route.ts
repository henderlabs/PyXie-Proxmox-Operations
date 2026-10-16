import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

function fail(e: unknown) {
  return NextResponse.json({ error: (e as Error).message }, { status: e instanceof ApiError ? e.status : 500 });
}

export async function GET() {
  try { return NextResponse.json(await apiFetch("/api/defaults")); } catch (e) { return fail(e); }
}

export async function POST(req: NextRequest) {
  try { return NextResponse.json(await apiFetch("/api/defaults", { method: "POST", body: JSON.stringify(await req.json()) })); } catch (e) { return fail(e); }
}
