"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import type { Node, Operation, StorageItem } from "@/lib/api";
import MigrationBars from "@/components/MigrationBars";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { isInFlight } from "@/lib/operationStatus";
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
  // Storages this VM's host can reach: the cluster's shared storage and this host's own local storage.
  const options = storage
    .filter((s) => !s.is_missing && s.cluster_id === clusterId && (s.scope === "cluster-shared" || (s.scope === "node-local" && s.node_id === nodeId)) && !["pbs", "iscsi"].includes(s.type ?? ""))
    .filter((s) => !(diskSlot && s.name === diskStorage))
    .sort((a, b) => a.name.localeCompare(b.name));

  // Follow a running move.
  useEffect(() => {
    if (!op || !isInFlight(op.status)) return;
    const t = setInterval(async () => {
      const r = await fetch(`/api/operations/${op.id}`);
      if (r.ok) {
        const next = (await r.json()) as Operation;
        setOp(next);
        if (!isInFlight(next.status)) {
          notifyOperationsChanged();
          router.refresh();
        }
      }
    }, 3000);
    return () => clearInterval(t);
  }, [op, router]);

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
          <p className="text-muted mb-2">Copies the VM&apos;s disks to another storage on {nodeName}, one disk at a time, while it keeps running. Each old copy is deleted only after Proxmox reports its copy finished. Disks already on the chosen storage are left alone.</p>
          <label className="block text-muted mb-1">{diskSlot ? `Move ${diskSlot} to` : "Move the disks to"}</label>
          <select className="bg-surface border border-border rounded px-2 py-1.5 text-sm w-full mb-2" value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">Choose a storage…</option>
            {options.map((s) => (
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
          <div className="text-text mb-1">Moving {workloadName}&apos;s disks to {res?.target_storage}…</div>
          <MigrationBars op={op} />
          <div className="text-muted mt-1">You can leave this page: progress is also in the Tasks panel.</div>
        </div>
      ) : op.status === "completed" ? (
        <div>
          <div className="text-good mb-2">Done: the disks are on {res?.target_storage}, checked against Proxmox.</div>
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
