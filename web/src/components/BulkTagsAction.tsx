"use client";

import { useState } from "react";
import type { Operation, Workload } from "@/lib/api";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { isInFlight, isTerminal } from "@/lib/operationStatus";

type Row = { w: Workload; before: string[]; after: string[]; op: Operation | null; error: string | null };
const split = (s: string) => s.split(/[,;\s]+/).map((t) => t.trim()).filter(Boolean);
const has = (list: string[], t: string) => list.some((x) => x.toLowerCase() === t.toLowerCase());
const MAX = 50;

/** Add and/or remove tags on every ticked guest. Each guest gets its own previewed, verified tag change (the normal operation); this just runs them in a row. */
export default function BulkTagsAction({ workloads, onClose }: { workloads: Workload[]; onClose: () => void }) {
  const [add, setAdd] = useState("");
  const [remove, setRemove] = useState("");
  const [rows, setRows] = useState<Row[] | null>(null);
  const [phase, setPhase] = useState<"form" | "preview" | "running" | "done">("form");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const picked = workloads.slice(0, MAX);

  async function preview() {
    setBusy(true); setError(null);
    const adds = split(add), rems = split(remove);
    const out: Row[] = [];
    try {
      for (const w of picked) {
        const before = w.tags ?? [];
        const after = [...before.filter((t) => !has(rems, t)), ...adds.filter((t) => !has(before, t) || has(rems, t))];
        const dedup = after.filter((t, i) => after.findIndex((x) => x.toLowerCase() === t.toLowerCase()) === i);
        if (dedup.length === before.length && dedup.every((t) => has(before, t))) { out.push({ w, before, after: dedup, op: null, error: "no change" }); continue; }
        const r = await fetch("/api/operations/workload-tags/dry-run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workload_id: w.id, tags: dedup }) });
        const d = await r.json();
        out.push({ w, before, after: dedup, op: r.ok ? d : null, error: r.ok ? null : d.error || "could not check" });
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
    } finally {
      setPhase("done"); notifyOperationsChanged();
    }
  }

  const changed = rows?.filter((r) => r.op && r.op.status === "awaiting_approval") ?? [];
  const statusText = (r: Row) => (r.error ? r.error : !r.op ? "—" : r.op.status === "completed" ? "done" : r.op.status === "failed" ? `failed: ${r.op.error ?? ""}` : r.op.status === "blocked" ? `blocked: ${((r.op.dry_run_result as { reasons?: string[] } | null)?.reasons ?? []).slice(0, 2).join("; ")}` : r.op.status.replace(/_/g, " "));
  return (
    <div className="mb-3 border border-border rounded p-3 bg-surface2/40 text-xs">
      <div className="font-medium text-text mb-1">Tags for {picked.length} guest{picked.length === 1 ? "" : "s"}{workloads.length > MAX ? ` (the first ${MAX} of ${workloads.length} selected)` : ""}</div>
      {phase === "form" ? (
        <div className="space-y-2">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 max-w-2xl">
            <div><label className="block text-muted mb-1">Add tags</label><input className="bg-surface border border-border rounded px-2 py-1.5 text-sm w-full" value={add} onChange={(e) => setAdd(e.target.value)} placeholder="prod, web" /></div>
            <div><label className="block text-muted mb-1">Remove tags</label><input className="bg-surface border border-border rounded px-2 py-1.5 text-sm w-full" value={remove} onChange={(e) => setRemove(e.target.value)} placeholder="old-tag" /></div>
          </div>
          {error && <div className="text-bad">{error}</div>}
          <div className="flex gap-2 items-center">
            <button type="button" disabled={busy || (!add.trim() && !remove.trim())} onClick={preview} className="px-2 py-1 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50">{busy ? "Checking each guest…" : "Preview"}</button>
            <button type="button" onClick={onClose} className="text-muted hover:text-text">cancel</button>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          <table className="w-full">
            <thead><tr className="text-left text-muted"><th className="pr-3 py-1">Guest</th><th className="pr-3">Tags now</th><th className="pr-3">Tags after</th><th>Result</th></tr></thead>
            <tbody>
              {rows!.map((r) => (
                <tr key={r.w.id} className="border-t border-border align-top">
                  <td className="pr-3 py-1 text-text">{r.w.name || `VM ${r.w.vmid}`}</td>
                  <td className="pr-3 text-muted">{r.before.join(", ") || "—"}</td>
                  <td className="pr-3 text-text">{r.after.join(", ") || "—"}</td>
                  <td className={r.op?.status === "completed" ? "text-good" : r.op?.status === "failed" || r.op?.status === "blocked" ? "text-bad" : "text-muted"}>{r.op?.status === "awaiting_approval" ? "ready" : statusText(r)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex gap-2 items-center">
            {phase === "preview" && <button type="button" disabled={changed.length === 0} onClick={run} className="px-2 py-1 rounded bg-ink text-on-ink border border-warn hover:bg-warn/10 disabled:opacity-50">Confirm &amp; change {changed.length} guest{changed.length === 1 ? "" : "s"}</button>}
            {phase === "running" && <span className="text-muted">Working through them one by one…</span>}
            <button type="button" onClick={() => { if (phase === "preview" && rows) rows.forEach((r) => { if (r.op && r.op.status === "awaiting_approval") fetch(`/api/operations/${r.op.id}/dismiss`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dismissed: true }) }).catch(() => {}); }); onClose(); }} className="text-muted hover:text-text">{phase === "done" ? "close" : "cancel"}</button>
          </div>
        </div>
      )}
    </div>
  );
}
