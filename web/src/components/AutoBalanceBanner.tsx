"use client";

import Link from "next/link";
import type { AutoBalanceView } from "@/components/AutoBalanceCard";
import { scheduleText } from "@/components/AutoBalanceCard";
import { Card } from "@/components/Card";
import { MigrateIcon } from "@/components/Icons";
import { lastCheck } from "@/components/BalanceStatusStrip";

/** Dashboard: shown whenever automatic balancing is on, so nobody forgets PyXie may be moving guests by itself. */
export default function AutoBalanceBanner({ clusters }: { clusters: AutoBalanceView[] }) {
  const active = clusters.filter((c) => c.config.mode !== "off");
  if (active.length === 0) return null;
  return (
    <>
      {active.map((v) => {
        const paused = !!v.config.paused_until && new Date(v.config.paused_until).getTime() > Date.now();
        const auto = v.config.mode === "auto_approve";
        const tone = paused ? "border-border text-muted" : auto ? "border-warn/40 text-warn" : "border-accent/40 text-accent";
        const headline = paused
          ? `Automatic balancing is paused on ${v.cluster_name}`
          : auto
          ? `Automatic balancing is ON for ${v.cluster_name}: PyXie moves guests on its own (auto-approve, ${v.config.level})`
          : `Automatic balancing is on for ${v.cluster_name} (recommend only: it prepares plans and never moves anything itself)`;
        return (
          <Card key={v.cluster_id} className={`${tone} flex-1 min-w-[280px]`}>
            <div className="flex items-center gap-2 text-sm flex-wrap">
              <MigrateIcon className="w-4 h-4" />
              <span className="font-medium">{headline}</span>
              <span className="text-xs text-muted">
                Last check {lastCheck(v)}. Checks {scheduleText(v.config)}.
                {v.waiting_operation_id ? " A plan is waiting for your approval." : ""}
              </span>
              <Link href="/operations/balance" className="text-xs text-accent hover:underline ml-auto">Open Balance Load →</Link>
            </div>
          </Card>
        );
      })}
    </>
  );
}
