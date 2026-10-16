import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

const GET_OK = new Set(["options", "scan-nfs", "definitions", "scan/iscsi", "scan/lvm", "scan/lvmthin", "scan/zfs"]);
const POST_OK = new Set(["create/dry-run", "remove/dry-run"]);
const fail = (e: unknown) => NextResponse.json({ error: (e as Error).message }, { status: e instanceof ApiError ? e.status : 500 });

export async function GET(req: NextRequest, { params }: { params: { path: string[] } }) {
  const path = params.path.join("/");
  if (!GET_OK.has(path)) return NextResponse.json({ error: "not found" }, { status: 404 });
  try { return NextResponse.json(await apiFetch(`/api/storage-setup/${path}${req.nextUrl.search}`)); } catch (e) { return fail(e); }
}

export async function POST(req: NextRequest, { params }: { params: { path: string[] } }) {
  const path = params.path.join("/");
  if (!POST_OK.has(path)) return NextResponse.json({ error: "not found" }, { status: 404 });
  try { return NextResponse.json(await apiFetch(`/api/storage-setup/${path}`, { method: "POST", body: JSON.stringify(await req.json()) })); } catch (e) { return fail(e); }
}
