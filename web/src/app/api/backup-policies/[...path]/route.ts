import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

const fail = (e: unknown) => NextResponse.json({ error: (e as Error).message }, { status: e instanceof ApiError ? e.status : 500 });
const UUID = /^[0-9a-fA-F-]{36}$/;

export async function GET(_req: NextRequest, { params }: { params: { path: string[] } }) {
  const [a, b] = params.path;
  if (a !== "workload" || !b || !UUID.test(b) || params.path.length !== 2) return NextResponse.json({ error: "not found" }, { status: 404 });
  try { return NextResponse.json(await apiFetch(`/api/backup-policies/workload/${b}`)); } catch (e) { return fail(e); }
}

export async function POST(req: NextRequest, { params }: { params: { path: string[] } }) {
  if (params.path.join("/") !== "dry-run") return NextResponse.json({ error: "not found" }, { status: 404 });
  try { return NextResponse.json(await apiFetch("/api/backup-policies/dry-run", { method: "POST", body: JSON.stringify(await req.json()) })); } catch (e) { return fail(e); }
}
