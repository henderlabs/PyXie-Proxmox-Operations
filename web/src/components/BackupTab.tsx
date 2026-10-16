"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import type { Operation } from "@/lib/api";
import { Card, CardTitle } from "@/components/Card";
import OpPreview from "@/components/OpPreview";
import { notifyOperationsChanged } from "@/lib/operationsBus";

type Info = { policy: { id: string; name: string; retention: string; schedule: string; storage: string; mode: string; source: string } | null; exempt: boolean; source: string | null;
  jobs: { id: string; storage: string | null; schedule: string | null; enabled: boolean; mode: string | null; retention: string | null; by_policy: boolean; covers: string; next_run: number | null }[];
  backups: { volid: string; storage: string; ctime: number | null; size: number | null; notes: string | null; verified: string | null; protected: boolean }[]; error: string | null; policies: { id: string; name: string }[]; cluster_id: string };
const GIB = 1073741824;
const input = "bg-surface border border-border rounded px-2 py-1.5 text-sm w-full";
const when = (s: number | null) => (s ? new Date(s * 1000).toLocaleString() : "—");
const size = (b: number | null) => (b && b >= 1048576 ? (b >= GIB ? `${(b / GIB).toFixed(2)} GiB` : `${Math.round(b / 1048576)} MiB`) : "—");

/** One guest's backup picture: which policy and jobs cover it, its restore points on the backup datastore, and the policy set for this VM. */
export default function BackupTab({ workloadId, workloadName, canEdit }: { workloadId: string; workloadName: string; canEdit: boolean }) {
  const [info, setInfo] = useState<Info | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pend, setPend] = useState<Operation | null>(null);
  const [pick, setPick] = useState("__inherit");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { fetch(`/api/backup-policies/workload/${workloadId}`).then(async (r) => { const d = await r.json(); if (!r.ok) throw new Error(d.error || "failed"); setInfo(d); }).catch((e) => setError(String(e.message || e))); }, [workloadId]);
  useEffect(() => { load(); }, [load]);

  async function preview() {
    if (!info) return;
    setBusy(true); setError(null);
    const change = pick === "__inherit" ? { action: "assign", clear: [{ scope_type: "vm", scope_value: workloadId }] } : { action: "assign", set: [{ scope_type: "vm", scope_value: workloadId, policy_id: pick === "__none" ? null : pick }] };
    try {
      const r = await fetch("/api/backup-policies/dry-run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cluster_id: info.cluster_id, change, leave_other: true }) });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Could not check that"); return; }
      setPend(d); notifyOperationsChanged();
    } catch { setError("Could not reach PyXie to check that. Try again."); } finally { setBusy(false); }
  }
  if (error && !info) return <Card><div className="text-sm text-bad">{error}</div></Card>;
  if (!info) return <Card><div className="text-sm text-muted">Reading the backup picture from Proxmox…</div></Card>;
  const newest = info.backups[0]?.ctime ?? null;
  const ageDays = newest ? (Date.now() / 1000 - newest) / 86400 : null;
  const covered = info.jobs.length > 0;
  return (
    <div className="space-y-4">
      {info.error && <div className="text-warn text-sm">{info.error}</div>}
      <Card>
        <CardTitle>Backup</CardTitle>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
          <div><div className="text-xs text-muted mb-1">Policy</div>
            {info.policy ? <><div className="text-text">{info.policy.name}</div><div className="text-xs text-muted">from {info.policy.source}</div><div className="text-xs text-muted mt-1">{info.policy.schedule} · {info.policy.mode} · to {info.policy.storage}</div><div className="text-xs text-muted">{info.policy.retention}</div></>
              : info.exempt ? <div className="text-warn">Marked as: no backup <span className="text-muted text-xs">({info.source})</span></div> : <div className="text-muted">No policy applies. <Link href="/operations/protection" className="text-accent hover:underline">Manage policies</Link></div>}</div>
          <div><div className="text-xs text-muted mb-1">Covered by</div>
            {info.jobs.length === 0 ? <div className={covered ? "" : "text-warn"}>No backup job includes this {"VM"}</div> : info.jobs.map((j) => <div key={j.id} className="text-xs"><span className="text-text">{j.id}</span> <span className="text-muted">{j.by_policy ? "(PyXie policy)" : `(other job, ${j.covers})`} · {j.schedule ?? "?"} · {j.storage}{j.enabled ? "" : " · off"}{j.next_run ? ` · next ${when(j.next_run)}` : ""}</span></div>)}
            {info.jobs.length > 1 && <div className="text-xs text-warn mt-1">More than one job backs this VM up.</div>}</div>
          <div><div className="text-xs text-muted mb-1">Last backup</div>
            {newest ? <><div className={ageDays !== null && ageDays > 2 && covered ? "text-warn" : "text-text"}>{when(newest)}</div><div className="text-xs text-muted">{ageDays !== null ? `${ageDays < 1 ? "less than a day" : `${Math.floor(ageDays)} day${Math.floor(ageDays) === 1 ? "" : "s"}`} ago` : ""} · {info.backups.length} restore point{info.backups.length === 1 ? "" : "s"}</div></>
              : <div className={covered ? "text-warn" : "text-muted"}>{covered ? "None found yet" : "None"}</div>}</div>
        </div>
      </Card>
      {canEdit && (
        <Card>
          <CardTitle>Policy for {workloadName}</CardTitle>
          {pend ? <OpPreview key={pend.id} op={pend} confirmLabel="Confirm" progressText="Changing the backup jobs" doneText="Done and read back from Proxmox." onFinished={() => notifyOperationsChanged()} onClose={() => { setPend(null); load(); }} /> : (
            <div className="space-y-2 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <select className={input + " max-w-xs"} value={pick} onChange={(e) => setPick(e.target.value)}><option value="__inherit">Follow tag / cluster default</option><option value="__none">No backup for this VM</option>{info.policies.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
                <button type="button" disabled={busy} onClick={preview} className="px-3 py-1.5 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50 text-xs">{busy ? "Checking…" : "Preview"}</button>
              </div>
              <p className="text-xs text-muted">This sets the policy for this VM only; it beats a tag or cluster rule. Create or edit policies under <Link href="/operations/protection" className="text-accent hover:underline">Protection</Link>.</p>
              {error && <div className="text-bad text-xs">{error}</div>}
            </div>
          )}
        </Card>
      )}
      <Card>
        <CardTitle>Restore points</CardTitle>
        {info.backups.length === 0 ? <div className="text-sm text-muted">No backups of this VM were found on its backup datastores.</div> : (
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-muted uppercase"><th className="py-1 pr-3">Taken</th><th className="pr-3">Datastore</th><th className="pr-3">Size</th><th className="pr-3">Verified</th><th>Notes</th></tr></thead>
            <tbody>{info.backups.map((b) => <tr key={b.volid} className="border-t border-border"><td className="py-1 pr-3 whitespace-nowrap">{when(b.ctime)}{b.protected ? <span className="text-xs text-muted ml-1">(protected)</span> : null}</td><td className="pr-3">{b.storage}</td><td className="pr-3 whitespace-nowrap">{size(b.size)}</td><td className="pr-3">{b.verified ?? "—"}</td><td className="text-xs text-muted">{b.notes ?? ""}</td></tr>)}</tbody>
          </table>
        )}
        <p className="text-xs text-muted mt-2">Restoring is done in Proxmox (VM &gt; Backup) or Proxmox Backup Server.</p>
      </Card>
    </div>
  );
}
