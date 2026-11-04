import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

// Only the inventory refresh interval, so any signed-in user can size the "host stopped answering" limit
// without being handed the rest of the settings.
export async function GET() {
  try {
    const s = await apiFetch<{ inventory_refresh_interval_seconds: number }>("/api/settings");
    return NextResponse.json({ inventory_refresh_interval_seconds: s.inventory_refresh_interval_seconds });
  } catch {
    return NextResponse.json({ inventory_refresh_interval_seconds: null });
  }
}
