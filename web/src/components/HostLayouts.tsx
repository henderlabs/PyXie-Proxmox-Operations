import type { NetworkLayout } from "@/lib/api";

// Read-only. Hosts with the same network layout are shown once. "Carries" is only filled where something proves it: the host's management address, the
// datacenter migration network, a corosync link address, or guests attached to the bridge. Nothing is read from a name.
export default function HostLayouts({ layouts }: { layouts: NetworkLayout[] }) {
  if (layouts.length === 0) return <div className="text-sm text-muted">No hosts yet.</div>;
  return (
    <div className="space-y-5">
      {layouts.map((c) => (
        <div key={c.cluster_id}>
          {layouts.length > 1 && <div className="text-xs text-muted mb-1">{c.cluster_name}</div>}
          {c.groups.map((g) => {
            const members = new Set(g.interfaces.flatMap((i) => i.ports));
            const rows = g.interfaces.filter((i) => !members.has(i.iface));
            return (
              <div key={g.nodes.join(",")} className="mb-4">
                <div className="text-sm text-text mb-1">
                  {g.error ? <span className="text-bad">Could not read {g.nodes.join(", ")}: {g.error}</span>
                    : g.is_main && c.groups.length === 1 ? <>Same on all {g.nodes.length} host{g.nodes.length === 1 ? "" : "s"} <span className="text-muted text-xs">({g.nodes.join(", ")})</span></>
                    : g.is_main ? <>Same on {g.nodes.length} hosts <span className="text-muted text-xs">({g.nodes.join(", ")})</span></>
                    : <span className="text-warn">Different on {g.nodes.join(", ")}</span>}
                </div>
                {!g.error && (
                  <table className="w-full text-sm">
                    <thead><tr className="text-left text-xs text-muted uppercase"><th className="py-1 pr-3">Link</th><th className="pr-3">Made of</th><th className="pr-3">Carries</th><th>State</th></tr></thead>
                    <tbody>
                      {rows.map((i) => (
                        <tr key={i.iface} className="border-t border-border align-top">
                          <td className="py-1.5 pr-3"><span className="text-text font-mono">{i.iface}</span> <span className="text-muted text-xs">{i.type}{i.vlan_aware ? ", VLAN-aware" : ""}</span></td>
                          <td className="pr-3 text-xs text-muted">{i.ports.length ? i.ports.join(" + ") : "—"}</td>
                          <td className="pr-3 text-xs">{i.carries.length ? i.carries.join("; ") : <span className="text-muted" title="PyXie only says what a link carries when it can prove it">not shown</span>}</td>
                          <td className="text-xs">{i.active ? <span className="text-good">up</span> : <span className="text-warn">inactive</span>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            );
          })}
          <div className="text-xs text-muted">
            Carries is filled only where it is proven: the host&apos;s management address, the datacenter migration network{c.facts.migration_network ? ` (${c.facts.migration_network})` : " (none set)"}, a cluster link address{c.facts.corosync_links_known ? "" : " (not readable here)"}, or guests attached to the bridge. A link that shows nothing is not necessarily unused.
          </div>
        </div>
      ))}
    </div>
  );
}
