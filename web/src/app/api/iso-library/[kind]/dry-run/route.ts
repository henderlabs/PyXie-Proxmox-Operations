import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

// POST /api/iso-library/{download|delete|enable}/dry-run -> the API's /api/iso-library/{kind}/dry-run
export async function POST(req: NextRequest, { params }: { params: { kind: string } }) {
  try {
    if (!["download", "delete", "enable"].includes(params.kind)) return NextResponse.json({ error: "unknown action" }, { status: 404 });
    return NextResponse.json(await apiFetch(`/api/iso-library/${params.kind}/dry-run`, { method: "POST", body: JSON.stringify(await req.json()) }));
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: e instanceof ApiError ? e.status : 500 });
  }
}
