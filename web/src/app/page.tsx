import Link from "next/link";
import { apiFetch } from "@/lib/api";
import type { AuditEvent, DashboardSummary, Finding, Recommendation } from "@/lib/api";
import { Card, CardTitle, PageHeader } from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import RebootBanner from "@/components/RebootBanner";
import AutoRefresh from "@/components/AutoRefresh";
import QuickStartBanner from "@/components/QuickStartBanner";
import AutoBalanceBanner from "@/components/AutoBalanceBanner";
import type { AutoBalanceView } from "@/components/AutoBalanceCard";
import DashboardNodesList from "@/components/DashboardNodesList";
import { HalfGauge } from "@/components/HalfGauge";
import { BalanceGauge, NodeBalanceStrip } from "@/components/ClusterBalance";
import { ServerIcon, ClusterIcon, StorageIcon, CpuIcon, MemoryIcon, DashboardIcon, ShieldIcon, HealthIcon, WrenchIcon } from "@/components/Icons";
import { formatBytes } from "@/lib/format";

type ProtectionSummary = { ok: number; partial: number; none: number; total: number; has_backup_server: boolean } | null;

type SnapshotsSummary = {
  total: number; workloads: number; oldest_days: number | null; warn_days: number; crit_days: number; aged: number; aged_critical: number;
  oldest: { workload_id: string; vmid: number; workload_name: string | null; snapshot: string; age_days: number | null }[];
};

export default async function DashboardPage() {
  // Deliberately only cheap DB reads here. reboot-required (RebootBanner,
  // below) used to be fetched server-side too -- a real SSH probe per node
  // (1-3+ seconds each, measured 2026-09-12), blocking the entire
  // Dashboard on it. Isolated into its own client component instead, same
  // fix already applied to Nodes/Maintenance.
  const [summary, findings, activity, recommendations, protection, autoBalance, snapshots] = await Promise.all([
    apiFetch<DashboardSummary>("/api/dashboard/summary"),
    apiFetch<Finding[]>("/api/findings?active=true"),
    apiFetch<AuditEvent[]>("/api/audit-events?limit=8"),
    apiFetch<Recommendation[]>("/api/recommendations"),
    apiFetch<ProtectionSummary>("/api/protection/summary").catch(() => null),
    apiFetch<AutoBalanceView[]>("/api/auto-balance").catch(() => [] as AutoBalanceView[]),
    apiFetch<SnapshotsSummary>("/api/workloads/snapshots-summary").catch(() => null),
  ]);

  const wrapperFindings = findings.filter((f) => f.category === "host_wrapper" && f.triage !== "dismissed");
  const issueCount = findings.filter((f) => f.severity === "critical" || f.severity === "warning").length;
  const criticalCount = findings.filter((f) => f.severity === "critical").length;
  const topFindings = [...findings]
    .filter((f) => f.severity === "critical" || f.severity === "warning")
    .sort((a, b) => (a.severity === "critical" ? 0 : 1) - (b.severity === "critical" ? 0 : 1))
    .slice(0, 5);
  const topRecommendations = [...recommendations]
    .sort((a, b) => (severityRank(a.severity) - severityRank(b.severity)))
    .slice(0, 5);

  const hasProtectionData = !!protection && protection.total > 0 && protection.has_backup_server;

  const cpuValues = summary.nodes_detail.map((n) => n.cpu_usage_pct).filter((v): v is number => v !== null);
  const memValues = summary.nodes_detail.map((n) => n.mem_usage_pct).filter((v): v is number => v !== null);
  const avgCpu = cpuValues.length ? cpuValues.reduce((a, b) => a + b, 0) / cpuValues.length : null;
  const avgMem = memValues.length ? memValues.reduce((a, b) => a + b, 0) / memValues.length : null;
  const nodesInMaintenance = summary.nodes_detail.filter((n) => n.maintenance_mode);

  return (
    <div>
      <PageHeader title="Dashboard" subtitle="What needs my attention right now?" icon={<DashboardIcon className="w-5 h-5" />} />
      <AutoRefresh seconds={30} />
      <QuickStartBanner />

      <div className="flex flex-wrap gap-3 mb-4 empty:mb-0">
        {nodesInMaintenance.length > 0 && (
          <Card className="border-warn/40 flex-1 min-w-[280px]">
            <div className="flex items-center gap-2 text-sm text-warn">
              <WrenchIcon className="w-4 h-4" />
              <span className="font-medium">
                {nodesInMaintenance.map((n) => n.name).join(", ")} {nodesInMaintenance.length === 1 ? "is" : "are"} in maintenance mode
              </span>
              <Link href="/operations/maintenance" className="text-xs text-accent hover:underline ml-auto">
                Go to Maintenance →
              </Link>
            </div>
          </Card>
        )}
        {wrapperFindings.length > 0 && (
          <Card className="border-warn/40 flex-1 min-w-[280px]">
            <div className="flex items-center gap-2 text-sm text-warn flex-wrap">
              <ServerIcon className="w-4 h-4" />
              <span className="font-medium">
                Host wrapper needs attention on {wrapperFindings.length} {wrapperFindings.length === 1 ? "node" : "nodes"}:{" "}
                {wrapperFindings.map((f) => (f.evidence as { node?: string } | null)?.node ?? f.title).slice(0, 4).join(", ")}
                {wrapperFindings.length > 4 ? "…" : ""}
              </span>
              <span className="text-xs text-muted">Run the host script on the node to install or upgrade it.</span>
              <Link href="/platform/credentials#builder-host" className="text-xs text-accent hover:underline ml-auto">
                Open the host script builder →
              </Link>
            </div>
          </Card>
        )}
        <AutoBalanceBanner clusters={autoBalance} />
        <RebootBanner nodes={summary.nodes_detail.map((n) => ({ id: n.id, name: n.name }))} />
      </div>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,620px),1fr))] gap-4 mb-4">
      <Card className="flex flex-col">
        <CardTitle>Cluster Resource Usage</CardTitle>
        <div className="flex-1 flex flex-wrap items-center content-center justify-around gap-4 py-1">
          <HalfGauge value={avgCpu} label="Avg CPU across nodes" icon={<CpuIcon />} />
          <HalfGauge
            value={avgMem}
            label="Avg Memory across nodes"
            icon={<MemoryIcon />}
            detail={
              summary.mem_total_bytes ? `${formatBytes(summary.mem_used_bytes)} of ${formatBytes(summary.mem_total_bytes)}` : null
            }
          />
          <HalfGauge
            value={summary.storage_used_pct}
            label="Storage used"
            icon={<StorageIcon />}
            detail={
              summary.storage_total_bytes
                ? `${formatBytes(summary.storage_used_bytes)} of ${formatBytes(summary.storage_total_bytes)}`
                : null
            }
          />
          <BalanceGauge nodes={summary.nodes_detail} icon={<ClusterIcon />} />
        </div>
        <div className="mt-auto pt-3 border-t border-border flex flex-wrap items-center justify-between gap-x-6 gap-y-1 text-xs text-muted">
          <span>
            {[
              [summary.counts.clusters, "cluster", "clusters"],
              [summary.counts.nodes, "node", "nodes"],
              [summary.counts.vms, "VM", "VMs"],
              [summary.counts.containers, "container", "containers"],
              [summary.counts.storage, "storage pool", "storage pools"],
            ].map(([n, one, many], i) => (
              <span key={String(one)}>
                {i > 0 && " · "}
                <span className="font-medium text-text tabular-nums">{n}</span> {n === 1 ? one : many}
              </span>
            ))}
          </span>
          <span className="tabular-nums">
            <span className="font-medium text-text">{summary.workloads_running}</span> of {summary.workloads_total} running ·{" "}
            {summary.workloads_total - summary.workloads_running} not running
          </span>
        </div>
      </Card>

      <Card>
        <CardTitle>{`Host Load · ${summary.nodes_online} / ${summary.nodes_total} online`}</CardTitle>
        <NodeBalanceStrip nodes={summary.nodes_detail} />
      </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4">
        <Card
          className={`${summary.cluster_status === "critical" ? "border-bad/50" : summary.cluster_status === "warning" ? "border-warn/40" : ""}`}
        >
          <div className="flex items-center gap-2 mb-3">
            <HealthIcon className="w-4 h-4 text-proxmox/70" />
            <span className="text-xs font-semibold uppercase tracking-wider text-muted">Health</span>
            {summary.cluster_status === "healthy" && issueCount > 0 ? (
              <span className={`text-xs font-medium ${criticalCount > 0 ? "text-bad" : "text-warn"}`}>
                {criticalCount > 0 ? `${criticalCount} critical` : "No critical findings"}{issueCount - criticalCount > 0 ? ` · ${issueCount - criticalCount} warning${issueCount - criticalCount === 1 ? "" : "s"} to review` : ""}
              </span>
            ) : (
              <StatusBadge status={summary.cluster_status} />
            )}
          </div>
          <div className="grid grid-cols-2 gap-4 text-center">
            <Link href="/operations/maintenance" className="hover:underline">
              <div className="text-xs text-muted mb-1">Updates</div>
              <div className={`text-xl font-semibold ${summary.nodes_with_updates > 0 ? "text-warn" : "text-text"}`}>{summary.nodes_with_updates}</div>
              <div className="text-xs text-muted">{summary.nodes_with_updates === 1 ? "node has" : "nodes have"} updates</div>
            </Link>
            <Link href="/operations/health" className="hover:underline">
              <div className="text-xs text-muted mb-1">Active findings</div>
              <div className={`text-xl font-semibold ${criticalCount > 0 ? "text-bad" : issueCount > 0 ? "text-warn" : "text-text"}`}>{issueCount}</div>
              <div className="text-xs text-muted">critical + warning</div>
            </Link>
          </div>
        </Card>
      <Card>
        <CardTitle>Protection</CardTitle>
        {hasProtectionData ? (
          <>
          <div className="grid grid-cols-3 gap-4 text-center">
            <Link href="/operations/protection" className="hover:underline">
              <div className="text-xs text-muted flex items-center justify-center gap-1 mb-1">
                <ShieldIcon className="w-4 h-4 text-good/70" />
                3-2-1 met
              </div>
              <div className="text-xl font-semibold text-good">{protection!.ok}</div>
            </Link>
            <Link href="/operations/protection" className="hover:underline">
              <div className="text-xs text-muted flex items-center justify-center gap-1 mb-1">
                <ShieldIcon className="w-4 h-4 text-warn/70" />
                Partly protected
              </div>
              <div className="text-xl font-semibold text-warn">{protection!.partial}</div>
            </Link>
            <Link href="/operations/protection" className="hover:underline">
              <div className="text-xs text-muted flex items-center justify-center gap-1 mb-1">
                <ShieldIcon className="w-4 h-4 text-bad/70" />
                Not protected
              </div>
              <div className="text-xl font-semibold text-bad">{protection!.none}</div>
            </Link>
          </div>
          <div className="text-xs text-muted mt-2 text-center">of {protection!.total} current guests, the same count as the Protection page</div>
          </>
        ) : (
          <div className="text-sm text-muted py-2">No protection provider configured yet -- see Operations → Protection.</div>
        )}
      </Card>
        {snapshots && (
            <Card className={`${snapshots.aged_critical > 0 ? "border-bad/40" : snapshots.aged > 0 ? "border-warn/40" : ""}`}>
              <CardTitle>Snapshots</CardTitle>
              <div className="grid grid-cols-3 gap-4 text-center mb-3">
                <div>
                  <div className="text-xs text-muted mb-1">Total</div>
                  <div className="text-xl font-semibold text-text">{snapshots.total}</div>
                  <div className="text-xs text-muted">on {snapshots.workloads} VM{snapshots.workloads === 1 ? "" : "s"}</div>
                </div>
                <div>
                  <div className="text-xs text-muted mb-1">Oldest</div>
                  <div className={`text-xl font-semibold ${snapshots.oldest_days != null && snapshots.oldest_days >= snapshots.crit_days ? "text-bad" : snapshots.oldest_days != null && snapshots.oldest_days >= snapshots.warn_days ? "text-warn" : "text-text"}`}>
                    {snapshots.oldest_days != null ? `${snapshots.oldest_days} d` : "—"}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-muted mb-1">{`Older than ${snapshots.warn_days} d`}</div>
                  <div className={`text-xl font-semibold ${snapshots.aged_critical > 0 ? "text-bad" : snapshots.aged > 0 ? "text-warn" : "text-good"}`}>{snapshots.aged}</div>
                </div>
              </div>
              {snapshots.oldest.length > 0 && (
                <ul className="text-sm divide-y divide-border">
                  {snapshots.oldest.slice(0, 2).map((s) => (
                    <li key={`${s.workload_id}-${s.snapshot}`} className="py-1 flex justify-between gap-3">
                      <Link href={`/infrastructure/workloads/${s.workload_id}?tab=snapshots`} className="text-accent hover:underline truncate">{s.workload_name || `VMID ${s.vmid}`} · {s.snapshot}</Link>
                      <span className="text-muted whitespace-nowrap">{s.age_days != null ? `${s.age_days} d old` : ""}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
        <Card className={topFindings.length > 0 ? "border-warn/40" : ""}>
          <CardTitle>Needs Attention</CardTitle>
          {topFindings.length === 0 ? (
            <div className="text-sm text-muted py-2">Nothing needs attention right now.</div>
          ) : (
            <div className="divide-y divide-border">
              {topFindings.map((f) => (
                <div key={f.id} className="flex items-center justify-between py-2 text-sm gap-2">
                  <div className="min-w-0">
                    <span className="text-text">{f.title}</span>
                    <span className="text-muted text-xs uppercase ml-2">{f.category}</span>
                  </div>
                  <StatusBadge status={f.severity} />
                </div>
              ))}
            </div>
          )}
          <div className="mt-2">
            <Link href="/operations/health" className="text-xs text-accent hover:underline">
              View all findings →
            </Link>
          </div>
        </Card>

        <Card>
          <CardTitle>Top Recommendations</CardTitle>
          {topRecommendations.length === 0 ? (
            <div className="text-sm text-muted py-2">No open recommendations right now.</div>
          ) : (
            <div className="divide-y divide-border">
              {topRecommendations.map((r) => (
                <div key={r.id} className="flex items-center justify-between py-2 text-sm gap-2">
                  <span className="text-text min-w-0">{r.title}</span>
                  <StatusBadge status={r.severity} />
                </div>
              ))}
            </div>
          )}
          <div className="mt-2">
            <Link href="/operations/recommendations" className="text-xs text-accent hover:underline">
              View all recommendations →
            </Link>
          </div>
        </Card>
      </div>


      <Card className="mb-4">
        <CardTitle>Nodes</CardTitle>
        <DashboardNodesList initialNodes={summary.nodes_detail} />
      </Card>

      <Card>
        <CardTitle>Recent Activity</CardTitle>
        <div className="divide-y divide-border">
          {activity.length === 0 && <div className="text-sm text-muted py-4">No activity recorded yet.</div>}
          {activity.map((event) => (
            <div key={event.id} className="flex items-center justify-between py-2 text-sm">
              <span className="text-text">{describeEvent(event)}</span>
              <span className="text-muted text-xs">{new Date(event.timestamp).toLocaleString()}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

function severityRank(s: string): number {
  return { critical: 0, warning: 1, info: 2 }[s] ?? 3;
}

function describeEvent(event: AuditEvent): string {
  const map: Record<string, string> = {
    "application.startup": "Application started",
    "inventory.discovery.started": "Inventory synchronization started",
    "inventory.discovery.completed": "Inventory synchronized",
    "inventory.discovery.failed": "Inventory synchronization failed",
    "pve_target.created": "PVE target created",
    "pve_target.connection_tested": "PVE connection tested",
    "credential.created": "Credential created",
    "settings.changed": "Settings changed",
    "auth.login.success": "User signed in",
    "auth.login.failed": "Failed sign-in attempt",
    "auth.logout": "User signed out",
    "protection.sync.completed": "Protection status synchronized",
    "protection.sync.failed": "Protection sync failed",
    "maintenance_plan.generated": "Maintenance plan generated",
    "policy.changed": "Policy changed",
  };
  return map[event.event_type] || event.event_type.replace(/_/g, " ").replace(/\./g, " ");
}
