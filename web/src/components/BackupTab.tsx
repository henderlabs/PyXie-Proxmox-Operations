"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, CardTitle } from "@/components/Card";

type Info = { policy: { id: string; name: string; retention: string; schedule: string; storage: string; mode: string; source: string } | null; exempt: boolean; source: string | null;
  jobs: { id: string; storage: string | null; schedule: string | null; enabled: boolean; mode: string | null; retention: string | null; by_policy: boolean; covers: string; next_run: number | null }[];
  backups: { volid: string; storage: string; ctime: number | null; size: number | null; notes: string | null; verified: string | null; protected: boolean }[]; error: string | null; disks?: { slot: string; size_bytes: number | null; backup: boolean | null; accepted: boolean }[]; policies: { id: string; name: string }[]; cluster_id: string };
const GIB = 1073741824;
const when = (s: number | null) => (s ? new Date(s * 1000).toLocaleString() : "—");
const size = (b: number | null) => (b && b >= 1048576 ? (b >= GIB ? `${(b / GIB).toFixed(2)} GiB` : `${Math.round(b / 1048576)} MiB`) : "—");

/** One guest's backup picture: which policy and jobs cover it, its restore points on the backup datastore, and the policy set for this VM. */
export default function BackupTab({ workloadId, workloadName, canEdit, onOpenHardware }: { workloadId: string; workloadName: string; canEdit: boolean; onOpenHardware?: () => void }) {
  const [info, setInfo] = useState<Info | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => { fetch(`/api/backup-policies/workload/${workloadId}`).then(async (r) => { const d = await r.json(); if (!r.ok) throw new Error(d.error || "failed"); setInfo(d); }).catch((e) => setError(String(e.message || e))); }, [workloadId]);
  useEffect(() => { load(); }, [load]);

  if (error && !info) return <Card><div className="text-sm text-bad">{error}</div></Card>;
  if (!info) return <Card><div className="text-sm text-muted">Reading the backup picture from Proxmox…</div></Card>;
  async function setOnPurpose(slot: string, on: boolean) {
    const r = on ? await fetch("/api/protection/disk-exceptions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workload_id: workloadId, slot }) })
      : await fetch(`/api/protection/disk-exceptions/${workloadId}/${slot}`, { method: "DELETE" });
    if (r.ok) load();
  }
  const noPlan = !info.policy;
  const newest = info.backups[0]?.ctime ?? null;
  const ageDays = newest ? (Date.now() / 1000 - newest) / 86400 : null;
  const covered = info.jobs.length > 0;
  return (
    <div className="space-y-4">
      {info.error && <div className="text-warn text-sm">{info.error}</div>}
      {(info.disks ?? []).length > 0 && (
        <Card>
          <CardTitle>Disks in backups</CardTitle>
          <div className="text-xs text-muted mb-2">{noPlan ? "This guest has no backup plan, so none of its disks are backed up. " : "A disk marked Left out is skipped by every backup of this guest. "}A powered-off guest is never started for a backup.</div>
          <table className="w-full text-sm">
            <tbody>
              {info.disks!.map((d) => (
                <tr key={d.slot} className="border-t border-border">
                  <td className="py-1.5 pr-3">{d.slot}</td>
                  <td className="pr-3 text-muted">{d.size_bytes ? `${(d.size_bytes / GIB).toFixed(d.size_bytes >= GIB ? 0 : 2)} GiB` : "—"}</td>
                  <td className="pr-3">{noPlan ? <span className="text-muted">Not backed up (no plan)</span> : d.backup === false ? <span className={d.accepted ? "text-muted" : "text-warn"}>{d.accepted ? "Left out on purpose" : "Left out"}</span> : <span className="text-good">Backed up</span>}</td>
                  <td className="text-right text-xs whitespace-nowrap">
                    {canEdit && d.backup === false && <button type="button" onClick={() => setOnPurpose(d.slot, !d.accepted)} className="text-accent hover:underline mr-3">{d.accepted ? "Not on purpose" : "Mark on purpose"}</button>}
                    {canEdit && onOpenHardware && <button type="button" onClick={onOpenHardware} className="text-accent hover:underline">Change in Hardware</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      <Card>
        <CardTitle>Backup</CardTitle>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
          <div><div className="text-xs text-muted mb-1">Covered by</div>
            {info.jobs.length === 0 ? <div className={covered ? "" : "text-warn"}>No backup job includes this {"VM"}</div> : info.jobs.map((j) => <div key={j.id} className="text-xs"><span className="text-text">{j.id}</span> <span className="text-muted">{j.by_policy ? "(PyXie policy)" : `(other job, ${j.covers})`} · {j.schedule ?? "?"} · {j.storage}{j.enabled ? "" : " · off"}{j.next_run ? ` · next ${when(j.next_run)}` : ""}</span></div>)}
            {info.jobs.length > 1 && <div className="text-xs text-warn mt-1">More than one job backs this VM up.</div>}</div>
          <div><div className="text-xs text-muted mb-1">Last backup</div>
            {newest ? <><div className={ageDays !== null && ageDays > 2 && covered ? "text-warn" : "text-text"}>{when(newest)}</div><div className="text-xs text-muted">{ageDays !== null ? `${ageDays < 1 ? "less than a day" : `${Math.floor(ageDays)} day${Math.floor(ageDays) === 1 ? "" : "s"}`} ago` : ""} · {info.backups.length} restore point{info.backups.length === 1 ? "" : "s"}</div></>
              : <div className={covered ? "text-warn" : "text-muted"}>{covered ? "None found yet" : "None"}</div>}</div>
        </div>
      </Card>
      <Card>
        <CardTitle>Restore points</CardTitle>
        {info.backups.length === 0 ? <div className="text-sm text-muted">No backups of this VM were found on its backup datastores.</div> : (
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-muted uppercase"><th className="py-1 pr-3">Taken</th><th className="pr-3">Datastore</th><th className="pr-3">Size</th><th className="pr-3">Verified</th><th>Notes</th></tr></thead>
            <tbody>{info.backups.map((b) => <tr key={b.volid} className="border-t border-border"><td className="py-1 pr-3 whitespace-nowrap">{when(b.ctime)}{b.protected ? <span className="text-xs text-muted ml-1">(protected)</span> : null}</td><td className="pr-3">{b.storage}</td><td className="pr-3 whitespace-nowrap">{size(b.size)}</td><td className="pr-3">{b.verified ?? "—"}</td><td className="text-xs text-muted">{b.notes ?? ""}</td></tr>)}</tbody>
          </table>
        )}
        <p className="text-xs text-muted mt-2">To restore, open this guest on the <a href={`/operations/protection?guest=${encodeURIComponent(workloadName)}`} className="text-accent hover:underline">Protection page</a>: every restore point is listed there with a Restore button (as a new guest or over this one).</p>
      </Card>
    </div>
  );
}
