"use client";

import { useCallback, useEffect, useMemo, useState, useRef } from "react";
import type { Operation } from "@/lib/api";
import { Card, CardTitle } from "@/components/Card";
import OpPreview from "@/components/OpPreview";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";

type Policy = { id: string; name: string; storage: string; schedule: string; mode: string; enabled: boolean; notes_template: string | null; job_id: string; retention: string; members: number[];
  keep_last: number | null; keep_hourly: number | null; keep_daily: number | null; keep_weekly: number | null; keep_monthly: number | null; keep_yearly: number | null; job: { id: string; enabled: boolean } | null };
type Assignment = { id: string | null; policy_id: string | null; scope_type: "cluster" | "tag" | "vm"; scope_value: string | null; priority: number; policy_name: string };
type Guest = { id: string; vmid: number; name: string; tags: string[]; type: string; policy_id: string | null; policy_name: string | null; source: string | null; other_jobs: string[] };
type Overview = { policies: Policy[]; assignments: Assignment[]; guests: Guest[]; other_jobs: { id: string; storage: string | null; schedule: string | null; enabled: boolean; covers: string }[]; storages: { name: string; type: string | null }[]; tags: string[]; drift: { action: string; job_id: string; why: string }[] };
const input = "bg-surface border border-border rounded px-2 py-1.5 text-sm w-full";
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const MODES = [
  { v: "snapshot", l: "Snapshot (recommended)", h: "Backs a running VM up without stopping it. Needs nothing special in the guest; the guest agent makes the copy consistent." },
  { v: "suspend", l: "Suspend", h: "Pauses the VM briefly while its memory is copied. Rarely needed." },
  { v: "stop", l: "Stop", h: "Shuts the VM down for the backup and starts it again. Safest copy, but the VM is down meanwhile." },
];
const PRESETS: { label: string; keep: Partial<Record<string, number>> }[] = [
  { label: "Light: 7 daily", keep: { keep_daily: 7 } },
  { label: "Standard: 7 daily, 4 weekly, 6 monthly", keep: { keep_daily: 7, keep_weekly: 4, keep_monthly: 6 } },
  { label: "Long: 14 daily, 8 weekly, 12 monthly, 3 yearly", keep: { keep_daily: 14, keep_weekly: 8, keep_monthly: 12, keep_yearly: 3 } },
];
const KEEPS: [string, string, string][] = [["keep_last", "Last", "The newest N backups, whatever their age."], ["keep_hourly", "Hourly", "One per hour for N hours."], ["keep_daily", "Daily", "One per day for N days."], ["keep_weekly", "Weekly", "One per week for N weeks."], ["keep_monthly", "Monthly", "One per month for N months."], ["keep_yearly", "Yearly", "One per year for N years."]];

function scheduleOf(freq: string, time: string, day: string, dom: string, custom: string): string {
  if (freq === "daily") return time;
  if (freq === "weekly") return `${day} ${time}`;
  if (freq === "monthly") return `*-*-${dom.padStart(2, "0")} ${time}`;
  return custom.trim();
}
function readSchedule(s: string): { freq: string; time: string; day: string; dom: string; custom: string } {
  const d = { freq: "custom", time: "02:00", day: "sat", dom: "1", custom: s };
  let m = s.match(/^(\d{1,2}:\d{2})$/); if (m) return { ...d, freq: "daily", time: m[1].padStart(5, "0") };
  m = s.match(/^(mon|tue|wed|thu|fri|sat|sun) (\d{1,2}:\d{2})$/); if (m) return { ...d, freq: "weekly", day: m[1], time: m[2].padStart(5, "0") };
  m = s.match(/^\*-\*-(\d{1,2}) (\d{1,2}:\d{2})$/); if (m) return { ...d, freq: "monthly", dom: String(parseInt(m[1], 10)), time: m[2].padStart(5, "0") };
  return d;
}

/** Backup policies: named bundles (datastore, schedule, mode, retention) that become Proxmox backup jobs, and who they apply to (the cluster, a tag, or one VM). */
export default function BackupPolicies() {
  const me = useMe();
  const admin = me === undefined || me?.is_admin === true;
  const [clusters, setClusters] = useState<{ id: string; name: string }[]>([]);
  const [clusterId, setClusterId] = useState("");
  const curCluster = useRef(""); // the cluster now chosen: an answer that arrives for another one is ignored
  curCluster.current = clusterId;
  const [data, setData] = useState<Overview | null>(null);
  const [editing, setEditing] = useState<Policy | "new" | null>(null);
  const [pend, setPend] = useState<{ op: Operation; label: string } | null>(null);
  const [leaveOther, setLeaveOther] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tagPick, setTagPick] = useState({ tag: "", policy: "" });

  useEffect(() => { fetch("/api/defaults").then((r) => r.json()).then((d) => { setClusters(d.clusters); if (d.clusters[0]) setClusterId(d.clusters[0].id); }).catch(() => setError("Could not load clusters")); }, []);
  const load = useCallback(async () => {
    if (!clusterId) return;
    const mine = clusterId;
    const r = await fetch(`/api/backup-policies?cluster_id=${clusterId}`);
    const d = await r.json();
    if (curCluster.current !== mine) return;
    if (!r.ok) { setError(d.error || "Could not read the backup policies"); return; }
    setData(d);
  }, [clusterId]);
  useEffect(() => { load(); }, [load]);

  async function preview(change: object, label: string) {
    setBusy(true); setError(null);
    try {
      const r = await fetch("/api/backup-policies/dry-run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cluster_id: clusterId, change, leave_other: leaveOther }) });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Could not check that"); return; }
      setPend({ op: d, label }); setEditing(null); notifyOperationsChanged();
    } catch { setError("Could not reach PyXie to check that. Try again."); } finally { setBusy(false); }
  }
  const polOpts = data?.policies ?? [];
  const clusterRule = data?.assignments.find((a) => a.scope_type === "cluster");
  const tagRules = data?.assignments.filter((a) => a.scope_type === "tag") ?? [];
  const setCluster = (policyId: string) => preview(policyId ? { action: "assign", set: [{ scope_type: "cluster", scope_value: null, policy_id: policyId }] } : { action: "assign", clear: [{ scope_type: "cluster" }] }, "Change the cluster default policy");
  const vmRule = (g: Guest) => data?.assignments.find((a) => a.scope_type === "vm" && a.scope_value === g.id);
  const setVm = (g: Guest, v: string) => preview(v === "__inherit" ? { action: "assign", clear: [{ scope_type: "vm", scope_value: g.id }] } : { action: "assign", set: [{ scope_type: "vm", scope_value: g.id, policy_id: v === "__none" ? null : v }] }, `Backup policy for ${g.name}`);

  if (!admin && me !== undefined) return <Card><CardTitle>Backup policies</CardTitle><div className="text-sm text-muted">Only administrators can manage backup policies.</div></Card>;
  return (
    <div className="space-y-4 mb-4">
      {clusters.length > 1 && <select className={input + " max-w-xs"} value={clusterId} onChange={(e) => { setClusterId(e.target.value); setData(null); }}>{clusters.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>}
      {error && <div className="text-bad text-sm">{error}</div>}
      {pend && (
        <Card>
          <CardTitle>{pend.label}</CardTitle>
          <OpPreview key={pend.op.id} op={pend.op} confirmLabel="Confirm" progressText="Changing the backup jobs" doneText="Done and read back from Proxmox." onFinished={() => notifyOperationsChanged()} onClose={() => { setPend(null); load(); }} />
        </Card>
      )}
      <Card>
        <div className="flex items-center justify-between mb-2">
          <CardTitle>Backup policies</CardTitle>
          {!editing && !pend && <button type="button" onClick={() => setEditing("new")} className="px-2 py-1 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 text-xs">New policy</button>}
        </div>
        <p className="text-xs text-muted mb-3">A policy is a named bundle: where backups go, when they run, how, and how long they are kept. PyXie turns each policy into one Proxmox backup job (named pyxie-…) and keeps the job&apos;s VM list in step with who the policy applies to: the whole cluster, every VM with a tag, or one VM. The most specific one wins (VM, then tag, then cluster).</p>
        {!data ? <div className="text-sm text-muted">Reading the backup jobs from Proxmox…</div> : (<>
          {data.drift.length > 0 && !pend && !editing && (
            <div className="mb-3 rounded border border-warn/50 bg-warn/10 p-2 text-xs text-warn flex flex-wrap items-center gap-3">
              <span>The Proxmox jobs differ from the policies ({data.drift.map((d) => `${d.action} ${d.job_id}`).join(", ")}): for example a VM was tagged or created since the last sync.</span>
              <button type="button" disabled={busy} onClick={() => preview({ action: "sync" }, "Sync the backup jobs with the policies")} className="px-2 py-1 rounded border border-warn/60 hover:bg-warn/10">Preview sync</button>
            </div>
          )}
          {editing ? <PolicyForm key={editing === "new" ? "new" : editing.id} existing={editing === "new" ? null : editing} stores={data.storages} busy={busy} onCancel={() => setEditing(null)} onPreview={(p) => preview({ action: "save", policy: p }, p.id ? `Update policy ${p.name}` : `New policy ${p.name}`)} /> : (
            data.policies.length === 0 ? <div className="text-sm text-muted">No policies yet. Create one, then assign it to the cluster, a tag or a VM.</div> : (
              <table className="w-full text-sm">
                <thead><tr className="text-left text-xs text-muted uppercase"><th className="py-1 pr-3">Policy</th><th className="pr-3">Datastore</th><th className="pr-3">When</th><th className="pr-3">Keeps</th><th className="pr-3">VMs</th><th /></tr></thead>
                <tbody>
                  {data.policies.map((p) => (
                    <tr key={p.id} className="border-t border-border align-top">
                      <td className="py-1.5 pr-3 text-text">{p.name}{!p.enabled && <span className="text-warn text-xs ml-1">(off)</span>}<div className="text-xs text-muted">{p.job ? `job ${p.job.id}` : "no job yet: it is created when a VM uses it"}</div></td>
                      <td className="pr-3">{p.storage}</td>
                      <td className="pr-3 whitespace-nowrap">{p.schedule}<div className="text-xs text-muted">{p.mode}</div></td>
                      <td className="pr-3 text-xs text-muted">{p.retention}</td>
                      <td className="pr-3">{p.members.length}</td>
                      <td className="text-right whitespace-nowrap text-xs">{!pend && <><button type="button" onClick={() => setEditing(p)} className="text-accent hover:underline mr-3">Edit</button><button type="button" onClick={() => preview({ action: "delete", policy_id: p.id }, `Delete policy ${p.name}`)} className="text-bad hover:underline">Delete</button></>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          )}
        </>)}
      </Card>
      {data && data.policies.length > 0 && (
        <Card>
          <CardTitle>Who gets which policy</CardTitle>
          <div className="space-y-4 text-sm">
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-xs text-muted w-40">Every VM in this cluster</span>
              <select className={input + " max-w-xs"} value={clusterRule?.policy_id ?? ""} disabled={busy || !!pend} onChange={(e) => setCluster(e.target.value)}><option value="">No default</option>{polOpts.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
            </div>
            <div>
              <div className="text-xs text-muted mb-1">By tag (a tag beats the cluster default; between tags the higher priority wins)</div>
              {tagRules.map((a) => (
                <div key={a.scope_value} className="flex flex-wrap items-center gap-3 mb-1">
                  <span className="text-text w-40">VMs tagged <b>{a.scope_value}</b></span>
                  <span className="text-muted">{a.policy_name || "no backup"}</span>
                  <button type="button" disabled={busy || !!pend} onClick={() => preview({ action: "assign", clear: [{ scope_type: "tag", scope_value: a.scope_value }] }, `Remove the rule for tag ${a.scope_value}`)} className="text-xs text-bad hover:underline">Remove</button>
                </div>
              ))}
              <div className="flex flex-wrap items-center gap-2 mt-1">
                <select className={input + " max-w-[10rem]"} value={tagPick.tag} onChange={(e) => setTagPick({ ...tagPick, tag: e.target.value })}><option value="">Tag…</option>{data.tags.filter((t) => !tagRules.some((a) => a.scope_value === t)).map((t) => <option key={t}>{t}</option>)}</select>
                <select className={input + " max-w-[12rem]"} value={tagPick.policy} onChange={(e) => setTagPick({ ...tagPick, policy: e.target.value })}><option value="">Policy…</option><option value="__none">No backup</option>{polOpts.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
                <button type="button" disabled={busy || !!pend || !tagPick.tag || !tagPick.policy} onClick={() => preview({ action: "assign", set: [{ scope_type: "tag", scope_value: tagPick.tag, policy_id: tagPick.policy === "__none" ? null : tagPick.policy, priority: 0 }] }, `Policy for tag ${tagPick.tag}`)} className="px-2 py-1 rounded border border-border text-xs hover:bg-surface2 disabled:opacity-50">Add rule</button>
              </div>
            </div>
            <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={leaveOther} onChange={(e) => setLeaveOther(e.target.checked)} /> Take VMs out of other backup jobs that already cover them (so they are not backed up twice). The preview lists each job.</label>
          </div>
        </Card>
      )}
      {data && (
        <Card>
          <CardTitle>Every VM</CardTitle>
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-muted uppercase"><th className="py-1 pr-3">VM</th><th className="pr-3">Tags</th><th className="pr-3">Backed up by</th><th className="pr-3">Set for this VM</th></tr></thead>
            <tbody>
              {data.guests.map((g) => {
                const r = vmRule(g);
                return (
                  <tr key={g.id} className="border-t border-border align-top">
                    <td className="py-1.5 pr-3 text-text">{g.name} <span className="text-muted text-xs">{g.type === "lxc" ? "CT" : "VM"} {g.vmid}</span></td>
                    <td className="pr-3 text-xs text-muted">{g.tags.join(", ") || "—"}</td>
                    <td className="pr-3 text-xs">{g.policy_name ? <span className="text-text">{g.policy_name}</span> : g.source ? <span className="text-warn">no backup</span> : <span className="text-muted">no policy</span>}{g.source ? <span className="text-muted"> · {g.source}</span> : null}
                      {g.other_jobs.length > 0 && <div className={g.policy_name ? "text-warn" : "text-muted"}>{g.policy_name ? "also in " : "in "}job{g.other_jobs.length > 1 ? "s" : ""} {g.other_jobs.join(", ")}</div>}</td>
                    <td className="pr-3"><select className={input + " max-w-[13rem]"} disabled={busy || !!pend} value={r ? (r.policy_id ?? "__none") : "__inherit"} onChange={(e) => setVm(g, e.target.value)}><option value="__inherit">Follow tag / cluster</option><option value="__none">No backup</option>{polOpts.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {data.other_jobs.length > 0 && <p className="text-xs text-muted mt-3">Other backup jobs in Proxmox (not managed by policies): {data.other_jobs.map((j) => `${j.id} (${j.covers}, ${j.schedule ?? "?"}, ${j.storage ?? "?"}${j.enabled ? "" : ", off"})`).join("; ")}.</p>}
        </Card>
      )}
    </div>
  );
}

function PolicyForm({ existing, stores, busy, onCancel, onPreview }: { existing: Policy | null; stores: { name: string; type: string | null }[]; busy: boolean; onCancel: () => void; onPreview: (p: Record<string, unknown>) => void }) {
  const init = readSchedule(existing?.schedule ?? "02:00");
  const [name, setName] = useState(existing?.name ?? "");
  const [storage, setStorage] = useState(existing?.storage ?? stores.find((s) => s.type === "pbs")?.name ?? stores[0]?.name ?? "");
  const [freq, setFreq] = useState(init.freq);
  const [time, setTime] = useState(init.time);
  const [day, setDay] = useState(init.day);
  const [dom, setDom] = useState(init.dom);
  const [custom, setCustom] = useState(init.custom);
  const [mode, setMode] = useState(existing?.mode ?? "snapshot");
  const [enabled, setEnabled] = useState(existing?.enabled ?? true);
  const [notes, setNotes] = useState(existing?.notes_template ?? "");
  const [keep, setKeep] = useState<Record<string, string>>(() => Object.fromEntries(KEEPS.map(([k]) => [k, String((existing as unknown as Record<string, number | null>)?.[k] ?? "")])));
  const schedule = useMemo(() => scheduleOf(freq, time, day, dom, custom), [freq, time, day, dom, custom]);
  const none = KEEPS.every(([k]) => !keep[k]);
  const ready = name.trim() && storage && schedule;
  const policy = () => ({ ...(existing ? { id: existing.id } : {}), name: name.trim(), storage, schedule, mode, enabled, notes_template: notes.trim() || null, ...Object.fromEntries(KEEPS.map(([k]) => [k, keep[k] ? parseInt(keep[k], 10) : null])) });
  return (
    <div className="space-y-3 text-sm">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div><label className="block text-xs text-muted mb-1">Policy name</label><input className={input} value={name} onChange={(e) => setName(e.target.value)} placeholder="Gold" /></div>
        <div><label className="block text-xs text-muted mb-1">Backups go to</label>
          <select className={input} value={storage} onChange={(e) => setStorage(e.target.value)}>{stores.map((s) => <option key={s.name} value={s.name}>{s.name}{s.type === "pbs" ? " (Proxmox Backup Server)" : ` (${s.type})`}</option>)}</select>
          <div className="text-xs text-muted mt-1">Datastores that hold backups.</div></div>
        <div><label className="block text-xs text-muted mb-1">Mode</label>
          <select className={input} value={mode} onChange={(e) => setMode(e.target.value)}>{MODES.map((m) => <option key={m.v} value={m.v}>{m.l}</option>)}</select>
          <div className="text-xs text-muted mt-1">{MODES.find((m) => m.v === mode)?.h}</div></div>
      </div>
      <div>
        <div className="text-xs text-muted mb-1">When</div>
        <div className="flex flex-wrap items-center gap-2">
          <select className={input + " max-w-[9rem]"} value={freq} onChange={(e) => setFreq(e.target.value)}><option value="daily">Every day</option><option value="weekly">Every week</option><option value="monthly">Every month</option><option value="custom">Custom…</option></select>
          {freq === "weekly" && <select className={input + " max-w-[6rem]"} value={day} onChange={(e) => setDay(e.target.value)}>{DAYS.map((d) => <option key={d}>{d}</option>)}</select>}
          {freq === "monthly" && <><span className="text-muted">on day</span><input type="number" min={1} max={31} className={input + " max-w-[5rem]"} value={dom} onChange={(e) => setDom(e.target.value)} /></>}
          {freq !== "custom" ? <><span className="text-muted">at</span><input type="time" className={input + " max-w-[8rem]"} value={time} onChange={(e) => setTime(e.target.value)} /></> : <input className={input + " max-w-xs"} value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="mon..fri 22:30" />}
          <span className="text-xs text-muted">Proxmox calendar: <code>{schedule || "—"}</code></span>
        </div>
      </div>
      <div>
        <div className="text-xs text-muted mb-1">How long to keep backups (pruned after each backup run)</div>
        <div className="flex flex-wrap gap-2 mb-2">{PRESETS.map((p) => <button type="button" key={p.label} onClick={() => setKeep(Object.fromEntries(KEEPS.map(([k]) => [k, String(p.keep[k] ?? "")])))} className="px-2 py-0.5 rounded border border-border text-xs text-muted hover:text-text hover:bg-surface2">{p.label}</button>)}</div>
        <div className="grid grid-cols-3 md:grid-cols-6 gap-2">{KEEPS.map(([k, l, h]) => <div key={k}><label className="block text-xs text-muted mb-0.5" title={h}>{l}</label><input type="number" min={1} className={input} value={keep[k]} onChange={(e) => setKeep({ ...keep, [k]: e.target.value })} title={h} /></div>)}</div>
        <div className="text-xs text-muted mt-1">{none ? "Nothing set: every backup is kept forever and the datastore fills up. Set at least one." : "Keeps the newest backups that satisfy any of these. Leave a box empty to not use it."}</div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div><label className="block text-xs text-muted mb-1">Notes on each backup (optional)</label><input className={input} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="{{guestname}}" />
          <div className="text-xs text-muted mt-1">Proxmox fills in {"{{guestname}}"}, {"{{node}}"}, {"{{vmid}}"}, {"{{cluster}}"}.</div></div>
        <label className="flex items-center gap-2 text-xs pt-5"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Enabled (a switched-off policy keeps its job, but Proxmox does not run it)</label>
      </div>
      <div className="flex gap-2 items-center">
        <button type="button" disabled={busy || !ready} onClick={() => onPreview(policy())} className="px-3 py-1.5 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50 text-xs">{busy ? "Checking…" : "Preview"}</button>
        <button type="button" onClick={onCancel} className="text-xs text-muted hover:text-text">cancel</button>
      </div>
    </div>
  );
}
