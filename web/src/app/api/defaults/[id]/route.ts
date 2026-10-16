import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

function fail(e: unknown) {
  return NextResponse.json({ error: (e as Error).message }, { status: e instanceof ApiError ? e.status : 500 });
}

export async function PUT(req: NextRequest, { params }: { params: { id: string } }) {
  try { return NextResponse.json(await apiFetch(`/api/defaults/${params.id}`, { method: "PUT", body: JSON.stringify(await req.json()) })); } catch (e) { return fail(e); }
}

export async function DELETE(_req: NextRequest, { params }: { params: { id: string } }) {
  try { return NextResponse.json(await apiFetch(`/api/defaults/${params.id}`, { method: "DELETE" })); } catch (e) { return fail(e); }
}
