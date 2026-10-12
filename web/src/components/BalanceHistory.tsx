"use client";

import { useEffect, useState } from "react";
import { Card, CardTitle } from "@/components/Card";

type NodeSnap = { name: string; status: string; maintenance: boolean; mem_pct: number | null; cpu_pct: number | null };
type Refusals = {
  blocked_total?: number; blocked?: { name: string; from: string; to: string; why: string; gain?: number | null }[];
  left_alone_total?: number; left_alone?: { reason: string; count: number }[];
};
type Check = {
  kind: "check"; id: string; at: string; first_at: string; repeat_count: number; cluster: string | null; trigger: "automatic" | "manual"; actor: string | null;
  mode: string | null; level: string | null; result: string; reason: string | null; balance_score: number | null; trigger_score: number | null; moves: number;
  operation_id: string | null;
  details: { nodes?: NodeSnap[]; plan?: { name: string; from: string; to: string; storage?: string | null }[]; refusals?: Refusals; scope?: string; metric?: string } | null;
};
type Move = {
  kind: "move"; id: string; at: string; started_at: string | null; status: string; workload: string | number | null; vmid: number | null; from: string | null; to: string | null;
  storage: string | null; duration_seconds: number | null; automatic: boolean; by: string | null; error: string | null; cluster: string | null;
};
type Item = Check | Move;

function ago(iso: string) {
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return m < 1 ? "just now" : m < 90 ? `${m} min ago` : m < 2880 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
}
function when(iso: string) {
  return new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
function dur(s: number | null) {
  if (s == null) return "";
  return s < 90 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

const RESULT: Record<string, { label: string; cls: string }> = {
  balanced: { label: "Balanced", cls: "bg-good/15 text-good" },
  skipped: { label: "Skipped", cls: "bg-muted/15 text-muted" },
  no_moves: { label: "No move worth making", cls: "bg-accent/15 text-accent" },
  plan_created: { label: "Plan prepared", cls: "bg-warn/15 text-warn" },
  plan_auto_approved: { label: "Auto-approved", cls: "bg-warn/15 text-warn" },
  stopped: { label: "Stopped", cls: "bg-bad/15 text-bad" },
  error: { label: "Error", cls: "bg-bad/15 text-bad" },
};
const MOVE_STATUS: Record<string, string> = { completed: "bg-good/15 text-good", failed: "bg-bad/15 text-bad", blocked: "bg-bad/15 text-bad", cancelled: "bg-muted/15 text-muted" };

function summary(c: Check): string {
  const sc = c.balance_score != null ? `score ${c.balance_score}${c.trigger_score != null ? `, acts below ${c.trigger_score}` : ""}` : null;
  switch (c.result) {
    case "balanced": return `The cluster is balanced enough (${sc ?? "no score"}), so nothing was planned.`;
    case "skipped": return `Nothing done: ${c.reason ?? "skipped"}.`;
    case "no_moves": return c.trigger === "manual" && !c.balance_score
      ? c.reason ?? "No move would make memory use more even."
      : `The cluster is out of balance (${sc ?? "no score"}) but no move was worth making at this level${c.details?.refusals?.blocked_total ? `: ${c.details.refusals.blocked_total} candidate move(s) were refused by a safety rule` : ""}.`;
    case "plan_created": return `Prepared a plan with ${c.moves} move${c.moves === 1 ? "" : "s"}${sc ? ` (${sc})` : ""}${c.reason ? `. ${c.reason}` : ""}.`;
    case "plan_auto_approved": return `Approved and started ${c.moves} move${c.moves === 1 ? "" : "s"} on its own${sc ? ` (${sc})` : ""}.`;
    case "stopped": return `Automatic approval stopped: ${c.reason ?? ""}`;
    case "error": return `The check could not run: ${c.reason ?? "unknown error"}`;
    default: return c.reason ?? c.result;
  }
}

function CheckRow({ c }: { c: Check }) {
  const r = RESULT[c.result] ?? { label: c.result, cls: "bg-muted/15 text-muted" };
  const d = c.details;
  const hasDetail = !!(d && ((d.nodes && d.nodes.length) || (d.plan && d.plan.length) || d.refusals?.blocked_total || d.refusals?.left_alone_total));
  return (
    <li className="py-2.5">
      <div className="flex items-start gap-3">
        <span className={`shrink-0 mt-0.5 w-40 text-center px-2 py-0.5 rounded text-xs font-medium ${r.cls}`}>{r.label}</span>
        <div className="flex-1 min-w-0">
          <div className="text-sm text-text">{summary(c)}</div>
          <div className="text-xs text-muted mt-0.5">
            {c.trigger === "manual" ? `Preview by ${c.actor ?? "someone"}` : `Automatic check${c.mode === "auto_approve" ? " (auto-approve)" : ""}${c.level ? `, ${c.level}` : ""}`}
            {c.cluster ? ` · cluster ${c.cluster}` : ""}
            {c.repeat_count > 1 ? ` · same result ${c.repeat_count} times, from ${when(c.first_at)} to ${when(c.at)}` : ""}
          </div>
          {hasDetail && (
            <details className="mt-1.5">
              <summary className="text-xs text-accent cursor-pointer">What it looked at</summary>
              <div className="mt-1.5 space-y-2 text-xs text-muted">
                {d?.nodes && d.nodes.length > 0 && (
                  <table className="text-xs">
                    <thead><tr className="text-left"><th className="pr-4 font-medium">Node</th><th className="pr-4 font-medium">Memory</th><th className="pr-4 font-medium">CPU</th><th className="font-medium">State</th></tr></thead>
                    <tbody>
                      {d.nodes.map((n) => (
                        <tr key={n.name}><td className="pr-4 text-text">{n.name}</td><td className="pr-4 tabular-nums">{n.mem_pct ?? "—"}%</td><td className="pr-4 tabular-nums">{n.cpu_pct ?? "—"}%</td>
                          <td>{n.maintenance ? "maintenance" : n.status}</td></tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {d?.plan && d.plan.length > 0 && (
                  <div><span className="text-text">Planned: </span>{d.plan.map((m) => `${m.name} ${m.from} → ${m.to}${m.storage ? ` (disks on ${m.storage})` : ""}`).join("; ")}</div>
                )}
                {d?.refusals?.blocked && d.refusals.blocked.length > 0 && (
                  <div>
                    <span className="text-text">Refused by a safety rule ({d.refusals.blocked_total}): </span>
                    {d.refusals.blocked.map((b) => `${b.name} ${b.from} → ${b.to}: ${b.why}`).join("; ")}
                  </div>
                )}
                {d?.refusals?.left_alone && d.refusals.left_alone.length > 0 && (
                  <div>
                    <span className="text-text">Left where they are ({d.refusals.left_alone_total}): </span>
                    {d.refusals.left_alone.map((l) => `${l.count} × ${l.reason}`).join("; ")}
                  </div>
                )}
              </div>
            </details>
          )}
        </div>
        <span className="shrink-0 text-xs text-muted text-right" title={new Date(c.at).toLocaleString()}>{ago(c.at)}</span>
      </div>
    </li>
  );
}

function MoveRow({ m }: { m: Move }) {
  return (
    <li className="py-2.5 flex items-start gap-3">
      <span className={`shrink-0 mt-0.5 w-40 text-center px-2 py-0.5 rounded text-xs font-medium ${MOVE_STATUS[m.status] ?? "bg-accent/15 text-accent"}`}>
        Move {m.status.replace(/_/g, " ")}
      </span>
      <div className="flex-1 min-w-0">
        <div className="text-sm text-text">
          {m.workload ?? "A VM"}{m.vmid && m.workload !== m.vmid ? ` (VM ${m.vmid})` : ""}: {m.from ?? "?"} → {m.to ?? "?"}
          {m.storage ? `, disks to ${m.storage}` : ""}
          {m.duration_seconds != null ? ` · took ${dur(m.duration_seconds)}` : ""}
        </div>
        <div className="text-xs text-muted mt-0.5">
          {m.automatic ? "Started by automatic balancing" : `Approved by ${m.by ?? "a person"}`}{m.cluster ? ` · cluster ${m.cluster}` : ""}
        </div>
        {m.error && <div className="text-xs text-bad mt-0.5">{m.error}</div>}
      </div>
      <span className="shrink-0 text-xs text-muted text-right" title={new Date(m.at).toLocaleString()}>{ago(m.at)}</span>
    </li>
  );
}

/** Everything Balance Load did: each check (automatic cycles and manual previews, repeats folded) and each move that followed. */
export default function BalanceHistory() {
  const [items, setItems] = useState<Item[] | null>(null);
  const [interval, setIntervalSeconds] = useState(300);
  const [filter, setFilter] = useState<"all" | "checks" | "moves">("all");
  const [shown, setShown] = useState(25);

  useEffect(() => {
    let live = true;
    const load = () =>
      fetch("/api/auto-balance/history?limit=200")
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => { if (live && d) { setItems(d.items); setIntervalSeconds(d.check_interval_seconds ?? 300); } })
        .catch(() => {});
    load();
    const t = setInterval(load, 30000);
    return () => { live = false; clearInterval(t); };
  }, []);

  const list = (items ?? []).filter((i) => filter === "all" || (filter === "checks" ? i.kind === "check" : i.kind === "move"));
  return (
    <Card id="balance-history">
      <CardTitle>History</CardTitle>
      <p className="text-sm text-muted mb-3">
        Every check Balance Load has made and every move that came of it. Automatic balancing checks the cluster about every {interval < 120 ? "minute" : `${Math.round(interval / 60)} minutes`}; when a check
        gives the same result as the one before, it is folded into one line with a count instead of repeating. Open <em>What it looked at</em> to see the node loads, planned moves and refusals behind a decision. Kept for 90 days.
      </p>
      <div className="flex gap-2 mb-2">
        {([["all", "Everything"], ["checks", "Checks"], ["moves", "Moves"]] as const).map(([id, label]) => (
          <button key={id} onClick={() => { setFilter(id); setShown(25); }}
            className={`px-2.5 py-1 rounded border text-xs ${filter === id ? "border-accent bg-accent/10 text-text" : "border-border text-muted hover:text-text"}`}>{label}</button>
        ))}
      </div>
      {items === null ? (
        <div className="text-sm text-muted">Loading…</div>
      ) : list.length === 0 ? (
        <div className="text-sm text-muted">Nothing yet. A line appears here each time Balance Load checks the cluster, automatically or when you preview.</div>
      ) : (
        <>
          <ul className="divide-y divide-border">
            {list.slice(0, shown).map((i) => (i.kind === "check" ? <CheckRow key={i.id} c={i} /> : <MoveRow key={i.id} m={i} />))}
          </ul>
          {list.length > shown && (
            <button onClick={() => setShown((s) => s + 25)} className="mt-2 text-xs text-accent hover:underline">Show more ({list.length - shown} older)</button>
          )}
        </>
      )}
    </Card>
  );
}
