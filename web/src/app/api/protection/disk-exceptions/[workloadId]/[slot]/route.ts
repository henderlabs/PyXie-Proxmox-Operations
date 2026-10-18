import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

const UUID = /^[0-9a-fA-F-]{36}$/;
const SLOT = /^[a-z]+\d*$/;

export async function DELETE(_req: NextRequest, { params }: { params: { workloadId: string; slot: string } }) {
  if (!UUID.test(params.workloadId) || !SLOT.test(params.slot)) return NextResponse.json({ error: "not found" }, { status: 404 });
  try {
    return NextResponse.json(await apiFetch(`/api/protection/disk-exceptions/${params.workloadId}/${params.slot}`, { method: "DELETE" }));
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: e instanceof ApiError ? e.status : 500 });
  }
}
