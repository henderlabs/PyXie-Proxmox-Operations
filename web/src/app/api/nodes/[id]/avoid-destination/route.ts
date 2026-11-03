import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function PUT(req: NextRequest, { params }: { params: { id: string } }) {
  const body = await req.text();
  try {
    const result = await apiFetch(`/api/nodes/${params.id}/avoid-destination`, { method: "PUT", body });
    return NextResponse.json(result);
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: (e as Error).message }, { status });
  }
}
