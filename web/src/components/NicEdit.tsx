"use client";

import { useEffect, useState } from "react";
import type { Operation } from "@/lib/api";
import OpPreview from "@/components/OpPreview";
import type { NetInfo } from "@/lib/workloadDetail";
import { notifyOperationsChanged } from "@/lib/operationsBus";

type NicOptions = {
  supported: boolean; models: string[]; next_slot: string | null; hosts: string[]; error: string | null;
  bridges: { name: string; vlan_aware: boolean; allowed_vlans: string | null; missing_on: string[] }[];
};

export type NicEditRequest = { action: "add" | "edit" | "remove"; nic?: NetInfo };

/** Add, edit or remove one network device. Every bridge on every host is checked in the preview: a bridge the VM's host lacks or a VLAN
 * it cannot carry blocks the change; a bridge another host lacks is a warning (the VM could not move there). */
export default function NicEditPanel({ workloadId, workloadName, running, request, onClose }: { workloadId: string; workloadName: string; running: boolean; request: NicEditRequest; onClose: () => void }) {
  const { action, nic } = request;
  const [opts, setOpts] = useState<NicOptions | null>(null);
  const [model, setModel] = useState(nic?.model || "virtio");
  const [bridge, setBridge] = useState(nic?.bridge || "");
  const [tag, setTag] = useState(nic?.vlan_tag ? String(nic.vlan_tag) : "");
  const [mac, setMac] = useState(nic?.mac || "");
  const [firewall, setFirewall] = useState(!!nic?.firewall);
  const [linkDown, setLinkDown] = useState(!!nic?.link_down);
  const [op, setOp] = useState<Operation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (action !== "add") return;
    fetch(`/api/workloads/${workloadId}/defaults`).then((r) => (r.ok ? r.json() : null)).then((d: { values: Record<string, unknown>; sources: Record<string, string> } | null) => {
      if (!d) return;
      if (d.sources.nic_model !== "built-in") setModel(String(d.values.nic_model));
      if (d.sources.nic_bridge !== "built-in" && d.values.nic_bridge) setBridge(String(d.values.nic_bridge));
      if (d.sources.nic_vlan !== "built-in" && d.values.nic_vlan) setTag(String(d.values.nic_vlan));
      if (d.sources.nic_firewall !== "built-in") setFirewall(Boolean(d.values.nic_firewall));
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workloadId, action]);

  useEffect(() => {
    fetch(`/api/workloads/${workloadId}/nic-options`).then(async (r) => { const d = await r.json(); if (!r.ok || !Array.isArray(d.bridges)) throw new Error(d.error || "options"); return d as NicOptions; }).then((d: NicOptions) => {
      setOpts(d);
      setBridge((b) => b || (d.bridges.length ? d.bridges[0].name : ""));
    }).catch(() => setError("Could not read the bridges from Proxmox"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workloadId]);

  async function preview() {
    setPending(true); setError(null);
    try {
      const body = {
        workload_id: workloadId, action, net_id: action === "add" ? opts?.next_slot ?? null : nic?.key,
        model, bridge, tag: tag.trim() ? parseInt(tag, 10) : null, firewall, mac: mac.trim() || null, link_down: linkDown,
      };
      const r = await fetch("/api/operations/workload-nic-change/dry-run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Could not check that"); return; }
      setOp(d); notifyOperationsChanged();
    } finally { setPending(false); }
  }

  const done = () => { setOp(null); notifyOperationsChanged(); onClose(); };
  const sel = "bg-surface border border-border rounded px-2 py-1.5 text-sm w-full";
  const title = action === "add" ? `Add a network device to ${workloadName}` : action === "edit" ? `Edit ${nic?.key} on ${workloadName}` : `Remove ${nic?.key} from ${workloadName}`;
  return (
    <div className="mt-3 border border-border rounded p-3 bg-surface2/40 text-xs max-w-xl">
      <div className="font-medium text-text mb-1">{title}</div>
      {op ? (
        <OpPreview
          op={op} danger={action === "remove"} confirmLabel={action === "add" ? "Confirm & add device" : action === "edit" ? "Confirm & change device" : "Confirm & remove device"}
          progressText="Changing the network device" doneText="Done and read back from Proxmox." onFinished={() => notifyOperationsChanged()} onClose={done}
        />
      ) : action === "remove" ? (
        <div className="space-y-2">
          <p className="text-muted">{nic?.key} ({nic?.model} on {nic?.bridge}{nic?.vlan_tag ? `, VLAN ${nic.vlan_tag}` : ""}) is removed from the VM. The preview says what that does to its connectivity.</p>
          {error && <div className="text-bad">{error}</div>}
          <div className="flex gap-2 items-center">
            <button type="button" disabled={pending} onClick={preview} className="px-2 py-1 rounded bg-ink text-on-ink border border-bad hover:bg-bad/10 disabled:opacity-50">{pending ? "Checking…" : "Preview removal"}</button>
            <button type="button" onClick={onClose} className="text-muted hover:text-text">cancel</button>
          </div>
        </div>
      ) : !opts ? (
        <div className="text-muted">{error || "Reading every host's bridges…"}</div>
      ) : (
        <div className="space-y-2">
          <p className="text-muted">Checked against {opts.hosts.length} host{opts.hosts.length === 1 ? "" : "s"}: a bridge or VLAN the VM&apos;s own host cannot carry blocks the change; one that another host lacks is a warning.</p>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-muted mb-1">Model</label>
              <select className={sel} value={model} onChange={(e) => setModel(e.target.value)}>{opts.models.map((m) => <option key={m} value={m}>{m}{m === "virtio" ? " (fastest, needs drivers on Windows)" : ""}</option>)}</select>
            </div>
            <div>
              <label className="block text-muted mb-1">Bridge</label>
              <select className={sel} value={bridge} onChange={(e) => setBridge(e.target.value)}>
                {opts.bridges.map((b) => <option key={b.name} value={b.name}>{b.name}{b.vlan_aware ? " (VLAN-aware)" : ""}{b.missing_on.length ? `  ⚠ not on ${b.missing_on.join(", ")}` : ""}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-muted mb-1">VLAN tag (empty = untagged)</label>
              <input type="number" min={1} max={4094} className={sel} value={tag} onChange={(e) => setTag(e.target.value)} placeholder="untagged" />
            </div>
            <div>
              <label className="block text-muted mb-1">MAC address</label>
              <input className={sel} value={mac} onChange={(e) => setMac(e.target.value)} placeholder={action === "add" ? "chosen by Proxmox" : ""} />
            </div>
          </div>
          <label className="flex items-center gap-2"><input type="checkbox" checked={firewall} onChange={(e) => setFirewall(e.target.checked)} /> Firewall (applies only where the Proxmox firewall is enabled)</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={linkDown} onChange={(e) => setLinkDown(e.target.checked)} /> Disconnected (link down)</label>
          {running && <p className="text-muted">The VM is running: Proxmox hot-plugs the change; a model change waits for its next shutdown and start.</p>}
          {error && <div className="text-bad">{error}</div>}
          <div className="flex gap-2 items-center">
            <button type="button" disabled={pending || !bridge} onClick={preview} className="px-2 py-1 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50">{pending ? "Checking every host…" : "Preview"}</button>
            <button type="button" onClick={onClose} className="text-muted hover:text-text">cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}
