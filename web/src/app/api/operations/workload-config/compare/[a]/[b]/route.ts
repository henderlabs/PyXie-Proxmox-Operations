import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function GET(_req: NextRequest, { params }: { params: { a: string; b: string } }) {
  try {
    return NextResponse.json(await apiFetch(`/api/operations/workload-config/compare/${params.a}/${params.b}`));
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 500;
    return NextResponse.json({ error: (e as Error).message }, { status });
  }
}
