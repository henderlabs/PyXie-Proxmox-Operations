import type { Node } from "@/lib/api";

// A node PVE reports as offline, or whose last successful poll is older than the stale limit, is treated as offline.
// The limit is never below 3 minutes and follows the inventory refresh interval (Settings), so a normal refresh
// cycle can never make every host look down.
export const STALE_AFTER_MS = 3 * 60 * 1000;
// For this long after a host drops out it is flagged "just went offline" (a hung or rebooting host should not look healthy).
export const JUST_OFFLINE_MS = 7 * 60 * 1000;

/** Stale limit for a given inventory refresh interval: 2.5 refresh cycles, at least 3 minutes. */
export function staleAfterMs(refreshSeconds?: number | null): number {
  if (!refreshSeconds || !Number.isFinite(refreshSeconds) || refreshSeconds <= 0) return STALE_AFTER_MS;
  return Math.max(STALE_AFTER_MS, Math.round(refreshSeconds * 2.5 * 1000));
}

export type NodeLiveState = "online" | "just_offline" | "offline";

/** Pure: how a node should look right now. `now` and the stale limit are injectable for tests. */
export function nodeLiveState(n: Pick<Node, "status" | "last_seen">, now: number = Date.now(), staleMs: number = STALE_AFTER_MS): NodeLiveState {
  const age = now - new Date(n.last_seen).getTime();
  const down = n.status !== "online" || (Number.isFinite(age) && age > staleMs);
  if (!down) return "online";
  return Number.isFinite(age) && age <= staleMs + JUST_OFFLINE_MS ? "just_offline" : "offline";
}
