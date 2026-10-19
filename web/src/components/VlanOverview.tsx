"use client";

import { Fragment, useMemo, useState } from "react";
import type { NetworkSdnStatus, VlanSegment } from "@/lib/api";

// Read-only. One row per network: an SDN network (its VLAN comes from the network) or a bridge + VLAN pair. Unused SDN networks are listed too.
const vlanOf = (s: VlanSegment) => (s.sdn ? s.sdn.tag : s.tag);

export default function VlanOverview({ segments, sdn = [] }: { segments: VlanSegment[]; sdn?: NetworkSdnStatus[] }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = useMemo(() => (terms.length ? segments.filter((s) => {
    const hay = [s.bridge, s.sdn?.alias ?? "", s.sdn?.zone ?? "", String(vlanOf(s) ?? "untagged"), ...s.guests.map((g) => `${g.name ?? ""} ${g.vmid}`)].join(" ").toLowerCase();
    return terms.every((t) => hay.includes(t));
  }) : segments), [segments, q]); // eslint-disable-line react-hooks/exhaustive-deps
  const inUse = segments.filter((s) => s.guests.length > 0).length;
  const unused = segments.filter((s) => s.unused).length;
  if (segments.length === 0) {
    return <div className="text-sm text-muted">No guest network interfaces found.</div>;
  }
  return (
    <div>
      {sdn.filter((s) => !s.readable).map((s) => (
        <div key={s.cluster_id} className="text-xs text-warn mb-2">{s.cluster_name}: PyXie could not read the SDN networks ({s.error ?? "no answer"}). The read-only Proxmox account needs SDN.Audit (the built-in PVEAuditor role has it).</div>
      ))}
      <div className="flex flex-wrap items-center gap-3 mb-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by network, VLAN or guest" className="bg-surface border border-border rounded px-2 py-1 text-xs w-64" />
        <span className="text-xs text-muted">
          {segments.length} network{segments.length === 1 ? "" : "s"} · {inUse} in use{unused ? ` · ${unused} unused` : ""}
          {sdn.filter((s) => s.readable && s.zones.length).map((s) => ` · zone ${s.zones.join(", ")} (${s.cluster_name})`).join("")}
        </span>
      </div>
      <table className="w-full text-sm">
        <thead><tr className="text-left text-xs text-muted uppercase"><th className="py-1 pr-3">Network</th><th className="pr-3">VLAN</th><th className="pr-3">Guests</th><th className="pr-3">Hosts that can carry it</th><th /></tr></thead>
        <tbody>
          {shown.map((s) => {
            const key = `${s.cluster_id}-${s.bridge}-${s.tag}`;
            const v = vlanOf(s);
            return (
              <Fragment key={key}>
                <tr className="border-t border-border align-top">
                  <td className="py-1.5 pr-3"><span className="text-text font-mono">{s.bridge}</span>{s.sdn?.alias && <span className="text-muted text-xs ml-2">{s.sdn.alias}</span>}</td>
                  <td className="pr-3">{v != null ? v : <span className="text-muted">untagged</span>}</td>
                  <td className="pr-3">{s.guests.length ? s.guests.length : <span className="text-muted">0</span>}</td>
                  <td className="pr-3 text-xs">
                    {s.note ? <span className="text-warn">not checked</span>
                      : s.unused ? <span className="text-muted">unused</span>
                      : s.blocked_hosts.length === 0 ? <span className="text-good">all hosts</span>
                      : <span className="text-warn">not on {s.blocked_hosts.map((h) => h.node).join(", ")}</span>}
                  </td>
                  <td className="text-right text-xs">{s.guests.length > 0 && <button type="button" className="text-accent hover:underline" onClick={() => setOpen(open === key ? null : key)}>{open === key ? "hide" : "guests"}</button>}</td>
                </tr>
                {(s.note || s.problems.length > 0 || s.blocked_hosts.length > 0) && (
                  <tr><td colSpan={5} className="pb-1.5 text-xs">
                    {s.note && <div className="text-warn">{s.note}</div>}
                    {s.problems.map((p) => <div key={p} className="text-bad">{p}</div>)}
                    {s.blocked_hosts.length > 0 && <div className="text-warn">{s.blocked_hosts.map((h) => `${h.node} (${h.reason})`).join(", ")}</div>}
                  </td></tr>
                )}
                {open === key && (
                  <tr><td colSpan={5} className="pb-2">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 text-xs">
                      {s.guests.map((g) => (
                        <div key={`${g.workload_id}-${g.net_id}`} className="py-0.5 flex justify-between gap-2">
                          <span className="truncate text-text">{g.name ?? g.vmid} <span className="text-muted">{g.type === "vm" ? "VM" : "CT"} {g.vmid}</span></span>
                          <span className="text-muted shrink-0">{g.node} · {g.net_id}</span>
                        </div>
                      ))}
                    </div>
                  </td></tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
