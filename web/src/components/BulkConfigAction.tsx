"use client";

import { useEffect, useState } from "react";
import type { Operation, Workload } from "@/lib/api";
import { abandonPlan, notifyOperationsChanged } from "@/lib/operationsBus";
import { isInFlight, isTerminal } from "@/lib/operationStatus";

type Spec = { key: string; label: string; group: string; type: "bool" | "int" | "float" | "enum" | "text"; apply: "live" | "restart"; choices?: string[] | null; min?: number | null; max?: number | null; max_length?: number | null };
type Change = { key: string; old: string | null; new: string | null; remove: boolean; applies: "live" | "restart" | "now" };
type Row = { w: Workload; op: Operation | null; error: string | null };
const MAX = 50;
const show = (v: string | null | undefined, t?: string) => (v == null || v === "" ? "(not set)" : t === "bool" ? (v === "1" ? "yes" : "no") : v);

/** One setting, one new value, applied to every ticked guest of one kind. Each guest gets its own previewed, digest-checked, verified change
 * (the normal Config change); this reviews them together and runs them in a row. Settings that wait for a restart stay pending, they never restart a guest. */
export default function BulkConfigAction({ workloads, onClose }: { workloads: Workload[]; onClose: () => void }) {
  const hasVm = workloads.some((w) => w.type === "vm"), hasCt = workloads.some((w) => w.type !== "vm");
  const [kind, setKind] = useState<"vm" | "ct">(hasVm ? "vm" : "ct");
  const [specs, setSpecs] = useState<Spec[]>([]);
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [rows, setRows] = useState<Row[] | null>(null);
  const [phase, setPhase] = useState<"form" | "preview" | "running" | "done">("form");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const picked = workloads.filter((w) => (kind === "vm" ? w.type === "vm" : w.type !== "vm") && !w.is_missing).slice(0, MAX);
  const spec = specs.find((s) => s.key === key) ?? null;

  useEffect(() => {
    setKey(""); setValue("");
    fetch(`/api/operations/workload-config/specs/${kind}`).then((r) => r.json()).then((d) => setSpecs(d.fields ?? [])).catch(() => setError("Could not load the settings list"));
  }, [kind]);

  async function preview() {
    if (!spec) return;
    setBusy(true); setError(null);
    const out: Row[] = [];
    try {
      for (const w of picked) {
        const r = await fetch("/api/operations/workload-config/dry-run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workload_id: w.id, changes: { [spec.key]: value } }) });
        const d = await r.json();
        out.push({ w, op: r.ok ? d : null, error: r.ok ? null : d.error || "could not check" });
      }
      setRows(out); setPhase("preview"); notifyOperationsChanged();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  async function run() {
    if (!rows) return;
    setPhase("running"); notifyOperationsChanged();
    const live = [...rows];
    try {
      for (const r of live) {
        if (r.op && r.op.status === "awaiting_approval") {
          try {
            const res = await fetch(`/api/operations/${r.op.id}/approve`, { method: "POST" });
            const d = await res.json();
            if (res.ok) r.op = d; else r.error = d.error || "approve failed";
          } catch { r.error = "approve failed: could not reach PyXie"; }
        }
      }
      setRows([...live]);
      let failures = 0;
      for (let i = 0; i < 150; i++) {
        let pending = false;
        for (const r of live) {
          if (!r.op || isTerminal(r.op.status)) continue;
          try {
            const res = await fetch(`/api/operations/${r.op.id}`);
            if (res.ok) { r.op = await res.json(); failures = 0; } else failures++;
          } catch { failures++; }
          if (r.op && (isInFlight(r.op.status) || r.op.status === "approved")) pending = true;
        }
        setRows([...live]);
        if (!pending || failures > 10) break;
        await new Promise((res) => setTimeout(res, 2000));
      }
    } finally { setPhase("done"); notifyOperationsChanged(); }
  }

  const ready = rows?.filter((r) => r.op && r.op.status === "awaiting_approval") ?? [];
  const changeOf = (r: Row): Change | null => ((r.op?.dry_run_result as { changes?: Change[] } | null)?.changes ?? [])[0] ?? null;
  const resultText = (r: Row) => {
    if (r.error) return r.error;
    if (!r.op) return "-";
    const s = r.op.status;
    if (s === "awaiting_approval") return "ready";
    if (s === "completed") return changeOf(r)?.applies === "live" ? "done" : "done (pending until restart)";
    if (s === "failed") return `failed: ${r.op.error ?? ""}`;
    if (s === "blocked") return `blocked: ${((r.op.dry_run_result as { reasons?: string[] } | null)?.reasons ?? []).slice(0, 2).join("; ")}`;
    return s.replace(/_/g, " ");
  };
  const field = "bg-surface border border-border rounded px-2 py-1.5 text-sm";
  return (
    <div className="mb-3 border border-border rounded p-3 bg-surface2/40 text-xs">
      <div className="font-medium text-text mb-1">Change a setting for {picked.length} {kind === "vm" ? "VM" : "container"}{picked.length === 1 ? "" : "s"}{workloads.length > picked.length ? ` (of ${workloads.length} selected)` : ""}</div>
      {phase === "form" ? (
        <div className="space-y-2">
          {hasVm && hasCt && (
            <div className="text-muted">Your selection mixes VMs and containers, which have different settings. Change settings for:{" "}
              <select className={field} value={kind} onChange={(e) => setKind(e.target.value as "vm" | "ct")}><option value="vm">the VMs</option><option value="ct">the containers</option></select>
            </div>
          )}
          <div className="flex flex-wrap gap-2 items-end">
            <div><label className="block text-muted mb-1">Setting</label>
              <select className={field} value={key} onChange={(e) => { setKey(e.target.value); setValue(""); }}>
                <option value="">Choose…</option>
                {specs.map((s) => <option key={s.key} value={s.key}>{s.group}: {s.label}</option>)}
              </select></div>
            {spec && (
              <div><label className="block text-muted mb-1">New value</label>
                {spec.type === "bool" ? <select className={field} value={value} onChange={(e) => setValue(e.target.value)}><option value="">Choose…</option><option value="1">yes</option><option value="0">no</option></select>
                  : spec.type === "enum" ? <select className={field} value={value} onChange={(e) => setValue(e.target.value)}><option value="">Choose…</option>{(spec.choices ?? []).map((c) => <option key={c}>{c}</option>)}</select>
                  : <input className={`${field} w-56`} value={value} onChange={(e) => setValue(e.target.value)} placeholder={spec.min != null ? `${spec.min} to ${spec.max}` : ""} />}
              </div>
            )}
          </div>
          {spec && <div className="text-muted">{spec.apply === "live" ? "Takes effect immediately on running guests." : "Saved as a pending change on each guest; it takes effect at that guest's next restart. No guest is restarted."} Leave the value empty only to remove the setting.</div>}
          {error && <div className="text-bad">{error}</div>}
          <div className="flex gap-2 items-center">
            <button type="button" disabled={busy || !spec || (spec.type !== "text" && value === "")} onClick={preview} className="px-2 py-1 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50">{busy ? "Checking each guest…" : "Preview"}</button>
            <button type="button" onClick={onClose} className="text-muted hover:text-text">cancel</button>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          <div className="text-muted">{spec?.label}: each guest is read from Proxmox now. Nothing is written until you confirm.</div>
          <table className="w-full">
            <thead><tr className="text-left text-muted"><th className="pr-3 py-1">Guest</th><th className="pr-3">Now</th><th className="pr-3">After</th><th className="pr-3">Takes effect</th><th>Result</th></tr></thead>
            <tbody>
              {rows!.map((r) => {
                const c = changeOf(r);
                return (
                  <tr key={r.w.id} className="border-t border-border align-top">
                    <td className="pr-3 py-1 text-text">{r.w.name || `VMID ${r.w.vmid}`}</td>
                    <td className="pr-3 text-muted">{c ? show(c.old, spec?.type) : "-"}</td>
                    <td className="pr-3 text-text">{c ? (c.remove ? "(removed)" : show(c.new, spec?.type)) : "-"}</td>
                    <td className="pr-3 text-muted">{c ? (c.applies === "live" ? "immediately" : c.applies === "restart" ? "next restart" : "now (guest is off)") : "-"}</td>
                    <td className={r.op?.status === "completed" ? "text-good" : r.op?.status === "failed" || r.op?.status === "blocked" || r.error ? "text-bad" : "text-muted"}>{resultText(r)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="flex gap-2 items-center">
            {phase === "preview" && <button type="button" disabled={ready.length === 0} onClick={run} className="px-2 py-1 rounded bg-ink text-on-ink border border-warn hover:bg-warn/10 disabled:opacity-50">Confirm &amp; change {ready.length} guest{ready.length === 1 ? "" : "s"}</button>}
            {phase === "running" && <span className="text-muted">Working through them one by one…</span>}
            <button type="button" onClick={() => { if (phase === "preview" && rows) rows.forEach((r) => { if (r.op && r.op.status === "awaiting_approval") abandonPlan(r.op.id); }); onClose(); }} className="text-muted hover:text-text">{phase === "done" ? "close" : "cancel"}</button>
          </div>
        </div>
      )}
    </div>
  );
}
