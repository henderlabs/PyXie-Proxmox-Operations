"use client";

import { useEffect, useState } from "react";
import type { Operation } from "@/lib/api";
import OpPreview from "@/components/OpPreview";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";

type Iso = { volid: string; size: number | null; nodes: string[] };
const GIB = 1073741824;

/** Attach, change or eject the ISO in a CD/DVD drive of a VM (slot = the drive, or null to add a new drive). */
export default function CdromAction({ workloadId, clusterId, nodeName, slot, current, label, onDone }: { workloadId: string; clusterId: string; nodeName: string; slot: string | null; current?: string | null; label: string; onDone: () => void }) {
  const me = useMe();
  const [open, setOpen] = useState(false);
  const [isos, setIsos] = useState<Iso[] | null>(null);
  const [volid, setVolid] = useState("");
  const [bootFirst, setBootFirst] = useState(false);
  const [op, setOp] = useState<Operation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  useEffect(() => {
    if (!open || isos) return;
    fetch(`/api/vm-create/options?cluster_id=${clusterId}`).then((r) => r.json()).then((d: { isos: Iso[] }) => setIsos(d.isos.filter((i) => i.nodes.includes(nodeName)))).catch(() => setError("Could not list the ISOs"));
  }, [open, isos, clusterId, nodeName]);
  if (me !== undefined && me?.is_admin !== true) return null;

  async function run(eject: boolean) {
    setPending(true); setError(null);
    try {
      const r = await fetch("/api/operations/workload-cdrom/dry-run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workload_id: workloadId, slot, volid: eject ? null : volid, boot_first: !eject && bootFirst }) });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Could not check that"); return; }
      setOp(d); notifyOperationsChanged();
    } finally { setPending(false); }
  }
  const close = () => { setOpen(false); setOp(null); setError(null); setVolid(""); onDone(); };
  if (!open) return <button type="button" onClick={() => setOpen(true)} className="text-xs text-accent hover:underline">{label}</button>;
  return (
    <div className="mt-2 border border-border rounded p-3 bg-surface2/40 text-xs max-w-xl">
      {op ? <OpPreview op={op} confirmLabel="Confirm" progressText="Changing the drive" doneText="Done and read back from Proxmox." onFinished={onDone} onClose={close} /> : !isos ? <div className="text-muted">{error || "Reading the ISOs…"}</div> : (
        <div className="space-y-2">
          <div className="text-text font-medium">{slot ? `Drive ${slot}${current && current !== "none" ? ` (now ${current})` : " (empty)"}` : "Add a CD/DVD drive"}</div>
          <select className="bg-surface border border-border rounded px-2 py-1.5 text-sm w-full" value={volid} onChange={(e) => setVolid(e.target.value)}>
            <option value="">Choose an ISO…</option>{isos.map((i) => <option key={i.volid} value={i.volid}>{i.volid}{i.size ? ` (${(i.size / GIB).toFixed(1)} GiB)` : ""}</option>)}
          </select>
          <label className="flex items-center gap-2"><input type="checkbox" checked={bootFirst} onChange={(e) => setBootFirst(e.target.checked)} /> Boot from it at the next start</label>
          {error && <div className="text-bad">{error}</div>}
          <div className="flex gap-2 items-center">
            <button type="button" disabled={pending || !volid} onClick={() => run(false)} className="px-2 py-1 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50">{pending ? "Checking…" : "Preview"}</button>
            {slot && current && current !== "none" && <button type="button" disabled={pending} onClick={() => run(true)} className="px-2 py-1 rounded border border-warn text-warn hover:bg-warn/10 disabled:opacity-50">Eject instead</button>}
            <button type="button" onClick={() => setOpen(false)} className="text-muted hover:text-text">cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}
