"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Card, CardTitle } from "@/components/Card";

type Usage = {
  server_id: string; server: string; name: string; backend: string; used: number | null; total: number | null; avail: number | null; estimated_full: number | null;
  guests: number; backups: number; gc: { last_state: string | null; last_end: number | null; removed_bytes: number | null; pending_bytes: number | null };
  history: (number | null)[]; history_start: number | null; history_delta: number | null;
};

const GIB = 1073741824;
export const fmtBytes = (b: number | null | undefined) => (b == null ? "—" : b >= 1099511627776 ? `${(b / 1099511627776).toFixed(2)} TiB` : `${(b / GIB).toFixed(b >= 10 * GIB ? 0 : 1)} GiB`);

function useUsage() {
  const [data, setData] = useState<Usage[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { fetch("/api/pbs/datastore-usage").then(async (r) => { const d = await r.json(); if (!r.ok) throw new Error(d.error || "Could not read the backup servers"); setData(d); }).catch((e) => setErr(e.message)); }, []);
  return { data, err };
}

function Spark({ h, tone }: { h: (number | null)[]; tone: string }) {
  const pts = h.filter((x): x is number => x != null);
  if (pts.length < 3) return null;
  const step = Math.max(1, Math.floor(pts.length / 60));
  const s = pts.filter((_, i) => i % step === 0);
  const lo = Math.min(...s), hi = Math.max(...s), span = hi - lo || 1;
  const d = s.map((v, i) => `${i === 0 ? "M" : "L"}${((i / (s.length - 1)) * 100).toFixed(1)},${(22 - ((v - lo) / span) * 20).toFixed(1)}`).join(" ");
  return <svg viewBox="0 0 100 24" className="w-24 h-6" preserveAspectRatio="none" aria-label="usage over the last 30 days"><path d={d} fill="none" stroke="currentColor" strokeWidth="1.5" className={tone} vectorEffect="non-scaling-stroke" /></svg>;
}

function Bar({ pct }: { pct: number }) {
  const tone = pct >= 95 ? "bg-bad" : pct >= 85 ? "bg-warn" : "bg-good";
  return <div className="h-1.5 rounded bg-surface2 overflow-hidden"><div className={`h-full ${tone}`} style={{ width: `${Math.min(100, pct)}%` }} /></div>;
}
const pctOf = (u: Usage) => (u.backend !== "s3" && u.total && u.used != null ? (100 * u.used) / u.total : null);
const when = (s: number | null) => (s ? new Date(s * 1000).toLocaleDateString() : "—");

/** How much space the backups use on every backup server datastore. `compact` is the short form for a tile; the full form adds counts, the last cleanup, an estimate and a 30 day trend. */
export default function BackupStorage({ variant = "full", title = "Backup storage" }: { variant?: "compact" | "full"; title?: string }) {
  const { data, err } = useUsage();
  if (variant === "full" && data && data.length === 0 && !err) return null; // no backup server: nothing to show
  if (variant === "compact") {
    return (
      <Card className="!p-3">
        <div className="text-xs text-muted mb-1">{title}</div>
        {err && <div className="text-xs text-warn">{err}</div>}
        {!data && !err && <div className="text-xs text-muted">Reading…</div>}
        {(data ?? []).map((u) => { const p = pctOf(u); return (
          <div key={u.server_id + u.name} className="mb-1">
            <div className="flex justify-between text-xs"><span className="text-text truncate pr-2" title={`${u.name} on ${u.server}`}>{u.name}</span><span className="text-muted whitespace-nowrap">{fmtBytes(u.used)}{p != null ? ` of ${fmtBytes(u.total)} (${p.toFixed(0)}%)` : u.backend === "s3" ? " stored" : ""}</span></div>
            {p != null && <Bar pct={p} />}
          </div>); })}
      </Card>
    );
  }
  return (
    <Card className="mb-4">
      <CardTitle>{title}</CardTitle>
      <div className="text-xs text-muted mb-2">The real space the backups take on each backup server datastore (identical data is stored once, so this is smaller than the sum of the backups). Wasabi has no fixed capacity: you pay for what is stored.</div>
      {err && <div className="text-warn text-sm">{err}</div>}
      {!data && !err && <div className="text-sm text-muted">Reading the backup servers…</div>}
      <div className="divide-y divide-border">
        {(data ?? []).map((u) => { const p = pctOf(u); const s3 = u.backend === "s3"; return (
          <div key={u.server_id + u.name} className="py-2 grid grid-cols-1 md:grid-cols-[14rem_1fr_7rem] gap-x-4 gap-y-1 items-center text-sm">
            <div><div className="text-text">{u.name}</div><div className="text-xs text-muted">{u.server} · {s3 ? "S3, offsite" : "disk"}</div></div>
            <div>
              <div className="flex justify-between text-xs mb-0.5"><span className="text-text">{fmtBytes(u.used)} {s3 ? "stored" : "used"}{p != null ? ` of ${fmtBytes(u.total)}` : ""}</span><span className="text-muted">{p != null ? `${p.toFixed(0)}% full · ${fmtBytes(u.avail)} free` : "billed by usage"}</span></div>
              {p != null && <Bar pct={p} />}
              <div className="text-xs text-muted mt-1">{u.guests} guests · {u.backups} backups{p != null && u.estimated_full ? ` · estimated full ${when(u.estimated_full)}` : ""}
                {u.gc.last_end ? ` · last garbage collection ${when(u.gc.last_end)}${u.gc.last_state && u.gc.last_state !== "OK" ? ` (${u.gc.last_state})` : ""}${u.gc.removed_bytes ? `, freed ${fmtBytes(u.gc.removed_bytes)}` : ""}${u.gc.pending_bytes ? `, ${fmtBytes(u.gc.pending_bytes)} waiting for the next one` : ""}` : " · garbage collection has not run yet"}</div>
            </div>
            <div className="flex flex-col items-end text-accent"><Spark h={u.history} tone="text-accent" /><span className="text-[10px] text-muted">last 30 days</span></div>
          </div>); })}
      </div>
      <div className="text-xs text-muted mt-2">Pruned backups give their space back only after <Link href="/operations/protection/servers" className="text-accent hover:underline">garbage collection</Link> runs.</div>
    </Card>
  );
}
