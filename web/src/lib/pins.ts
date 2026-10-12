import type { Node, Workload } from "@/lib/api";

/** Host pinning rules (Affinity Rules page, Pinning column). A guest, or every guest carrying a tag, pinned to one or more hosts:
 *  soft = those hosts are preferred (maintenance may still move it); hard = only those hosts are allowed. */
export type PinRule = {
  id: string;
  scope_type: "workload" | "tag_group" | string;
  workload_id: string | null;
  tag: string | null;
  node_ids: string[];
  strict: boolean;
  origin?: "rule" | "workload_page" | string;
  description: string | null;
  color?: string | null;
  created_by?: string | null;
};

// A different family from the affinity palette (which is bright), so a pin never reads as an affinity rule: deeper, earthier tones.
export const PIN_PALETTE = ["#b45309", "#be123c", "#4d7c0f", "#0369a1", "#4338ca", "#a21caf", "#78716c", "#0f766e"];

export function pinColor(r: { color?: string | null }, pins: unknown[], i: number): string {
  return r.color || PIN_PALETTE[(pins.length - 1 - i) % PIN_PALETTE.length];
}

type Live = Workload & { is_missing?: boolean };

/** Which guests a pin covers: the named guest, or everything carrying its tag. Missing guests never count. */
export function pinMembers(r: PinRule, workloads: Workload[]): Workload[] {
  const live = (workloads as Live[]).filter((w) => !w.is_missing);
  if (r.scope_type === "tag_group") return live.filter((w) => !!r.tag && (w.tags || []).includes(r.tag));
  return live.filter((w) => w.id === r.workload_id);
}

/** Pins that cover one guest. */
export function pinsOf(w: Workload, pins: PinRule[]): PinRule[] {
  return pins.filter((p) => (p.scope_type === "tag_group" ? !!p.tag && (w.tags || []).includes(p.tag) : p.workload_id === w.id));
}

/** Is this guest on one of the hosts any pin covering it names? */
export function onPinnedHost(w: Workload, pins: PinRule[]): boolean {
  return pinsOf(w, pins).some((p) => p.node_ids.includes(w.node_id));
}

export function hostsText(r: PinRule, nodes: Node[]): string {
  const names = r.node_ids.map((id) => nodes.find((n) => n.id === id)?.name || "an unknown host");
  return names.length <= 1 ? names[0] || "no host" : `${names.slice(0, -1).join(", ")} or ${names[names.length - 1]}`;
}

/** The pin in plain words: "st-PyXie should stay on st-pve101 or st-pve103". */
export function pinSentence(r: PinRule, byId: Map<string, Workload>, nodes: Node[]): string {
  const who = r.scope_type === "tag_group" ? `Guests tagged "${r.tag}"` : byId.get(r.workload_id || "")?.name || "a missing guest";
  return `${who} ${r.strict ? "must" : "should"} stay on ${hostsText(r, nodes)}`;
}

/** Guests that are not on a pinned host right now (one problem sentence), or null when every covered guest is where it should be. */
export function pinProblem(r: PinRule, members: Workload[], nodes: Node[]): string | null {
  const off = members.filter((m) => !r.node_ids.includes(m.node_id));
  if (off.length === 0) return null;
  const where = (w: Workload) => nodes.find((n) => n.id === w.node_id)?.name || "an unknown host";
  return off.length === 1 ? `${off[0].name || "a guest"} is on ${where(off[0])}` : `${off.length} guests are elsewhere (${off.slice(0, 3).map((w) => `${w.name} on ${where(w)}`).join(", ")}${off.length > 3 ? ", …" : ""})`;
}
