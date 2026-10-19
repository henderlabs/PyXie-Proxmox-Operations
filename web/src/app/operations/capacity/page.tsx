import { apiFetch } from "@/lib/api";
import type { ClusterCapacity } from "@/lib/api";
import { Card, CardTitle, EmptyState, NoInfrastructureHint, PageHeader, StatTile } from "@/components/Card";
import BackupStorage from "@/components/BackupStorage";
import CapacityNodesTable from "@/components/tables/CapacityNodesTable";
import { formatBytes } from "@/lib/format";
import { Meter } from "@/components/Gauges";
import { MemoryIcon, ServerIcon, GaugeIcon } from "@/components/Icons";

type HostFailure = { cluster_id: string; name: string; reserve_pct: number; rows: { node: string; running_guests: number; ha_guests: number; needs_bytes: number; spare_bytes: number; fits: boolean; short_bytes: number }[] };

export default async function CapacityPage() {
  const clusters = await apiFetch<ClusterCapacity[]>("/api/capacity");
  const failure = await apiFetch<HostFailure[]>("/api/capacity/host-failure").catch(() => [] as HostFailure[]);

  return (
    <div>
      <PageHeader title="Capacity" subtitle="Allocated capacity vs. observed consumption" icon={<GaugeIcon className="w-5 h-5" />} />
      {clusters.length === 0 && (
        <Card>
          <EmptyState message={<NoInfrastructureHint subject="clusters" />} />
        </Card>
      )}
      {clusters.map((c) => (
        <Card key={c.cluster_id} className="mb-4">
          <CardTitle>{c.name}</CardTitle>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
            <StatTile
              label="Memory Allocated"
              icon={<MemoryIcon />}
              value={<Meter value={c.memory_allocation_pct} width={80} />}
              sub={`${formatBytes(c.total_allocated_memory_bytes)} of ${formatBytes(c.total_memory_bytes)}`}
            />
            <StatTile label="Busiest Node" value={c.busiest_node || "—"} icon={<ServerIcon />} />
            <StatTile label="Most Constrained" value={c.most_constrained_resource || "—"} icon={<GaugeIcon />} />
            <StatTile label="Nodes" value={c.nodes.length} icon={<ServerIcon />} />
          </div>

          <div className="mb-4">
            <CapacityNodesTable nodes={c.nodes} />
          </div>

          {(() => {
            const f = failure.find((x) => x.cluster_id === c.cluster_id);
            if (!f) return null;
            return (
              <div className="mb-4">
                <CardTitle>If a host fails</CardTitle>
                <div className="text-xs text-muted mb-2">Memory only: would the running guests of a failed host fit in what the other hosts have left, keeping {f.reserve_pct}% of each free? This is a calculation, not a failover test. Proxmox restarts a failed host&apos;s guests by itself only when they are managed by HA.</div>
                <table className="w-full text-sm">
                  <thead><tr className="text-left text-xs text-muted uppercase"><th className="py-1 pr-3">If this host fails</th><th className="pr-3">Running guests</th><th className="pr-3">Restart on their own (HA)</th><th className="pr-3">Memory to place</th><th className="pr-3">Left on the others</th><th>Result</th></tr></thead>
                  <tbody>
                    {f.rows.map((r) => (
                      <tr key={r.node} className="border-t border-border">
                        <td className="py-1.5 pr-3 text-text">{r.node}</td>
                        <td className="pr-3">{r.running_guests}</td>
                        <td className="pr-3">{r.ha_guests} of {r.running_guests}</td>
                        <td className="pr-3">{formatBytes(r.needs_bytes)}</td>
                        <td className="pr-3">{formatBytes(r.spare_bytes)}</td>
                        <td className={r.fits ? "text-good" : "text-bad"}>{r.fits ? "Fits" : `Short by ${formatBytes(r.short_bytes)}`}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          })()}

          <CardTitle>Workloads contributing most CPU pressure</CardTitle>
          <div className="text-sm divide-y divide-border">
            {c.workloads_contributing_most_pressure.length === 0 && <div className="text-muted py-2">Not enough observed data yet.</div>}
            {c.workloads_contributing_most_pressure.map((w) => (
              <div key={w.vmid} className="flex justify-between py-1.5">
                <span className="text-text">
                  {w.name || "unnamed"} <span className="text-muted">(vmid {w.vmid})</span>
                </span>
                <span className="text-muted">score {w.cpu_pressure_score}</span>
              </div>
            ))}
          </div>
        </Card>
      ))}
      <BackupStorage title="Backup storage (backup servers)" />
    </div>
  );
}
