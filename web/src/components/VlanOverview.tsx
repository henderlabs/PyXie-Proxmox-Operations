"use client";

import { useState } from "react";
import type { VlanSegment } from "@/lib/api";
import { Card, CardTitle } from "@/components/Card";

// Read-only. Guest NICs grouped by bridge + VLAN, with the hosts that could
// not carry each one (a guest on that VLAN could not migrate there).
const SHOWN = 6;

export default function VlanOverview({ segments }: { segments: VlanSegment[] }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  if (segments.length === 0) {
    return <div className="text-sm text-muted">No guest network interfaces found.</div>;
  }
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {segments.map((s) => {
        const key = `${s.cluster_id}-${s.bridge}-${s.tag}`;
        const guests = open[key] ? s.guests : s.guests.slice(0, SHOWN);
        return (
        <Card key={key}>
          <CardTitle>
            {s.tag === null ? "Untagged" : `VLAN ${s.tag}`}{" "}
            <span className="text-text/50 font-normal">
              · {s.bridge} · {s.cluster_name}
            </span>
          </CardTitle>
          <div className="text-xs text-muted mb-2">
            {s.guests.length} interface{s.guests.length === 1 ? "" : "s"}
          </div>
          <div className="divide-y divide-border">
            {guests.map((g) => (
              <div key={`${g.workload_id}-${g.net_id}`} className="py-1 flex justify-between gap-2 text-sm">
                <span className="truncate">
                  {g.name ?? g.vmid} <span className="text-muted text-xs">{g.type === "vm" ? "VM" : "CT"} {g.vmid}</span>
                </span>
                <span className="text-muted text-xs shrink-0">
                  {g.node} · {g.net_id}
                </span>
              </div>
            ))}
          </div>
          {s.guests.length > SHOWN && (
            <button type="button" onClick={() => setOpen({ ...open, [key]: !open[key] })} className="mt-1 text-xs text-accent hover:underline">
              {open[key] ? "Show fewer" : `Show all ${s.guests.length}`}
            </button>
          )}
          {s.problems.length > 0 && (
            <div className="mt-2 text-xs text-bad space-y-0.5">
              {s.problems.map((p) => (
                <div key={p}>{p}</div>
              ))}
            </div>
          )}
          {s.blocked_hosts.length > 0 && (
            <div className="mt-2 text-xs text-warn">
              Cannot carry this network:{" "}
              {s.blocked_hosts.map((h) => `${h.node} (${h.reason})`).join(", ")}
            </div>
          )}
        </Card>
        );
      })}
    </div>
  );
}
