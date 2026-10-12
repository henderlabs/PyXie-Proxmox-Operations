"use client";

import { Card } from "@/components/Card";
import { scheduleText, type AutoBalanceView } from "@/components/AutoBalanceCard";

function ago(iso?: string) {
  if (!iso) return "not yet";
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return m < 1 ? "just now" : m < 90 ? `${m} min ago` : m < 2880 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
}

function lastCheck(v: AutoBalanceView): string {
  const s = v.state;
  if (!s.last_checked_at) return "no check has run yet";
  const when = ago(s.last_checked_at);
  switch (s.last_result) {
    case "skipped": return `${when}: nothing to do (${s.last_reason ?? "skipped"})`;
    case "plan_created": return `${when}: prepared a plan with ${s.moves} move(s)${s.last_reason ? ` (${s.last_reason})` : ""}`;
    case "plan_auto_approved": return `${when}: approved and started a move`;
    case "stopped": return `${when}: auto-approve stopped (${s.last_reason})`;
    case "revoked": return `${when}: auto-approve was revoked`;
    default: return when;
  }
}

/** One glance: is automatic balancing on, what did the last check decide, when is the next one. */
export default function BalanceStatusStrip({ clusters }: { clusters: AutoBalanceView[] }) {
  if (clusters.length === 0) return null;
  return (
    <Card className="mb-4">
      <div className="space-y-2">
        {clusters.map((v) => {
          const mode = v.config.mode;
          const paused = !!v.config.paused_until && new Date(v.config.paused_until).getTime() > Date.now();
          const label = mode === "off" ? "Automatic balancing off" : paused ? "Paused" : mode === "auto_approve" ? "Auto-approve" : "Recommend only";
          const cls = mode === "off" || paused ? "bg-muted/15 text-muted" : mode === "auto_approve" ? "bg-warn/15 text-warn" : "bg-good/15 text-good";
          return (
            <div key={v.cluster_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
              <span className="text-text font-medium">Cluster {v.cluster_name}</span>
              <span className={`px-2 py-0.5 rounded text-xs font-medium ${cls}`}>{label}{mode !== "off" ? ` · ${v.config.level}` : ""}</span>
              {v.balance_score !== null && (
                <span className="text-muted" title="Higher is more even. Automatic balancing prepares a plan when the busy-hours score falls below its trigger.">
                  balance score {v.balance_score}{mode !== "off" ? ` (acts below ${v.presets[v.config.level].trigger_score})` : ""}
                </span>
              )}
              {mode !== "off" && (
                <span className="text-muted">
                  Last check {lastCheck(v)}. Checks {scheduleText(v.config)}{v.next_check_at ? `; next ${new Date(v.next_check_at).getTime() - Date.now() < 90000 ? "any moment" : new Date(v.next_check_at).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })}` : ""}.
                </span>
              )}
              {v.waiting_operation_id && <span className="text-warn">A plan is waiting for your approval below.</span>}
              <a href="#balance-history" className="ml-auto text-xs text-accent hover:underline">History ↓</a>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
