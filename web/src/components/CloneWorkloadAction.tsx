"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { Operation } from "@/lib/api";
import OpPreview from "@/components/OpPreview";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";

type Opts = { nodes: { name: string; online: boolean; maintenance: boolean; mem_total: number | null; mem_used: number | null; storages: { name: string; type: string | null; avail: number | null; shared: boolean }[] }[]; pools: string[]; next_vmid: number | null };
const GIB = 1073741824;
const input = "bg-surface border border-border rounded px-2 py-1.5 text-sm w-full";

/** Clone this VM (or template): a full copy, or a linked clone of a template, onto any online host, with a new ID and name. */
export default function CloneWorkloadAction({ workloadId, workloadName, clusterId, nodeName, isTemplate, tags, autoOpen }: { workloadId: string; workloadName: string; clusterId: string; nodeName: string; isTemplate: boolean; tags: string[]; autoOpen?: boolean }) {
  const me = useMe();
  const [open, setOpen] = useState(!!autoOpen);
  const [opts, setOpts] = useState<Opts | null>(null);
  const [name, setName] = useState(`${workloadName}-clone`);
  const [vmid, setVmid] = useState("");
  const [node, setNode] = useState(nodeName);
  const [full, setFull] = useState(true);
  const [storage, setStorage] = useState("");
  const [pool, setPool] = useState("");
  const [tagText, setTagText] = useState(tags.join(", "));
  const [start, setStart] = useState(false);
  const [op, setOp] = useState<Operation | null>(null);
  const [newId, setNewId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!open || opts) return;
    fetch(`/api/vm-create/options?cluster_id=${clusterId}`).then((r) => r.json()).then(setOpts).catch(() => setError("Could not read the cluster from Proxmox"));
  }, [open, opts, clusterId]);
  useEffect(() => { if (isTemplate) setFull(false); }, [isTemplate]);
  if (me !== undefined && me?.is_admin !== true) return null;

  const tnode = opts?.nodes.find((n) => n.name === node);
  async function preview() {
    setPending(true); setError(null);
    try {
      const body = { workload_id: workloadId, name: name.trim(), newid: vmid ? parseInt(vmid, 10) : null, target_node: node, full, storage: full && storage ? storage : null, pool: pool || null, tags: tagText.split(/[,;\s]+/).filter(Boolean), start_after: start };
      const r = await fetch("/api/operations/workload-clone/dry-run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Could not check that"); return; }
      setOp(d); notifyOperationsChanged();
    } finally { setPending(false); }
  }
  async function finished() {
    if (!op) return;
    const r = await fetch(`/api/operations/${op.id}`);
    if (r.ok) setNewId((((await r.json()) as Operation).context as { workload_id?: string } | null)?.workload_id ?? null);
  }

  if (!open) return <button type="button" onClick={() => setOpen(true)} className="px-2.5 py-1 rounded border border-border text-sm text-muted hover:text-text">Clone…</button>;
  return (
    <div className="basis-full w-full mt-2 border border-border rounded p-3 bg-surface2/40 text-xs max-w-2xl">
      <div className="font-medium text-text mb-1">Clone {workloadName}{isTemplate ? " (template)" : ""}</div>
      {op ? (
        <div>
          <OpPreview op={op} confirmLabel="Confirm & clone" progressText="Cloning (a large VM takes a while; progress is in the Tasks panel)" doneText="Cloned, read back from Proxmox." onFinished={finished} onClose={() => { setOp(null); if (newId) setOpen(false); }} />
          {newId && <div className="mt-2 text-sm"><Link href={`/infrastructure/workloads/${newId}`} className="text-accent hover:underline">Open the clone →</Link></div>}
        </div>
      ) : !opts ? <div className="text-muted">{error || "Reading the cluster…"}</div> : (
        <div className="space-y-2">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
            <div><label className="block text-muted mb-1">Name</label><input className={input} value={name} onChange={(e) => setName(e.target.value)} /></div>
            <div><label className="block text-muted mb-1">VM ID</label><input type="number" className={input} value={vmid} onChange={(e) => setVmid(e.target.value)} placeholder={opts.next_vmid ? `automatic (${opts.next_vmid})` : "automatic"} /></div>
            <div><label className="block text-muted mb-1">Host</label>
              <select className={input} value={node} onChange={(e) => { setNode(e.target.value); setStorage(""); }}>{opts.nodes.map((n) => <option key={n.name} value={n.name} disabled={!n.online || n.maintenance}>{n.name}{n.maintenance ? " (maintenance)" : !n.online ? " (offline)" : ""}{n.mem_total && n.mem_used != null ? ` ${((n.mem_total - n.mem_used) / GIB).toFixed(0)} GiB free` : ""}</option>)}</select></div>
            <div><label className="block text-muted mb-1">Kind</label>
              <select className={input} value={full ? "full" : "linked"} onChange={(e) => setFull(e.target.value === "full")}>
                <option value="full">Full copy (independent)</option>{isTemplate && <option value="linked">Linked clone (shares disks with the template)</option>}
              </select></div>
            <div><label className="block text-muted mb-1">Target storage (full copy)</label>
              <select className={input} value={storage} disabled={!full} onChange={(e) => setStorage(e.target.value)}><option value="">Same as the original</option>{(tnode?.storages ?? []).map((s) => <option key={s.name} value={s.name}>{s.name} ({s.type}{s.avail ? `, ${(s.avail / GIB).toFixed(0)} GiB free` : ""})</option>)}</select></div>
            <div><label className="block text-muted mb-1">Pool</label><select className={input} value={pool} onChange={(e) => setPool(e.target.value)}><option value="">None</option>{opts.pools.map((p) => <option key={p}>{p}</option>)}</select></div>
            <div className="md:col-span-2"><label className="block text-muted mb-1">Tags</label><input className={input} value={tagText} onChange={(e) => setTagText(e.target.value)} /></div>
            <label className="flex items-center gap-2 pt-5"><input type="checkbox" checked={start} onChange={(e) => setStart(e.target.checked)} /> Start it afterwards</label>
          </div>
          {error && <div className="text-bad">{error}</div>}
          <div className="flex gap-2 items-center">
            <button type="button" disabled={pending || !name.trim()} onClick={preview} className="px-2 py-1 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50">{pending ? "Checking…" : "Preview"}</button>
            <button type="button" onClick={() => setOpen(false)} className="text-muted hover:text-text">cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}
