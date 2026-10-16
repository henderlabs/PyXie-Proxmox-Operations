import { NextRequest, NextResponse } from "next/server";
import { apiFetch, ApiError } from "@/lib/api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Streams the browser's file straight on to the API (never buffered here): the request body is passed through as it arrives.
export async function POST(req: NextRequest) {
  try {
    const len = req.headers.get("content-length");
    const result = await apiFetch(`/api/iso-library/upload${req.nextUrl.search}`, {
      method: "POST",
      body: req.body,
      headers: { "Content-Type": "application/octet-stream", ...(len ? { "Content-Length": len } : {}) },
      // @ts-expect-error Node's fetch needs duplex to stream a request body
      duplex: "half",
    });
    return NextResponse.json(result);
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: e instanceof ApiError ? e.status : 500 });
  }
}
