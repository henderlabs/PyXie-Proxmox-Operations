import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

const ALLOWED = new Set(["options", "appliances", "dry-run", "template-download/dry-run", "template-delete/dry-run"]);

function fail(e: unknown) {
  return NextResponse.json({ error: (e as Error).message }, { status: e instanceof ApiError ? e.status : 500 });
}

export async function GET(req: NextRequest, { params }: { params: { path: string[] } }) {
  const path = params.path.join("/");
  if (!ALLOWED.has(path)) return NextResponse.json({ error: "not found" }, { status: 404 });
  try { return NextResponse.json(await apiFetch(`/api/ct/${path}${req.nextUrl.search}`)); } catch (e) { return fail(e); }
}

export async function POST(req: NextRequest, { params }: { params: { path: string[] } }) {
  const path = params.path.join("/");
  if (!ALLOWED.has(path)) return NextResponse.json({ error: "not found" }, { status: 404 });
  try { return NextResponse.json(await apiFetch(`/api/ct/${path}`, { method: "POST", body: JSON.stringify(await req.json()) })); } catch (e) { return fail(e); }
}
