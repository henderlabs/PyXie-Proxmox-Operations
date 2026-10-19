"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Table } from "@/components/Table";
import RestorePoints from "@/components/RestorePoints";
import GuestPlan from "@/components/GuestPlan";
import OpPreview from "@/components/OpPreview";
import { Card, CardTitle } from "@/components/Card";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";
import type { BackupJob, Operation } from "@/lib/api";

type Copy = { store: string; offsite: boolean; last: number | null; age_text: string; fresh: boolean };
type Plan = { policy_id: string | null; name: string | null; source: string | null; exempt: boolean; cluster_id: string };
export type GuestRow = {
  id?: string; workload_id: string; vmid: number; name: string; type: string; status: "ok" | "partial" | "none"; copies: Copy[]; problems: string[]; covered_by: string[];
  plan?: Plan; notes?: string[]; disk_coverage?: { total: number; included: number; excluded: { slot: string; size_bytes: number | null; accepted: boolean; last_backup: number | null }[] };
  causes?: { level: "warn" | "info"; text: string }[]; state?: string | null;
};
export type PlansByCluster = Record<string, { id: string; name: string; is_default: boolean }[]>;

const statusText = { ok: "3-2-1 copies met", partial: "Partly protected", none: "Not protected" } as const;
const statusColor = { ok: "text-good", partial: "text-warn", none: "text-bad" } as const;
const order = { none: 0, partial: 1, ok: 2 } as const;
const sel = "bg-surface border border-border rounded px-2 py-1 text-xs max-w-[15rem]";

/** Every guest with its plan (a drop-down on the row), its 3-2-1 result and what is wrong and why; open a row for the plan in detail and every restore point. */
export default function ProtectionGuests({ rows, stores, plans }: { rows: GuestRow[]; stores: string[]; jobs?: BackupJob[]; plans: PlansByCluster }) {
  const me = useMe();
  const canEdit = me === undefined || me?.is_admin === true;
  const [q, setQ] = useState("");
  useEffect(() => { const g = new URLSearchParams(window.location.search).get("guest"); if (g) setQ(g); }, []);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pend, setPend] = useState<{ op: Operation; name: string } | null>(null);
  async function setLeftOut(workloadId: string, slot: string, on: boolean) {
    setErr(null);
    try {
      const r = on ? await fetch("/api/protection/disk-exceptions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workload_id: workloadId, slot }) })
        : await fetch(`/api/protection/disk-exceptions/${workloadId}/${slot}`, { method: "DELETE" });
      if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || d.error || "Could not save"); }
      location.reload();
    } catch (e) { setErr(e instanceof Error ? e.message : "Could not save"); }
  }
  const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = terms.length ? rows.filter((r) => { const hay = [r.name, r.vmid, r.type === "lxc" ? "ct container" : "vm", statusText[r.status], r.plan?.name ?? "", r.covered_by.join(" "), r.problems.join(" "), (r.causes ?? []).map((c) => c.text).join(" ")].join(" ").toLowerCase(); return terms.every((t) => hay.includes(t)); }) : rows;
  const attention = rows.filter((r) => r.status !== "ok").map((r) => r.workload_id);
  const open = attention.length <= 5 ? attention : [];

  async function change(r: GuestRow, v: string) {
    if (!r.plan) return;
    setBusy(r.workload_id); setErr(null);
    const change = v === "__inherit" ? { action: "assign", clear: [{ scope_type: "vm", scope_value: r.workload_id }] }
      : { action: "assign", set: [{ scope_type: "vm", scope_value: r.workload_id, policy_id: v === "__none" ? null : v }] };
    try {
      const res = await fetch("/api/backup-policies/dry-run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cluster_id: r.plan.cluster_id, change, leave_other: true }) });
      const d = await res.json();
      if (!res.ok) throw new Error(d.error || "Could not check that");
      setPend({ op: d, name: r.name }); notifyOperationsChanged();
      window.scrollTo({ top: document.getElementById("guest-plan-change")?.offsetTop ?? 0, behavior: "smooth" });
    } catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
  }

  return (
    <div>
      <div className="flex items-center gap-3 mb-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search guests by name, ID, plan, result or what is missing" aria-label="Search guests" className="w-full max-w-md px-3 py-1.5 rounded bg-surface2 border border-border text-sm text-text placeholder:text-muted" />
        {terms.length > 0 && <span className="text-xs text-muted whitespace-nowrap">{shown.length} of {rows.length} guests</span>}
      </div>
      {err && <div className="text-bad text-xs mb-2">{err}</div>}
      {pend && <div id="guest-plan-change" className="mb-3"><Card><CardTitle>Change the plan of {pend.name}</CardTitle>
        <OpPreview key={pend.op.id} op={pend.op} confirmLabel="Confirm" progressText="Changing the backup jobs" doneText="Done and read back from Proxmox and the backup server." onFinished={() => notifyOperationsChanged()} onClose={() => { setPend(null); window.location.reload(); }} /></Card></div>}
      <Table<GuestRow & { id: string }>
        rows={shown.map((r) => ({ ...r, id: r.workload_id }))}
        emptyMessage={terms.length ? "No guest matches the search." : "No guests."}
        storageKey="protection-guests-v2"
        defaultExpanded={open}
        renderDetail={(r) => <div><div className="px-3 pt-3"><GuestPlan workloadId={r.workload_id} name={r.name} hideSelect /></div><RestorePoints workloadId={r.workload_id} name={r.name} vmid={r.vmid} /></div>}
        columns={[
          {
            header: "Guest",
            render: (r) => (
              <span className="flex items-center gap-1.5">
                <Link href={`/infrastructure/workloads/${r.workload_id}`} className="text-accent hover:underline">{r.name}</Link>
                <span className="text-xs text-muted">{r.vmid}</span>
                <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-surface2 text-muted">{r.type === "lxc" ? "CT" : "VM"}</span>
              </span>
            ),
            sortValue: (r) => r.name,
          },
          {
            header: "Plan",
            render: (r) => {
              const list = plans[r.plan?.cluster_id ?? ""] ?? [];
              if (!r.plan || !canEdit) return <span className="text-xs text-text">{r.plan?.exempt ? "No backup" : r.plan?.name ?? "none"}</span>;
              const mine = r.plan.source === "set for this guest";
              const value = r.plan.exempt ? "__none" : mine ? r.plan.policy_id ?? "__inherit" : "__inherit";
              const auto = r.plan.name ? `Automatic: ${r.plan.name}${r.plan.source && r.plan.source !== "the default policy" ? ` (${r.plan.source})` : ""}` : "Automatic: no plan";
              return (
                <select className={sel} disabled={busy === r.workload_id} value={value} onClick={(e) => e.stopPropagation()} onChange={(e) => change(r, e.target.value)} aria-label={`Plan for ${r.name}`}>
                  <option value="__inherit">{auto}</option><option value="__none">No backup</option>{list.map((p) => <option key={p.id} value={p.id}>{p.name}{p.is_default ? " (default)" : ""}</option>)}
                </select>
              );
            },
            sortValue: (r) => r.plan?.name ?? "",
          },
          { header: "Result", render: (r) => <span className={statusColor[r.status]}>{statusText[r.status]}</span>, sortValue: (r) => order[r.status] },
          ...stores.map((s) => ({
            header: s,
            render: (r: GuestRow & { id: string }) => {
              const c = r.copies.find((x) => x.store === s);
              return <span className={c?.fresh ? "text-text" : c?.last ? "text-warn" : "text-muted"}>{c?.age_text ?? "never"}</span>;
            },
            sortValue: (r: GuestRow & { id: string }) => r.copies.find((x) => x.store === s)?.last ?? 0,
          })),
          {
            header: "What is wrong, and why",
            render: (r) => (r.problems.length === 0 && !(r.causes ?? []).length && !(r.notes ?? []).length ? <span className="text-xs text-muted">Nothing</span> : (
              <span className="block text-xs space-y-0.5">
                {r.problems.map((x, i) => <span key={i} className="block text-warn">{x}</span>)}
                {(r.causes ?? []).map((c, i) => <span key={i} className={`block ${c.level === "warn" ? "text-bad" : "text-muted"}`}>Why: {c.text}</span>)}
                {(r.notes ?? []).map((x, i) => <span key={i} className="block text-muted">{x}</span>)}
                {canEdit && (r.disk_coverage?.excluded ?? []).map((d) => (
                  <button key={d.slot} className="block text-accent hover:underline" onClick={() => setLeftOut(r.workload_id, d.slot, !d.accepted)}>
                    {d.accepted ? `Disk ${d.slot}: stop treating as on purpose` : `Disk ${d.slot}: left out on purpose`}
                  </button>
                ))}
              </span>)),
          },
        ]}
      />
    </div>
  );
}
