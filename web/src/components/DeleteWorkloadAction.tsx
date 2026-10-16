"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { Operation } from "@/lib/api";
import OpPreview from "@/components/OpPreview";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";

/** Delete a stopped guest. Irreversible, so: type its exact name, read the preview, confirm. Only offered while the guest is stopped. */
export default function DeleteWorkloadAction({ workloadId, workloadName, status }: { workloadId: string; workloadName: string; status: string }) {
  const router = useRouter();
  const me = useMe();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [purge, setPurge] = useState(true);
  const [unref, setUnref] = useState(false);
  const [op, setOp] = useState<Operation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  if (me !== undefined && me?.is_admin !== true) return null;

  async function preview() {
    setPending(true); setError(null);
    try {
      const r = await fetch("/api/operations/workload-delete/dry-run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workload_id: workloadId, confirm_name: typed, purge, destroy_unreferenced: unref }) });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Could not check that"); return; }
      setOp(d); notifyOperationsChanged();
    } finally { setPending(false); }
  }
  function close(deleted: boolean) { setOpen(false); setOp(null); setTyped(""); setError(null); if (deleted) router.push("/infrastructure/workloads"); else router.refresh(); }

  if (!open) return <button type="button" onClick={() => setOpen(true)} title="Delete this guest and its disks" className="px-2.5 py-1 rounded border border-bad/60 text-sm text-bad hover:bg-bad/10">Delete…</button>;
  return (
    <div className="basis-full w-full mt-2 border border-bad/50 rounded p-3 bg-bad/5 text-xs max-w-xl">
      <div className="font-medium text-bad mb-1">Delete {workloadName} for good</div>
      {status !== "stopped" && !op && <div className="mb-2 rounded border border-warn/50 bg-warn/10 text-warn p-2">This guest is {status}. Proxmox only deletes a stopped guest: shut it down first (the power buttons at the top of this page), then come back here.</div>}
      {op ? (
        <OpPreview op={op} danger confirmLabel="Confirm & DELETE" progressText="Deleting" doneText="Deleted: Proxmox no longer lists it." onFinished={() => notifyOperationsChanged()} onClose={() => close(op.status === "completed")} />
      ) : (
        <div className="space-y-2">
          <p className="text-muted">The guest and its disks are destroyed and cannot be recovered. Backups already taken are kept.</p>
          <div>
            <label className="block text-muted mb-1">Type its name ({workloadName}) to confirm</label>
            <input className="bg-surface border border-border rounded px-2 py-1.5 text-sm w-full" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
          </div>
          <label className="flex items-center gap-2"><input type="checkbox" checked={purge} onChange={(e) => setPurge(e.target.checked)} /> Also remove it from backup jobs, replication and HA configuration</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={unref} onChange={(e) => setUnref(e.target.checked)} /> Also destroy disks that carry its ID but are not in its configuration</label>
          {error && <div className="text-bad">{error}</div>}
          <div className="flex gap-2 items-center">
            <button type="button" disabled={pending || !typed || status !== "stopped"} onClick={preview} className="px-2 py-1 rounded bg-ink text-on-ink border border-bad hover:bg-bad/10 disabled:opacity-50">{pending ? "Checking…" : "Preview"}</button>
            <button type="button" onClick={() => close(false)} className="text-muted hover:text-text">cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}
