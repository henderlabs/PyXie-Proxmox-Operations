import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

const fail = (e: unknown) => NextResponse.json({ error: (e as Error).message }, { status: e instanceof ApiError ? e.status : 500 });
const ID = "[0-9a-fA-F-]{36}";
const ALLOWED: Record<string, RegExp[]> = {
  GET: [new RegExp(`^points/${ID}$`), new RegExp(`^options/${ID}$`)],
  POST: [/^dry-run$/],
};

async function handle(req: NextRequest, params: { path: string[] }) {
  const path = params.path.join("/");
  if (!(ALLOWED[req.method] ?? []).some((r) => r.test(path))) return NextResponse.json({ error: "not found" }, { status: 404 });
  const init: RequestInit = { method: req.method };
  if (req.method === "POST") init.body = JSON.stringify(await req.json().catch(() => ({})));
  try { return NextResponse.json(await apiFetch(`/api/restore/${path}${req.nextUrl.search}`, init)); } catch (e) { return fail(e); }
}

export async function GET(req: NextRequest, { params }: { params: { path: string[] } }) { return handle(req, params); }
export async function POST(req: NextRequest, { params }: { params: { path: string[] } }) { return handle(req, params); }
