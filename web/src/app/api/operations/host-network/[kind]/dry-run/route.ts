import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

const KINDS = new Set(["stage", "apply", "discard"]);

export async function POST(req: NextRequest, { params }: { params: { kind: string } }) {
  if (!KINDS.has(params.kind)) return NextResponse.json({ error: "unknown network action" }, { status: 404 });
  try {
    const body = await req.json();
    const result = await apiFetch(`/api/operations/host-network/${params.kind}/dry-run`, { method: "POST", body: JSON.stringify(body) });
    return NextResponse.json(result);
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: (e as Error).message }, { status });
  }
}
