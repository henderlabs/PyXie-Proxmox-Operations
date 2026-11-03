"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { StatTile } from "@/components/Card";

type Run = { end: number; ok: boolean };
type Server = {
  id: string; name: string; hostname: string; reachable: boolean | null; error?: string | null; version?: string | null; uptime?: number | null; cpu?: number | null;
  mem_pct?: number | null; root_pct?: number | null; datastores: { name: string; backend?: string; used: number; total: number | null }[]; used_bytes: number; total_bytes: number;
  last_runs?: { backup?: Run; verify?: Run; gc?: Run } | null; failed_24h?: number | null; updates?: number | null; reboot_required?: boolean | null;
  wrapper_version?: string | null; wrapper_outdated?: boolean; ssh_ready?: boolean; status_at?: string | null;
};
type Unconnected = { storage: string; server: string; datastore: string; port: number };
type Overview = { servers: Server[]; unconnected: Unconnected[] };

let cache: { at: number; p: Promise<Overview | null> } | null = null;
function load(): Promise<Overview | null> {
  if (!cache || Date.now() - cache.at > 5000) cache = { at: Date.now(), p: fetch("/api/pbs/overview").then((r) => (r.ok ? r.json() : null)).catch(() => null) };
  return cache.p;
}
function useOverview(): Overview | null | undefined {
  const [o, setO] = useState<Overview | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    const go = () => load().then((d) => live && setO(d));
    go();
    const t = setInterval(go, 30000);
    return () => { live = false; clearInterval(t); };
  }, []);
  return o;
}

const TB = 1e12;
const ago = (s?: number) => { if (!s) return "-"; const m = Math.max(0, Math.round((Date.now() / 1000 - s) / 60)); return m < 60 ? `${m} min ago` : m < 2880 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`; };
const run = (r?: Run) => (r ? <span className={r.ok ? "text-good" : "text-bad"}>{r.ok ? "✓" : "✕"} {ago(r.end)}</span> : <span className="text-muted">-</span>);

/** The cluster view's backup tile. Renders nothing when no backup server is connected, so installs without one look exactly as before. */
export function BackupKpiTile() {
  const o = useOverview();
  if (!o || o.servers.length === 0) return null;
  const up = o.servers.filter((s) => s.reachable).length;
  const used = o.servers.reduce((a, s) => a + s.used_bytes, 0), total = o.servers.reduce((a, s) => a + s.total_bytes, 0);
  const failed = o.servers.reduce((a, s) => a + (s.failed_24h ?? 0), 0);
  return <StatTile label="Backup servers" value={`${up} of ${o.servers.length} reachable${total ? ` · ${Math.round((100 * used) / total)}% used` : ""}${failed ? ` · ${failed} failed in 24 h` : ""}`} />;
}

/** The second table below "Nodes in this cluster": connected backup servers, plus Proxmox backup storages PyXie has no connection for. */
export function BackupServersTable() {
  const o = useOverview();
  if (!o || (o.servers.length === 0 && o.unconnected.length === 0)) return null;
  return (
    <div className="mt-6">
      <div className="text-[11px] uppercase tracking-wider text-muted font-semibold mb-2">Backup servers</div>
      <div className="border border-border rounded overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs text-muted uppercase"><th className="py-1.5 px-3">Server</th><th className="pr-3">Status</th><th className="pr-3">Version</th><th className="pr-3">Datastores</th><th className="pr-3">Last backup</th><th className="pr-3">Last verify</th><th className="pr-3">Last GC</th><th className="pr-3">Failed 24 h</th><th className="pr-3">Wrapper</th></tr></thead>
          <tbody>
            {o.servers.map((s) => (
              <tr key={s.id} className="border-t border-border align-top">
                <td className="py-1.5 px-3"><Link href={`/infrastructure/backup-servers/${s.id}`} className="text-accent hover:underline">{s.name}</Link><div className="text-[11px] text-muted">{s.hostname}</div></td>
                <td className="pr-3">{s.reachable == null ? <span className="text-muted text-xs">not read yet</span> : s.reachable ? <span className="text-[10px] px-1.5 py-0.5 rounded border border-good/60 text-good">reachable</span> : <span className="text-[10px] px-1.5 py-0.5 rounded border border-bad/60 text-bad" title={s.error ?? ""}>not reachable</span>}</td>
                <td className="pr-3">{s.version ?? "-"}{s.updates ? <div className="text-[11px] text-warn">{s.updates} update{s.updates === 1 ? "" : "s"}{s.reboot_required ? ", reboot needed" : ""}</div> : null}</td>
                <td className="pr-3">{s.datastores.length === 0 ? "-" : s.datastores.map((d) => <div key={d.name} className="text-xs">{d.name} {d.total ? `${Math.round((100 * d.used) / d.total)}% (${(d.used / TB).toFixed(1)} / ${(d.total / TB).toFixed(1)} TB)` : `${(d.used / TB).toFixed(1)} TB (${d.backend === "s3" ? "S3" : "no size"})`}</div>)}</td>
                <td className="pr-3">{run(s.last_runs?.backup)}</td><td className="pr-3">{run(s.last_runs?.verify)}</td><td className="pr-3">{run(s.last_runs?.gc)}</td>
                <td className="pr-3">{s.failed_24h == null ? <span className="text-muted">-</span> : s.failed_24h > 0 ? <span className="text-bad">{s.failed_24h}</span> : <span className="text-good">0</span>}</td>
                <td className="pr-3">{s.wrapper_version ? <span className={`text-[10px] px-1.5 py-0.5 rounded border ${s.wrapper_outdated ? "border-warn/60 text-warn" : "border-good/60 text-good"}`}>{s.wrapper_version}{s.wrapper_outdated ? " (outdated)" : ""}</span> : <span className="text-[10px] px-1.5 py-0.5 rounded border border-bad/60 text-bad">not set up</span>}</td>
              </tr>
            ))}
            {o.unconnected.map((u) => (
              <tr key={u.storage + u.server} className="border-t border-border text-muted">
                <td className="py-1.5 px-3">{u.server}<div className="text-[11px]">datastore {u.datastore}</div></td>
                <td colSpan={7} className="pr-3 text-xs">Seen by Proxmox as storage &lsquo;{u.storage}&rsquo;, not connected to PyXie.</td>
                <td className="pr-3"><Link href="/operations/protection/servers" className="text-accent hover:underline text-xs">Connect…</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
