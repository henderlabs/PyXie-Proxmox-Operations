"use client";

import { useState } from "react";
import RestorePoints from "@/components/RestorePoints";
import { Card, CardTitle } from "@/components/Card";

export type RemovedRow = { workload_id: string; kind: string; vmid: number; name: string | null; type: string; last: number; stores: Record<string, number> };
export type UnknownRow = { kind: string; vmid: string; last: number; stores: Record<string, number> };

const day = (s: number) => new Date(s * 1000).toLocaleDateString();

/** Guests that no longer exist but still have backups: the ones PyXie knew can be restored here (as a new guest); the rest are listed so they are not forgotten. */
export default function RemovedGuests({ removed, unknown }: { removed: RemovedRow[]; unknown: UnknownRow[] }) {
  const [open, setOpen] = useState<string | null>(null);
  if (removed.length === 0 && unknown.length === 0) return null;
  return (
    <Card className="mb-4">
      <CardTitle>Removed guests that still have backups</CardTitle>
      <div className="text-xs text-muted mb-2">These guests no longer exist in Proxmox. Their backups stay until the keep rules prune them. Open one to restore it as a new guest.</div>
      <table className="w-full text-sm">
        <tbody>
          {removed.map((r) => (
            <tr key={r.workload_id} className="border-t border-border align-top">
              <td className="py-1.5 pr-3">
                <button type="button" onClick={() => setOpen(open === r.workload_id ? null : r.workload_id)} className="text-accent hover:underline">{open === r.workload_id ? "▾" : "▸"} {r.name || `ID ${r.vmid}`}</button>
                <span className="text-muted text-xs ml-2">{r.kind === "ct" ? "container" : "VM"} {r.vmid}</span>
                {open === r.workload_id && <div className="mt-2"><RestorePoints workloadId={r.workload_id} name={r.name || `${r.vmid}`} vmid={r.vmid} /></div>}
              </td>
              <td className="pr-3 text-muted text-xs whitespace-nowrap">last backup {day(r.last)}</td>
              <td className="text-muted text-xs">{Object.keys(r.stores).join(", ")}</td>
            </tr>
          ))}
          {unknown.map((r) => (
            <tr key={`${r.kind}-${r.vmid}`} className="border-t border-border">
              <td className="py-1.5 pr-3"><span className="text-text">{r.kind === "ct" ? "Container" : "VM"} {r.vmid}</span> <span className="text-muted text-xs">PyXie never saw this guest, so its name is unknown: restore it from Proxmox</span></td>
              <td className="pr-3 text-muted text-xs whitespace-nowrap">last backup {day(r.last)}</td>
              <td className="text-muted text-xs">{Object.keys(r.stores).join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
