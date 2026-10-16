"use client";

import { useEffect, useState } from "react";
import { onOperationsChanged } from "@/lib/operationsBus";

export type TagColorData = { clusters: { cluster_id: string; cluster: string; colors: Record<string, { bg: string; fg: string }>; error: string | null }[] };
export type TagColor = { bg: string; fg: string; auto: boolean };

/** The color Proxmox's web UI shows for a tag with no override (checked against it): a 32-bit hash of the name + "prox" (the spelling
 * matters), red = low byte, blended 70/30 with white. */
export function autoColor(tag: string): string {
  let h = 0;
  const s = `${tag}prox`;
  for (let i = 0; i < s.length; i++) {
    h = s.charCodeAt(i) + ((h << 5) - h);
    h |= 0;
  }
  const u = h >>> 0;
  const blend = (x: number) => Math.round(0.7 * x + 0.3 * 255).toString(16).padStart(2, "0");
  return (blend(u & 255) + blend((u >> 8) & 255) + blend((u >> 16) & 255)).toUpperCase();
}

export function readableFg(bg: string): string {
  const r = parseInt(bg.slice(0, 2), 16), g = parseInt(bg.slice(2, 4), 16), b = parseInt(bg.slice(4, 6), 16);
  return 0.299 * r + 0.587 * g + 0.114 * b > 150 ? "000000" : "FFFFFF";
}

export function colorFor(data: TagColorData | null, clusterId: string | null | undefined, tag: string): TagColor {
  // Proxmox matches an override by the tag's exact spelling ("ai" does not color "AI"), so PyXie does too.
  const own = data?.clusters.find((c) => c.cluster_id === clusterId)?.colors[tag];
  if (own) return { bg: own.bg, fg: own.fg, auto: false };
  const bg = autoColor(tag);
  return { bg, fg: readableFg(bg), auto: true };
}

let cache: { at: number; data: TagColorData } | null = null;
let inflight: Promise<TagColorData | null> | null = null;

export async function loadTagColors(fresh = false): Promise<TagColorData | null> {
  if (!fresh && cache && Date.now() - cache.at < 15000) return cache.data;
  if (inflight) return inflight;
  inflight = fetch(`/api/workloads/tag-colors${fresh ? "?fresh=1" : ""}`)
    .then((r) => (r.ok ? r.json() : null))
    .then((d: TagColorData | null) => { if (d) cache = { at: Date.now(), data: d }; return d; })
    .catch(() => null)
    .finally(() => { inflight = null; });
  return inflight;
}

/** The tag colors, read live from Proxmox; refreshes when an operation finishes (e.g. after a color change). */
export function useTagColors(): TagColorData | null {
  const [data, setData] = useState<TagColorData | null>(cache?.data ?? null);
  useEffect(() => {
    let alive = true;
    loadTagColors().then((d) => alive && d && setData(d));
    const off = onOperationsChanged(() => loadTagColors(true).then((d) => alive && d && setData(d)));
    return () => { alive = false; off?.(); };
  }, []);
  return data;
}
