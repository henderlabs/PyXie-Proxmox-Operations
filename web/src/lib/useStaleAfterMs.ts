"use client";

import { useEffect, useState } from "react";
import { STALE_AFTER_MS, staleAfterMs } from "@/lib/nodeState";

/** The stale limit for "host stopped answering", from the inventory refresh interval in Settings. */
export function useStaleAfterMs(): number {
  const [ms, setMs] = useState(STALE_AFTER_MS);
  useEffect(() => {
    let alive = true;
    fetch("/api/system/refresh-interval")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (alive && d) setMs(staleAfterMs(d.inventory_refresh_interval_seconds)); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);
  return ms;
}
