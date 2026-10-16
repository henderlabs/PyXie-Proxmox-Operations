"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, useRef } from "react";
import type { Operation } from "@/lib/api";
import { Card, CardTitle } from "@/components/Card";
import OpPreview from "@/components/OpPreview";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";

type Pool = { name: string; comment: string; members: { vmid: number; type: string; name: string | null }[] };
type Wl = { id: string; vmid: number; name: string | null; cluster_id: string; type: string };
const input = "bg-surface border border-border rounded px-2 py-1.5 text-sm w-full";

/** Proxmox resource pools: create one, add or remove guests, delete an empty one. Every change is previewed against the real cluster first. */
export default function PoolsManager() {
  const me = useMe();
  const admin = me === undefined || me?.is_admin === true;
  const [clusters, setClusters] = useState<{ id: string; name: string }[]>([]);
  const [clusterId, setClusterId] = useState("");
  const curCluster = useRef(""); // the cluster now chosen: an answer that arrives for another one is ignored
  curCluster.current = clusterId;
  const [pools, setPools] = useState<Pool[] | null>(null);
  const [wls, setWls] = useState<Wl[]>([]);
  const [name, setName] = useState("");
  const [comment, setComment] = useState("");
  const [pick, setPick] = useState<Record<string, string>>({});
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [pend, setPend] = useState<{ op: Operation; label: string; danger: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch("/api/defaults").then((r) => r.json()).then((d) => { setClusters(d.clusters); if (d.clusters[0]) setClusterId(d.clusters[0].id); }).catch(() => setError("Could not load clusters"));
    fetch("/api/workloads").then((r) => r.json()).then((d) => setWls(Array.isArray(d) ? d : [])).catch(() => {});
  }, []);
  const load = useCallback(async () => {
    if (!clusterId) return;
    const mine = clusterId;
    const r = await fetch(`/api/pools/list?cluster_id=${clusterId}`);
    const d = await r.json();
    if (curCluster.current !== mine) return;
    if (!r.ok) { setError(d.error || "Could not read the pools"); return; }
    setPools(d);
  }, [clusterId]);
  useEffect(() => { load(); }, [load]);

  async function preview(path: string, body: object, label: string, danger = false) {
    setBusy(true); setError(null);
    try {
      const r = await fetch(`/api/pools/${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cluster_id: clusterId, ...body }) });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Could not check that"); return; }
      setPend({ op: d, label, danger }); notifyOperationsChanged();
    } finally { setBusy(false); }
  }
  const idOf = (vmid: number) => wls.find((w) => w.vmid === vmid && w.cluster_id === clusterId)?.id;
  if (!admin && me !== undefined) return <Card><div className="text-sm text-muted">Only administrators can change pools.</div></Card>;
  const mine = wls.filter((w) => w.cluster_id === clusterId);
  return (
    <div className="space-y-4">
      <div className="text-sm"><Link href="/infrastructure/workloads" className="text-muted hover:text-text">← Workloads</Link></div>
      {error && <div className="text-bad text-sm">{error}</div>}
      {clusters.length > 1 && <select className={input + " max-w-xs"} value={clusterId} onChange={(e) => { setClusterId(e.target.value); setPools(null); }}>{clusters.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>}
      {pend && (
        <Card>
          <CardTitle>{pend.label}</CardTitle>
          <OpPreview op={pend.op} danger={pend.danger} confirmLabel="Confirm" progressText="Changing the pool" doneText="Done and read back from Proxmox." onFinished={() => notifyOperationsChanged()} onClose={() => { setPend(null); load(); }} />
        </Card>
      )}
      <Card>
        <CardTitle>Create a pool</CardTitle>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-sm items-end">
          <div><label className="block text-xs text-muted mb-1">Pool name</label><input className={input} value={name} onChange={(e) => setName(e.target.value)} placeholder="web-tier" /></div>
          <div><label className="block text-xs text-muted mb-1">Comment (optional)</label><input className={input} value={comment} onChange={(e) => setComment(e.target.value)} /></div>
          <div><button type="button" disabled={busy || !!pend || !name.trim()} onClick={() => preview("create/dry-run", { pool: name.trim(), comment }, `Create pool ${name.trim()}`)} className="px-3 py-1.5 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50 text-xs">Preview</button></div>
        </div>
      </Card>
      {!pools ? <div className="text-sm text-muted">Reading the pools…</div> : pools.length === 0 ? <Card><div className="text-sm text-muted">This cluster has no pools yet.</div></Card> : pools.map((p) => {
        const free = mine.filter((w) => !pools.some((q) => q.members.some((m) => m.vmid === w.vmid)));
        return (
          <Card key={p.name}>
            <CardTitle>{p.name} <span className="text-text/50 font-normal">{p.comment ? `· ${p.comment}` : ""}</span></CardTitle>
            {p.members.length === 0 ? <div className="text-xs text-muted mb-2">Empty.</div> : (
              <div className="divide-y divide-border mb-2">
                {p.members.map((m) => (
                  <div key={m.vmid} className="py-1 flex justify-between items-center text-sm">
                    <span>{m.name ?? m.vmid} <span className="text-muted text-xs">{m.type === "lxc" ? "CT" : "VM"} {m.vmid}</span></span>
                    <button type="button" disabled={busy || !!pend || !idOf(m.vmid)} onClick={() => preview("members/dry-run", { pool: p.name, remove: [idOf(m.vmid)] }, `Remove ${m.name ?? m.vmid} from ${p.name}`)} className="text-xs text-muted hover:text-bad disabled:opacity-40">Remove</button>
                  </div>
                ))}
              </div>
            )}
            <div className="flex flex-wrap gap-2 items-center text-sm">
              <select className={input + " max-w-xs"} value={pick[p.name] ?? ""} onChange={(e) => setPick({ ...pick, [p.name]: e.target.value })}>
                <option value="">Add a guest…</option>{free.map((w) => <option key={w.id} value={w.id}>{w.name ?? w.vmid} ({w.type === "vm" ? "VM" : "CT"} {w.vmid})</option>)}
              </select>
              <button type="button" disabled={busy || !!pend || !pick[p.name]} onClick={() => preview("members/dry-run", { pool: p.name, add: [pick[p.name]] }, `Add a guest to ${p.name}`)} className="px-3 py-1.5 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50 text-xs">Preview add</button>
              {p.members.length === 0 && (<>
                <input className={input + " max-w-[10rem]"} placeholder="type name to delete" value={typed[p.name] ?? ""} onChange={(e) => setTyped({ ...typed, [p.name]: e.target.value })} autoComplete="off" />
                <button type="button" disabled={busy || !!pend || !typed[p.name]} onClick={() => preview("delete/dry-run", { pool: p.name, confirm: typed[p.name] }, `Delete the pool ${p.name}`, true)} className="px-3 py-1.5 rounded bg-ink text-on-ink border border-bad hover:bg-bad/10 disabled:opacity-50 text-xs">Preview delete</button>
              </>)}
            </div>
          </Card>
        );
      })}
      <p className="text-xs text-muted">A guest can be in one pool only. Pools group guests for permissions and for PyXie's defaults; changing membership never touches a guest's disks, power state or settings.</p>
    </div>
  );
}
