"use client";

import { useEffect, useState } from "react";
import type { Operation } from "@/lib/api";
import OpPreview from "@/components/OpPreview";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { DISK_BUS, DISK_CACHE } from "@/lib/vmOptions";

export type DiskRequest = { action: "add" | "detach" | "attach" | "delete_unused" | "move_to_vm" | "edit_options" | "remove_cdrom"; disk?: { key: string; volume: string; size: string | null; options?: Record<string, string> } };
type Store = { name: string; type: string | null; avail: number | null; shared: boolean };
type Wl = { id: string; name: string | null; vmid: number; type: string; node_id?: string; is_missing?: boolean; status?: string };
const input = "bg-surface border border-border rounded px-2 py-1.5 text-sm w-full";
const GIB = 1073741824;
const VM_TITLES: Record<DiskRequest["action"], string> = { add: "Add a disk", detach: "Detach a disk", attach: "Attach an unused disk", delete_unused: "Delete an unused disk", move_to_vm: "Move a disk to another VM", edit_options: "Change a disk's options", remove_cdrom: "Remove a CD/DVD drive" };
const CT_TITLES: Record<string, string> = { add: "Add a mount point", detach: "Detach a mount point", attach: "Attach an unused volume", delete_unused: "Delete an unused volume" };
const on = (v: string | undefined) => v === "on" || v === "1";

/** One disk (VM) or mount point (container) operation, previewed against the real configuration before anything changes. */
export default function DiskPanel({ workloadId, clusterId, nodeName, request, lxc = false, onClose }: { workloadId: string; clusterId: string; nodeName: string | null; request: DiskRequest; lxc?: boolean; onClose: () => void }) {
  const a = request.action;
  const o = request.disk?.options ?? {};
  const [bus, setBus] = useState(request.disk?.key.match(/^(scsi|virtio|sata|ide)/)?.[1] ?? "scsi");
  const [storages, setStorages] = useState<Store[]>([]);
  const [storage, setStorage] = useState("");
  const [size, setSize] = useState("32");
  const [path, setPath] = useState("");
  const [cache, setCache] = useState(o.cache ?? "none");
  const [flags, setFlags] = useState({ discard: on(o.discard) || o.discard === "ignore", ssd: on(o.ssd), iothread: a === "add" ? true : on(o.iothread), backup: o.backup !== "0", readonly: on(o.ro) });
  const [targets, setTargets] = useState<Wl[]>([]);
  const [target, setTarget] = useState("");
  const [confirm, setConfirm] = useState("");
  const [op, setOp] = useState<Operation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (a === "add") {
      const url = lxc ? `/api/ct/options?cluster_id=${clusterId}` : `/api/vm-create/options?cluster_id=${clusterId}`;
      fetch(url).then((r) => r.json()).then((d) => {
        const n = (d.nodes as { name: string; storages: Store[] }[] | undefined)?.find((x) => x.name === nodeName);
        setStorages(n?.storages ?? []); setStorage((s) => s || n?.storages?.[0]?.name || "");
      }).catch(() => setError("Could not read the datastores"));
    }
    if (a === "move_to_vm") {
      fetch("/api/workloads").then((r) => r.json()).then((d: Wl[]) => {
        const all = Array.isArray(d) ? d : [];
        const here = all.find((w) => w.id === workloadId)?.node_id;
        setTargets(all.filter((w) => w.type === "vm" && w.id !== workloadId && !!here && w.node_id === here && !w.is_missing));
      }).catch(() => {});
    }
  }, [a, clusterId, nodeName, workloadId, lxc]);

  async function preview() {
    setPending(true); setError(null);
    try {
      const body: Record<string, unknown> = { workload_id: workloadId, action: a };
      if (a === "add") Object.assign(body, lxc ? { storage, size_gb: parseInt(size, 10), path: path.trim(), backup: flags.backup } : { bus, storage, size_gb: parseInt(size, 10), cache, discard: flags.discard, ssd: flags.ssd, iothread: flags.iothread });
      if (a === "detach" || a === "delete_unused" || a === "remove_cdrom") Object.assign(body, { key: request.disk!.key, confirm });
      if (a === "attach") Object.assign(body, lxc ? { key: request.disk!.key, path: path.trim(), backup: flags.backup } : { key: request.disk!.key, bus });
      if (a === "move_to_vm") Object.assign(body, { key: request.disk!.key, bus, target_workload_id: target });
      if (a === "edit_options") Object.assign(body, { key: request.disk!.key, cache, ...flags });
      const r = await fetch("/api/operations/workload-disk-change/dry-run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { setError(d.error || "Could not check that"); return; }
      setOp(d); notifyOperationsChanged();
    } catch { setError("Could not reach PyXie to check that. Try again."); } finally { setPending(false); }
  }
  const volName = request.disk?.volume.split(":").pop() ?? "";
  const unit = lxc ? "mount point" : "disk";
  const title = (lxc ? CT_TITLES[a] : VM_TITLES[a]) ?? a;
  const ready = a === "add" ? !!storage && parseInt(size, 10) > 0 && (!lxc || !!path.trim()) : a === "attach" && lxc ? !!path.trim() : a === "delete_unused" ? confirm.trim() === volName : a === "move_to_vm" ? !!target : true;
  const confirmLabel = a === "delete_unused" ? `Confirm & DELETE the ${lxc ? "volume" : "disk"}` : `Confirm: ${title.toLowerCase()}`;
  const check = (k: "discard" | "ssd" | "iothread" | "backup" | "readonly", label: string, help?: string) => (
    <label className="flex items-start gap-2"><input type="checkbox" className="mt-0.5" checked={flags[k]} onChange={(e) => setFlags({ ...flags, [k]: e.target.checked })} /><span>{label}{help && <span className="block text-muted">{help}</span>}</span></label>
  );

  return (
    <div className={`mt-3 border rounded p-3 text-xs ${a === "delete_unused" ? "border-bad/50 bg-bad/5" : "border-border bg-surface2/40"}`}>
      <div className={`font-medium mb-2 ${a === "delete_unused" ? "text-bad" : "text-text"}`}>{title}{request.disk ? ` · ${request.disk.key}` : ""}</div>
      {op ? (
        <OpPreview key={op.id} op={op} danger={a === "delete_unused"} confirmLabel={confirmLabel} progressText={`Changing the ${unit}`} doneText="Done and read back from Proxmox." onFinished={() => notifyOperationsChanged()} onClose={onClose} />
      ) : (
        <div className="space-y-3">
          {a === "detach" && <p className="text-muted">{lxc ? "The container" : "The VM"} stops seeing {request.disk!.key} ({request.disk!.volume}). Nothing is deleted: the data stays on the datastore as an <span className="text-text">unused {lxc ? "volume" : "disk"}</span> you can attach again here{lxc ? "" : ", or move to another VM"}. {lxc ? "The container" : "The VM"} must be stopped.</p>}
          {a === "attach" && <p className="text-muted">Gives {lxc ? "the container" : "the VM"} the unused {lxc ? "volume" : "disk"} {request.disk!.volume} again{lxc ? ", at the folder you choose" : ", on the bus you choose"}.</p>}
          {a === "move_to_vm" && <p className="text-muted">Hands {request.disk!.key} ({request.disk!.volume}) to another VM on the same host. The data is not copied: the volume is re-labelled, so it is quick. This VM must be stopped.</p>}
          {a === "delete_unused" && <p className="text-bad">Destroys {request.disk!.volume} and everything on it. This cannot be undone.{lxc ? "" : " Consider moving it to another VM instead."}</p>}
          {a === "add" && <p className="text-muted">{lxc ? "A new, empty volume mounted inside the container at the folder you choose. The container must be stopped." : "A new, empty disk. SCSI, VirtIO and SATA disks can be added while the VM runs."}</p>}
          {a === "edit_options" && <p className="text-muted">Changes how Proxmox presents and treats this disk. Most options take effect at the VM&apos;s next stop and start.</p>}
          {a === "remove_cdrom" && <p className="text-muted">Removes the CD/DVD drive from the VM. Any ISO in it stays in the ISO library.</p>}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {!lxc && (a === "add" || a === "attach" || a === "move_to_vm") && (
              <div><label className="block text-muted mb-1">Bus</label>
                <select className={input} value={bus} onChange={(e) => setBus(e.target.value)}>{DISK_BUS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}</select>
                <div className="text-muted mt-1">{DISK_BUS.find((c) => c.value === bus)?.help}</div></div>
            )}
            {a === "add" && (<>
              <div><label className="block text-muted mb-1">Datastore</label>
                <select className={input} value={storage} onChange={(e) => setStorage(e.target.value)}>{storages.map((s) => <option key={s.name} value={s.name}>{s.name} ({s.type}{s.shared ? ", shared" : ""}{s.avail ? `, ${(s.avail / GIB).toFixed(0)} GiB free` : ""})</option>)}</select></div>
              <div><label className="block text-muted mb-1">Size (GiB)</label><input type="number" min={1} className={input} value={size} onChange={(e) => setSize(e.target.value)} /></div>
            </>)}
            {lxc && (a === "add" || a === "attach") && <div><label className="block text-muted mb-1">Mount at (folder inside the container)</label><input className={input} value={path} onChange={(e) => setPath(e.target.value)} placeholder="/data" /></div>}
            {(a === "add" || a === "edit_options") && !lxc && (
              <div><label className="block text-muted mb-1">Cache</label>
                <select className={input} value={cache} onChange={(e) => setCache(e.target.value)}>{DISK_CACHE.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}</select>
                <div className="text-muted mt-1">{DISK_CACHE.find((c) => c.value === cache)?.help}</div></div>
            )}
            {(a === "add" || a === "edit_options") && !lxc && (
              <div className="space-y-1.5 md:col-span-2">
                {check("discard", "Discard (TRIM)", "Lets thin storage take back space the guest has freed.")}
                {check("ssd", "SSD emulation", "Tells the guest the disk is an SSD. Not available on VirtIO Block.")}
                {check("iothread", "IO thread", "A thread of its own for this disk. Best with the VirtIO SCSI single controller.")}
                {a === "edit_options" && check("backup", "Include in backups", "Untick to leave this disk out of every backup of the VM.")}
                {a === "edit_options" && check("readonly", "Read only", "The guest cannot write to it.")}
              </div>
            )}
            {lxc && (a === "add" || a === "attach") && <div className="space-y-1.5">{check("backup", "Include in backups", "Untick to leave this mount point out of the container's backups.")}</div>}
            {a === "move_to_vm" && (
              <div className="md:col-span-2"><label className="block text-muted mb-1">Move it to</label>
                <select className={input} value={target} onChange={(e) => setTarget(e.target.value)}><option value="">Choose a VM on {nodeName ?? "this host"}…</option>{targets.map((w) => <option key={w.id} value={w.id}>{w.name ?? `VM ${w.vmid}`} ({w.vmid})</option>)}</select>
                {targets.length === 0 && <div className="text-muted mt-1">No other VM on this host. Proxmox moves a disk between VMs on the same host only.</div>}</div>
            )}
            {a === "delete_unused" && (
              <div className="md:col-span-2"><label className="block text-muted mb-1">Type the {lxc ? "volume" : "disk"}&apos;s name ({volName}) to confirm</label><input className={input} value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="off" /></div>
            )}
          </div>
          {error && <div className="text-bad">{error}</div>}
          <div className="flex gap-2 items-center">
            <button type="button" disabled={pending || !ready} onClick={preview} className="px-3 py-1.5 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50">{pending ? "Checking…" : "Preview"}</button>
            <button type="button" onClick={onClose} className="text-muted hover:text-text">cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}
