import { apiFetch } from "@/lib/api";
import type { BackupJob, Site } from "@/lib/api";
import { Card, CardTitle, PageHeader } from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import ActionButton from "@/components/ActionButton";
import PbsTargetForm from "@/components/PbsTargetForm";
import BackupJobMembership from "@/components/BackupJobMembership";
import { ShieldIcon } from "@/components/Icons";

type Copy = { store: string; offsite: boolean; backend: string; last: number | null; age_text: string; fresh: boolean };
type Row = { workload_id: string; vmid: number; name: string; type: string; status: "ok" | "partial" | "none"; copy_count: number; offsite: boolean; copies: Copy[]; problems: string[]; covered_by: string[] };
type Note = { level: string; text: string };
type Store = {
  name: string; backend: string; target: string; host: string; pbs_url: string; offsite: boolean; groups: number; snapshots: number; newest: number | null;
  prune: { id: string; schedule: string; retention: string; last_state: string | null }[];
  verify: { id: string; schedule: string; last_state: string | null }[];
  sync: { id: string; schedule: string; remote: string; last_state: string | null }[];
  notes: Note[];
};
type JobInfo = { cluster_id: string; cluster_name: string; job_id: string; storage: string; schedule: string; enabled: boolean; all_guests: boolean; member_count: number; scope_node: string | null; pve_url: string | null };
type Scorecard = { summary: { ok: number; partial: number; none: number; total: number }; rows: Row[]; stores: Store[]; jobs: JobInfo[]; errors: string[]; rules: { backup_max_age_h: number; offsite_max_age_h: number } };
type Target = { id: string; name: string; hostname: string; connection_health: string };

const when = (s: number | null) => (s ? new Date(s * 1000).toLocaleDateString() : "never");
const noteColor = (l: string) => (l === "bad" ? "text-bad" : "text-warn");
const statusText = { ok: "3-2-1 met", partial: "Partly protected", none: "Not protected" } as const;
const statusColor = { ok: "text-good", partial: "text-warn", none: "text-bad" } as const;

export default async function ProtectionPage() {
  const [card, targets, sites, backupJobs] = await Promise.all([
    apiFetch<Scorecard>("/api/protection/scorecard"),
    apiFetch<Target[]>("/api/protection/targets"),
    apiFetch<Site[]>("/api/sites"),
    apiFetch<BackupJob[]>("/api/protection/backup-jobs"),
  ]);
  const defaultSite = sites[0];
  const gaps = card.rows.filter((r) => r.status !== "ok");

  return (
    <div>
      <PageHeader title="Protection" subtitle="Does every guest have 3 copies, on 2 kinds of storage, with 1 offsite? Proxmox runs the jobs; PyXie scores the result." icon={<ShieldIcon className="w-5 h-5" />} />

      {card.errors.map((e) => <div key={e} className="text-warn text-sm mb-2">{e}</div>)}

      <div className="grid grid-cols-3 gap-4 mb-4">
        <Card><div className="text-xs text-muted mb-1">3-2-1 met</div><div className="text-2xl font-semibold text-good">{card.summary.ok}</div><div className="text-xs text-muted">of {card.summary.total} guests</div></Card>
        <Card><div className="text-xs text-muted mb-1">Partly protected</div><div className="text-2xl font-semibold text-warn">{card.summary.partial}</div><div className="text-xs text-muted">backed up, but a copy is missing or old</div></Card>
        <Card><div className="text-xs text-muted mb-1">Not protected</div><div className="text-2xl font-semibold text-bad">{card.summary.none}</div><div className="text-xs text-muted">no recent backup</div></Card>
      </div>

      <Card className="mb-4">
        <CardTitle>Where the copies live</CardTitle>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <div className="bg-surface2 rounded p-3 text-sm">
            <div className="text-text font-medium">Live guest</div>
            <div className="text-xs text-muted">Copy 1, on the cluster&apos;s own storage</div>
          </div>
          {card.stores.map((s) => (
            <div key={s.target + s.name} className="bg-surface2 rounded p-3 text-sm">
              <div className="flex justify-between items-start">
                <div>
                  <div className="text-text font-medium">{s.name}</div>
                  <div className="text-xs text-muted">{s.offsite ? "Offsite" : "On site"} · {s.backend === "s3" ? "S3 object storage" : "disk"} · {s.target}</div>
                </div>
                <a href={s.pbs_url} target="_blank" rel="noreferrer" className="text-xs text-proxmox hover:underline">Open in PBS</a>
              </div>
              <div className="text-xs text-muted mt-2">{s.groups} guests · {s.snapshots} backups · newest {when(s.newest)}</div>
              <div className="text-xs mt-2 space-y-0.5">
                <div><span className="text-muted">Keeps: </span>{s.prune.length ? s.prune.map((p) => `${p.retention} (${p.schedule})`).join("; ") : "no prune job"}</div>
                <div><span className="text-muted">Verify: </span>{s.verify.length ? s.verify.map((v) => `${v.schedule}${v.last_state ? ", last " + v.last_state : ""}`).join("; ") : "no verify job"}</div>
                {s.offsite && <div><span className="text-muted">Sync: </span>{s.sync.length ? s.sync.map((j) => `from ${j.remote} (${j.schedule})`).join("; ") : "no sync job"}</div>}
              </div>
              {s.notes.map((n) => <div key={n.text} className={`text-xs mt-1 ${noteColor(n.level)}`}>{n.text}</div>)}
            </div>
          ))}
        </div>
        <div className="text-xs text-muted mt-3">A datastore counts as offsite when it is S3-backed or its name says offsite. A copy counts when it is under {card.rules.backup_max_age_h} hours old ({Math.round(card.rules.offsite_max_age_h / 24)} days for offsite). Schedules and retention are set in Proxmox and PBS; PyXie shows them and does not change them.</div>
      </Card>

      <Card className="mb-4">
        <CardTitle>Every guest, gaps first</CardTitle>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted">
              <th className="py-1 pr-3">Guest</th><th className="pr-3">Result</th>
              {card.stores.map((s) => <th key={s.name} className="pr-3">{s.name}</th>)}
              <th className="pr-3">Job</th><th>What is missing</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {card.rows.map((r) => (
              <tr key={r.workload_id}>
                <td className="py-1.5 pr-3"><a href={`/infrastructure/workloads/${r.workload_id}`} className="text-text hover:underline">{r.name}</a> <span className="text-xs text-muted">{r.vmid}</span></td>
                <td className={`pr-3 ${statusColor[r.status]}`}>{statusText[r.status]}</td>
                {card.stores.map((s) => {
                  const c = r.copies.find((x) => x.store === s.name);
                  return <td key={s.name} className={`pr-3 ${c?.fresh ? "text-text" : c?.last ? "text-warn" : "text-muted"}`}>{c?.age_text ?? "never"}</td>;
                })}
                <td className="pr-3 text-xs">{r.covered_by.length ? r.covered_by.join(", ") : <span className="text-warn">none</span>}</td>
                <td className="text-xs text-muted">{r.problems.join("; ") || "Nothing"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {gaps.length === 0 && <div className="text-sm text-good mt-2">Every guest meets 3-2-1.</div>}
      </Card>

      <Card className="mb-4">
        <CardTitle>Backup jobs (set in Proxmox)</CardTitle>
        <div className="divide-y divide-border text-sm">
          {card.jobs.map((j) => (
            <div key={j.cluster_id + j.job_id} className="flex items-center justify-between py-2">
              <div>
                <span className="text-text">{j.job_id}</span> <span className="text-xs text-muted">{j.cluster_name} · {j.schedule} · to {j.storage} · {j.all_guests ? "all guests" : "chosen guests"} · {j.member_count} guests{j.enabled ? "" : " · disabled"}</span>
              </div>
              {j.pve_url && <a href={j.pve_url} target="_blank" rel="noreferrer" className="text-xs text-proxmox hover:underline">Open in Proxmox</a>}
            </div>
          ))}
          {card.jobs.length === 0 && <div className="text-muted py-2">No PBS-backed backup jobs found.</div>}
        </div>
      </Card>

      <Card className="mb-4">
        <CardTitle>Which guests a job backs up</CardTitle>
        <div className="text-xs text-muted mb-3">The one thing PyXie edits: add or remove a guest from a job, or switch it to all guests. Everything else about a job is in Proxmox.</div>
        <BackupJobMembership jobs={backupJobs} />
      </Card>

      <Card>
        <CardTitle>Backup servers</CardTitle>
        <div className="space-y-3">
          {targets.map((t) => (
            <div key={t.id} className="flex items-center justify-between bg-surface2 rounded p-3">
              <div><div className="text-sm text-text">{t.name}</div><div className="text-xs text-muted">{t.hostname}</div></div>
              <div className="flex items-center gap-3"><StatusBadge status={t.connection_health} /><ActionButton href={`/api/protection/targets/${t.id}/sync`} label="Sync Now" variant="primary" /></div>
            </div>
          ))}
          {defaultSite && <PbsTargetForm siteId={defaultSite.id} />}
        </div>
      </Card>
    </div>
  );
}
