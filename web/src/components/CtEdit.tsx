"use client";

import { useEffect, useState } from "react";
import type { Operation } from "@/lib/api";
import OpPreview from "@/components/OpPreview";
import type { NetInfo } from "@/lib/workloadDetail";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";
import GbInput from "@/components/GbInput";

const input = "bg-surface border border-border rounded px-2 py-1.5 text-sm w-full";

async function dryRun(path: string, body: unknown): Promise<{ op?: Operation; error?: string }> {
  try {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json().catch(() => ({}));
    return r.ok ? { op: d } : { error: d.error || "Could not check that" };
  } catch {
    return { error: "Could not reach PyXie to check that. Try again." };
  }
}

/** Change a container's cores, memory and swap. Applied at once, no restart. */
export function CtResizeAction({ workloadId, cores, memoryMb, swapMb, onDone }: { workloadId: string; cores: number | null; memoryMb: number | null; swapMb: number | null; onDone: () => void }) {
  const me = useMe();
  const [open, setOpen] = useState(false);
  const [c, setC] = useState(String(cores ?? ""));
  const [m, setM] = useState(String(memoryMb ?? ""));
  const [s, setS] = useState(String(swapMb ?? ""));
  const [op, setOp] = useState<Operation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  if (me !== undefined && me?.is_admin !== true) return null;
  const num = (v: string, cur: number | null) => (v.trim() === "" || parseInt(v, 10) === cur ? null : parseInt(v, 10));

  async function preview() {
    setPending(true); setError(null);
    const r = await dryRun("/api/operations/workload-ct-resize/dry-run", { workload_id: workloadId, cores: num(c, cores), memory_mb: num(m, memoryMb), swap_mb: num(s, swapMb) });
    setPending(false);
    if (r.error) { setError(r.error); return; }
    setOp(r.op!); notifyOperationsChanged();
  }
  const close = () => { setOpen(false); setOp(null); setError(null); onDone(); };
  if (!open) return <button type="button" onClick={() => setOpen(true)} className="text-xs text-accent hover:underline">Edit resources…</button>;
  return (
    <div className="basis-full w-full mt-2 mb-3 border border-border rounded p-3 bg-surface2/40 text-xs max-w-xl">
      {op ? <OpPreview op={op} confirmLabel="Confirm & change" progressText="Changing the container" doneText="Done and read back from Proxmox." onFinished={onDone} onClose={close} /> : (
        <div className="space-y-2">
          <div className="text-text font-medium">Cores, memory and swap (applied live, no restart)</div>
          <div className="grid grid-cols-3 gap-2">
            <div><label className="block text-muted mb-1">Cores</label><input type="number" className={input} value={c} onChange={(e) => setC(e.target.value)} /></div>
            <div><label className="block text-muted mb-1">Memory (GB)</label><GbInput className={input} value={m} onChange={setM} /></div>
            <div><label className="block text-muted mb-1">Swap (GB)</label><GbInput className={input} value={s} onChange={setS} /></div>
          </div>
          {error && <div className="text-bad">{error}</div>}
          <div className="flex gap-2 items-center">
            <button type="button" disabled={pending} onClick={preview} className="px-2 py-1 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50">{pending ? "Checking…" : "Preview"}</button>
            <button type="button" onClick={() => setOpen(false)} className="text-muted hover:text-text">cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}

export type CtNicRequest = { action: "add" | "edit" | "remove"; nic?: NetInfo };

/** Add, edit or remove a container network device (bridge, VLAN, firewall, address). */
export function CtNicPanel({ workloadId, workloadName, clusterId, request, onClose }: { workloadId: string; workloadName: string; clusterId: string; request: CtNicRequest; onClose: () => void }) {
  const { action, nic } = request;
  const [bridges, setBridges] = useState<{ name: string; vlan_aware: boolean; missing_on: string[] }[] | null>(null);
  const [bridge, setBridge] = useState(nic?.bridge || "");
  const [tag, setTag] = useState(nic?.vlan_tag ? String(nic.vlan_tag) : "");
  const [firewall, setFirewall] = useState(!!nic?.firewall);
  const [ip, setIp] = useState(nic?.ip || (action === "add" ? "dhcp" : ""));
  const [gw, setGw] = useState("");
  const [mac, setMac] = useState(nic?.mac || "");
  const [op, setOp] = useState<Operation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  useEffect(() => {
    fetch(`/api/ct/options?cluster_id=${clusterId}`).then((r) => r.json()).then((d) => { setBridges(d.bridges); setBridge((b) => b || d.bridges[0]?.name || ""); }).catch(() => setError("Could not read the bridges"));
  }, [clusterId]);

  async function preview() {
    setPending(true); setError(null);
    const r = await dryRun("/api/operations/workload-ct-nic/dry-run", { workload_id: workloadId, action, net_id: nic?.key ?? null, bridge, tag: tag.trim() ? parseInt(tag, 10) : null, firewall, ip: ip.trim() || null, gateway: gw.trim() || null, mac: mac.trim() || null });
    setPending(false);
    if (r.error) { setError(r.error); return; }
    setOp(r.op!); notifyOperationsChanged();
  }
  const done = () => { setOp(null); notifyOperationsChanged(); onClose(); };
  const title = action === "add" ? `Add a network device to ${workloadName}` : action === "edit" ? `Edit ${nic?.key} on ${workloadName}` : `Remove ${nic?.key} from ${workloadName}`;
  return (
    <div className="mt-3 border border-border rounded p-3 bg-surface2/40 text-xs max-w-xl">
      <div className="font-medium text-text mb-1">{title}</div>
      {op ? <OpPreview op={op} danger={action === "remove"} confirmLabel="Confirm" progressText="Changing the network device" doneText="Done and read back from Proxmox." onFinished={() => notifyOperationsChanged()} onClose={done} />
        : action === "remove" ? (
          <div className="space-y-2">
            <p className="text-muted">{nic?.key} ({nic?.bridge}{nic?.vlan_tag ? `, VLAN ${nic.vlan_tag}` : ""}) is removed from the container.</p>
            {error && <div className="text-bad">{error}</div>}
            <div className="flex gap-2 items-center"><button type="button" disabled={pending} onClick={preview} className="px-2 py-1 rounded bg-ink text-on-ink border border-bad hover:bg-bad/10 disabled:opacity-50">{pending ? "Checking…" : "Preview removal"}</button><button type="button" onClick={onClose} className="text-muted hover:text-text">cancel</button></div>
          </div>
        ) : !bridges ? <div className="text-muted">{error || "Reading the bridges…"}</div> : (
          <div className="space-y-2">
            <div className="grid grid-cols-2 gap-2">
              <div><label className="block text-muted mb-1">Bridge</label><select className={input} value={bridge} onChange={(e) => setBridge(e.target.value)}>{bridges.map((b) => <option key={b.name} value={b.name}>{b.name}{b.vlan_aware ? " (VLAN-aware)" : ""}{b.missing_on.length ? `  ⚠ not on ${b.missing_on.join(", ")}` : ""}</option>)}</select></div>
              <div><label className="block text-muted mb-1">VLAN tag (empty = untagged)</label><input type="number" className={input} value={tag} onChange={(e) => setTag(e.target.value)} /></div>
              <div><label className="block text-muted mb-1">IPv4 address</label><input className={input} value={ip} onChange={(e) => setIp(e.target.value)} placeholder="dhcp or 192.168.1.50/24" /></div>
              <div><label className="block text-muted mb-1">Gateway (fixed address only)</label><input className={input} value={gw} onChange={(e) => setGw(e.target.value)} placeholder={nic?.ip && nic.ip !== "dhcp" ? "keep" : ""} /></div>
              <div><label className="block text-muted mb-1">MAC address</label><input className={input} value={mac} onChange={(e) => setMac(e.target.value)} placeholder={action === "add" ? "chosen by Proxmox" : ""} /></div>
              <label className="flex items-center gap-2 pt-5"><input type="checkbox" checked={firewall} onChange={(e) => setFirewall(e.target.checked)} /> Firewall</label>
            </div>
            {error && <div className="text-bad">{error}</div>}
            <div className="flex gap-2 items-center"><button type="button" disabled={pending || !bridge} onClick={preview} className="px-2 py-1 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50">{pending ? "Checking every host…" : "Preview"}</button><button type="button" onClick={onClose} className="text-muted hover:text-text">cancel</button></div>
          </div>
        )}
    </div>
  );
}
