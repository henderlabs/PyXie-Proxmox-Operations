import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export async function POST(req: NextRequest) {
  try {
    return NextResponse.json(await apiFetch("/api/protection/disk-exceptions", { method: "POST", body: JSON.stringify(await req.json()) }));
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: e instanceof ApiError ? e.status : 500 });
  }
}
