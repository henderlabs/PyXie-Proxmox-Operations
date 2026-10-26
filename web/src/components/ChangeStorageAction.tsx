"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import type { Node, Operation, StorageItem } from "@/lib/api";
import MigrationBars from "@/components/MigrationBars";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { isInFlight } from "@/lib/operationStatus";
import { useOperationWatch } from "@/lib/useOperationWatch";
import { useMe } from "@/lib/useMe";

/** Change where a VM's disks live (Proxmox "move disk"): pick a storage, preview what moves and whether it fits, approve, watch it copy. */
export default function ChangeStorageAction({
  workloadId, workloadName, nodeId, clusterId, nodes, storage, diskSlot, diskStorage, startOpen, onClose,
}: {
  workloadId: string; workloadName: string; nodeId: string; clusterId: string; nodes: Node[]; storage: StorageItem[];
  /** Move only this disk (for example "scsi1") instead of every disk. */
  diskSlot?: string;
  /** Where that disk is now, so it is not offered as the target. */
  diskStorage?: string | null;
  /** Show the panel at once, with no button (the caller opened it). */
  startOpen?: boolean;
  onClose?: () => void;
}) {
  const router = useRouter();
  const me = useMe();
  const [open, setOpen] = useState(!!startOpen);
  const [target, setTarget] = useState("");
  const [op, setOp] = useState<Operation | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const nodeName = nodes.find((n) => n.id === nodeId)?.name ?? "this host";
  // The storages this host can put VM disks on (active, and set up for VM images): the same list the Add disk form uses.
  // The list is read live from every host, so it takes a few seconds: the dropdown says Loading until it arrives. If it cannot be
  // read, every reachable storage is offered and the preview still refuses an unusable one.
  const [holdsDisks, setHoldsDisks] = useState<Set<string> | null>(null);
  const [listReady, setListReady] = useState(false);
  useEffect(() => {
    if (!open) return;
    fetch(`/api/vm-create/options?cluster_id=${clusterId}`).then((r) => (r.ok ? r.json() : null)).then((d: { nodes?: { name: string; storages: { name: string }[] }[] } | null) => {
      const n = d?.nodes?.find((x) => x.name === nodeName);
      if (n) setHoldsDisks(new Set(n.storages.map((x) => x.name)));
    }).catch(() => {}).finally(() => setListReady(true));
  }, [open, clusterId, nodeName]);

  // Storages this VM's host can reach: the cluster's shared storage and this host's own local storage.
  const options = storage
    .filter((s) => !s.is_missing && s.cluster_id === clusterId && (s.scope === "cluster-shared" || (s.scope === "node-local" && s.node_id === nodeId)) && !["pbs", "iscsi"].includes(s.type ?? ""))
    .filter((s) => !(diskSlot && s.name === diskStorage))
    .filter((s) => !holdsDisks || holdsDisks.has(s.name))
    .sort((a, b) => a.name.localeCompare(b.name));

  // Follow the move whoever approves it: the Tasks panel can start or dismiss it while this panel is open.
  useOperationWatch(op, {
    onUpdate: (next) => setOp(next),
    onGone: () => reset(),
    onSettled: () => { notifyOperationsChanged(); router.refresh(); },
  });

  if (me !== undefined && me?.is_admin !== true) return null;

  function reset() {
    setOpen(false); setTarget(""); setOp(null); setError(null); onClose?.();
  }

  async function preview() {
    setPending(true); setError(null);
    try {
      const r = await fetch("/api/operations/workload-storage-moves/dry-run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workload_id: workloadId, target_storage: target, ...(diskSlot ? { disk_slot: diskSlot } : {}) }) });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Preview failed"); return; }
      setOp(d); notifyOperationsChanged();
    } finally { setPending(false); }
  }

  async function approve() {
    if (!op) return;
    setPending(true); setError(null);
    try {
      const r = await fetch(`/api/operations/${op.id}/approve`, { method: "POST" });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Approve failed"); return; }
      setOp(d); notifyOperationsChanged();
    } finally { setPending(false); }
  }

  async function cancelPreview() {
    if (op && op.status === "awaiting_approval") fetch(`/api/operations/${op.id}/dismiss`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dismissed: true }) }).catch(() => {});
    notifyOperationsChanged();
    reset();
    router.refresh();
  }

  if (!open) {
    return <button onClick={() => setOpen(true)} className="px-3 py-1.5 rounded text-sm font-medium border border-border text-text hover:bg-surface2">Change storage…</button>;
  }

  const res = op?.dry_run_result as { disks?: { slot: string; storage: string; size_bytes: number }[]; reasons?: string[]; target_storage?: string; total_bytes?: number } | null | undefined;
  return (
    <div className="mt-2 border border-border rounded p-3 bg-surface2/40 text-xs w-full max-w-md">
      <div className="font-medium text-text mb-1">{diskSlot ? `Move ${diskSlot} of ${workloadName}` : `Change storage for ${workloadName}`}</div>
      {!op ? (
        <>
          <p className="text-muted mb-2">{diskSlot ? `Copies ${diskSlot} to another storage on ${nodeName} while the VM keeps running. The old copy is deleted only after Proxmox reports the copy finished.` : `Copies the VM's disks to another storage on ${nodeName}, one disk at a time, while it keeps running. Each old copy is deleted only after Proxmox reports its copy finished. Disks already on the chosen storage are left alone.`}</p>
          <label className="block text-muted mb-1">{diskSlot ? `Move ${diskSlot} to` : "Move the disks to"}</label>
          <select disabled={!listReady} className="bg-surface border border-border rounded px-2 py-1.5 text-sm w-full mb-2 disabled:opacity-60" value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">{listReady ? "Choose a storage…" : "Loading storages…"}</option>
            {listReady && options.map((s) => (
              <option key={s.id} value={s.name}>
                {s.name} ({s.scope === "cluster-shared" ? "shared" : "local to " + nodeName}{s.capacity_bytes ? `, ${Math.round(((s.capacity_bytes - (s.used_bytes ?? 0)) / 1024 ** 3))} GB free` : ""})
              </option>
            ))}
          </select>
          {error && <div className="text-bad mb-2">{error}</div>}
          <div className="flex gap-2 items-center">
            <button disabled={!target || pending} onClick={preview} className="px-2 py-1 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50">{pending ? "Checking…" : "Preview"}</button>
            <button onClick={reset} className="text-muted hover:text-text">cancel</button>
          </div>
        </>
      ) : op.status === "blocked" ? (
        <div>
          <div className="text-bad mb-2">Blocked: {(res?.reasons || []).join("; ") || "see reasons"}</div>
          <button onClick={cancelPreview} className="text-muted hover:text-text">back</button>
        </div>
      ) : isInFlight(op.status) ? (
        <div>
          <div className="text-text mb-1">{diskSlot ? `Moving ${diskSlot} of ${workloadName} to ${res?.target_storage}…` : `Moving ${workloadName}'s disks to ${res?.target_storage}…`}</div>
          <MigrationBars op={op} />
          <div className="text-muted mt-1">You can leave this page: progress is also in the Tasks panel.</div>
        </div>
      ) : op.status === "completed" ? (
        <div>
          <div className="text-good mb-2">{diskSlot ? `Done: ${diskSlot} is on ${res?.target_storage}, checked against Proxmox.` : `Done: the disks are on ${res?.target_storage}, checked against Proxmox.`}</div>
          <button onClick={reset} className="text-muted hover:text-text">close</button>
        </div>
      ) : op.status === "failed" ? (
        <div>
          <div className="text-bad mb-2">{op.error || "The move failed"}</div>
          <button onClick={reset} className="text-muted hover:text-text">close</button>
        </div>
      ) : (
        <div>
          <div className="text-text mb-1">Will move to {res?.target_storage}:</div>
          <ul className="text-muted mb-2 list-disc list-inside">
            {(res?.disks || []).map((d) => <li key={d.slot}>{d.slot}: {Math.round(d.size_bytes / 1024 ** 3)} GB, now on {d.storage}</li>)}
          </ul>
          <ul className="text-muted mb-2 list-disc list-inside">{(res?.reasons || []).map((r, i) => <li key={i}>{r}</li>)}</ul>
          {error && <div className="text-bad mb-2">{error}</div>}
          <div className="flex gap-2 items-center">
            <button disabled={pending} onClick={approve} className="px-2 py-1 rounded bg-ink text-on-ink border border-warn hover:bg-warn/10 disabled:opacity-50">{pending ? "Starting…" : "Confirm & move"}</button>
            <button onClick={cancelPreview} className="text-muted hover:text-text">cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}
