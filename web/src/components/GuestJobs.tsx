"use client";

import { useState } from "react";
import type { BackupJob, Operation } from "@/lib/api";
import OpPreview from "@/components/OpPreview";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";

const btn = "px-2.5 py-1 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50 text-xs";

/** Which Proxmox backup jobs back this guest up, with the one thing PyXie edits: include or exclude the guest from a job. */
export default function GuestJobs({ workloadId, vmid, jobs }: { workloadId: string; vmid: number; jobs: BackupJob[] }) {
  const me = useMe();
  const canEdit = me === undefined || me?.is_admin === true;
  const [pend, setPend] = useState<{ op: Operation; label: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const rows = jobs.map((j) => ({ job: j, member: j.members.find((m) => m.workload_id === workloadId) })).filter((r) => r.member);
  async function toggle(job: BackupJob, included: boolean) {
    setBusy(true); setErr(null);
    try {
      const r = await fetch("/api/protection/backup-jobs/dry-run", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cluster_id: job.cluster_id, job_id: job.job_id, add_vmids: included ? [] : [vmid], remove_vmids: included ? [vmid] : [] }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || "Preview failed");
      setPend({ op: d, label: included ? `Exclude from ${job.job_id}` : `Include in ${job.job_id}` }); notifyOperationsChanged();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  if (rows.length === 0) return <div className="text-xs text-warn">No PBS-backed Proxmox backup job lists this guest.</div>;
  if (pend) return <OpPreview key={pend.op.id} op={pend.op} confirmLabel={pend.label} progressText="Changing the backup job" doneText="Done and read back from Proxmox." onFinished={() => notifyOperationsChanged()} onClose={() => { setPend(null); window.location.reload(); }} />;
  return (
    <div className="text-xs space-y-1">
      <div className="text-muted">Backup jobs (schedule and retention are set in Proxmox):</div>
      {rows.map(({ job, member }) => (
        <div key={job.job_id + job.cluster_id} className="flex items-center gap-3">
          <span className="text-text">{job.job_id}</span>
          <span className="text-muted">{job.schedule} · to {job.storage} · {job.all_guests ? "all guests" : "chosen guests"}</span>
          <span className={member!.included ? "text-good" : "text-warn"}>{member!.included ? "backs this guest up" : "does not back this guest up"}</span>
          {canEdit && <button className={btn} disabled={busy} onClick={() => toggle(job, !!member!.included)}>{member!.included ? "Exclude" : "Include"}</button>}
        </div>
      ))}
      {err && <div className="text-bad">{err}</div>}
    </div>
  );
}
