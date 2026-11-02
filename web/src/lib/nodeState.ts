import type { Node } from "@/lib/api";

// A node PVE reports as offline, or whose last successful poll is older than this, is treated as offline. Fixed, no setting.
export const STALE_AFTER_MS = 3 * 60 * 1000;
// For this long after a host drops out it is flagged "just went offline" (a hung or rebooting host should not look healthy).
export const JUST_OFFLINE_MS = 10 * 60 * 1000;

export type NodeLiveState = "online" | "just_offline" | "offline";

/** Pure: how a node should look right now. `now` is injectable for tests. */
export function nodeLiveState(n: Pick<Node, "status" | "last_seen">, now: number = Date.now()): NodeLiveState {
  const age = now - new Date(n.last_seen).getTime();
  const down = n.status !== "online" || (Number.isFinite(age) && age > STALE_AFTER_MS);
  if (!down) return "online";
  return Number.isFinite(age) && age <= JUST_OFFLINE_MS ? "just_offline" : "offline";
}
