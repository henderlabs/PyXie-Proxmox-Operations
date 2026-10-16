import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

const GET_OK = new Set(["list"]);
const POST_OK = new Set(["create/dry-run", "delete/dry-run", "members/dry-run"]);
const fail = (e: unknown) => NextResponse.json({ error: (e as Error).message }, { status: e instanceof ApiError ? e.status : 500 });

export async function GET(req: NextRequest, { params }: { params: { path: string[] } }) {
  const path = params.path.join("/");
  if (!GET_OK.has(path)) return NextResponse.json({ error: "not found" }, { status: 404 });
  try { return NextResponse.json(await apiFetch(`/api/pools${path === "list" ? "" : "/" + path}${req.nextUrl.search}`)); } catch (e) { return fail(e); }
}

export async function POST(req: NextRequest, { params }: { params: { path: string[] } }) {
  const path = params.path.join("/");
  if (!POST_OK.has(path)) return NextResponse.json({ error: "not found" }, { status: 404 });
  try { return NextResponse.json(await apiFetch(`/api/pools/${path}`, { method: "POST", body: JSON.stringify(await req.json()) })); } catch (e) { return fail(e); }
}
