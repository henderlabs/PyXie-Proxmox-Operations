import Link from "next/link";
import LocalTime from "@/components/LocalTime";
import { apiFetch } from "@/lib/api";
import type { HostMaintenanceStatus, Node, PveTask, Workload } from "@/lib/api";
import { Card, CardTitle, PageHeader } from "@/components/Card";
import NodeOverview from "@/components/NodeOverview";
import { formatBytes } from "@/lib/format";
import NodeWorkloadsTable from "@/components/tables/NodeWorkloadsTable";
import StatusBadge from "@/components/StatusBadge";
import { ServerIcon, WrenchIcon } from "@/components/Icons";
import RemoveHostButton from "@/components/RemoveHostButton";
import NodeConfig from "@/components/NodeConfig";
import HostShell from "@/components/HostShell";
import HostPower from "@/components/HostPower";
import WrapperPanel from "@/components/WrapperPanel";
import type { PolicyRow, StorageItem } from "@/lib/api";

export default async function NodeDetailPage({ params, searchParams }: { params: { id: string }; searchParams?: { tab?: string; wrapper?: string } }) {
  const tab = searchParams?.tab === "config" ? "config" : searchParams?.tab === "shell" ? "shell" : "overview";
  const [node, workloads, tasks, hostMaintenance, shellInfo] = await Promise.all([
    apiFetch<Node>(`/api/nodes/${params.id}`),
    apiFetch<Workload[]>(`/api/nodes/${params.id}/workloads`),
    apiFetch<PveTask[]>(`/api/nodes/${params.id}/tasks`),
    apiFetch<HostMaintenanceStatus>(`/api/nodes/${params.id}/host-maintenance-status`).catch(() => null),
    apiFetch<{ state: string }>(`/api/nodes/${params.id}/shell-info`).catch(() => null),
  ]);
  const showShellTab = !!shellInfo && shellInfo.state !== "admin_only" && shellInfo.state !== "no_permission";

  let pyxie: React.ComponentProps<typeof NodeConfig>["pyxie"] | null = null;
  if (tab === "config") {
    const [suggestions, policies, storage] = await Promise.all([
      apiFetch<Record<string, { current_performance_tier: string; current_trust_tier: string; current_notes: string }>>("/api/placement/node-tier-suggestions").catch(() => ({})),
      apiFetch<PolicyRow[]>("/api/policies?scope_type=node").catch(() => [] as PolicyRow[]),
      apiFetch<StorageItem[]>("/api/storage").catch(() => [] as StorageItem[]),
    ]);
    const sug = (suggestions as Record<string, { current_performance_tier: string; current_trust_tier: string; current_notes: string }>)[params.id];
    const locals = storage.filter((x) => x.scope === "node-local" && x.node_id === params.id);
    const shared = storage.filter((x) => x.scope === "cluster-shared" && x.cluster_id === node.cluster_id);
    pyxie = {
      nodeId: params.id, avoid: !!node.avoid_as_destination,
      tiers: { performance: sug?.current_performance_tier || "standard", trust: sug?.current_trust_tier || "standard" }, notes: sug?.current_notes || "",
      storageOptions: [...locals, ...shared].map((x) => ({ id: x.id, name: x.name, scope: x.scope })),
      defaultStorage: (policies.find((p) => p.key === "placement.default_storage_id" && p.scope_id === params.id)?.value as string | null) || null,
      autoResolvesTo: locals[0]?.name,
    };
  }

  return (
    <div className="contents">
      <NodeOverview
        nodeId={params.id}
        initialNode={node}
        rebootRequired={hostMaintenance ? hostMaintenance.reboot_required ?? null : null}
        header={
          <>
      <div className="flex items-start justify-between gap-3">
        <PageHeader title={node.name} subtitle="Host · live status from PVE" icon={<ServerIcon className="w-5 h-5" />} />
        <Link
          href={`/operations/maintenance?node=${node.id}`}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-warn hover:bg-warn/10 shrink-0 mt-9"
          title="Open this node on the Maintenance page"
        >
          <WrenchIcon className="w-4 h-4" />
          Manage in Maintenance
        </Link>
      </div>
          </>
        }
      />

      <div className="col-span-2 min-w-0">
      <div className="flex gap-4 border-b border-border mb-3 text-sm">
        <Link scroll={false} href={`/infrastructure/nodes/${params.id}`} className={`pb-1.5 ${tab === "overview" ? "text-text border-b-2 border-accent" : "text-muted hover:text-text"}`}>Overview</Link>
        <Link scroll={false} href={`/infrastructure/nodes/${params.id}?tab=config`} className={`pb-1.5 ${tab === "config" ? "text-text border-b-2 border-accent" : "text-muted hover:text-text"}`}>Config</Link>
        {showShellTab && <Link scroll={false} href={`/infrastructure/nodes/${params.id}?tab=shell`} className={`pb-1.5 ${tab === "shell" ? "text-text border-b-2 border-accent" : "text-muted hover:text-text"}`}>Shell</Link>}
      </div>
      {tab === "shell" && showShellTab ? (
        <HostShell nodeId={params.id} nodeName={node.name} />
      ) : tab === "config" && pyxie ? (
        <NodeConfig nodeId={params.id} nodeName={node.name} pyxie={pyxie} />
      ) : (
      <>
      {!node.is_missing && <div className="mb-3"><WrapperPanel nodeId={node.id} nodeName={node.name} initiallyOpen={searchParams?.wrapper === "1"} /></div>}
      {!node.is_missing && <div className="mb-3"><HostPower nodeId={node.id} nodeName={node.name} inMaintenance={!!node.maintenance_mode} /></div>}
      {(node.maintenance_mode || node.is_missing) && (
        <Card className="mb-4">
          <CardTitle>Decommission {node.name}</CardTitle>
          {(() => {
            const left = workloads.filter((w) => !w.is_missing).length;
            const steps: { done: boolean; label: React.ReactNode }[] = [
              { done: left === 0, label: left === 0 ? "Evacuate: no workloads are left on this host." : `Evacuate: ${left} workload${left === 1 ? " is" : "s are"} still on this host (use Evacuate / Maint Mode).` },
              {
                done: node.is_missing,
                label: node.is_missing
                  ? "Leave the cluster: PVE no longer lists this host."
                  : <>Leave the cluster: PVE still lists it. Power it off, then run <code>pvecm delnode {node.name}</code> on a remaining node (PyXie does not run this for you; it needs quorum).</>,
              },
            ];
            return (
              <ol className="text-sm space-y-1.5 list-decimal ml-5">
                {steps.map((st, i) => (
                  <li key={i}>
                    <span className={`text-[10px] font-semibold uppercase tracking-wide mr-2 ${st.done ? "text-good" : "text-warn"}`}>{st.done ? "Done" : "Next"}</span>
                    {st.label}
                  </li>
                ))}
                <li>
                  <span className={`text-[10px] font-semibold uppercase tracking-wide mr-2 ${node.is_missing ? "text-warn" : "text-muted"}`}>{node.is_missing ? "Next" : "Waiting"}</span>
                  Remove it from PyXie.{" "}
                  <RemoveHostButton nodeId={node.id} nodeName={node.name} stillMember={!node.is_missing} redirectTo="/infrastructure/nodes" />
                </li>
              </ol>
            );
          })()}
        </Card>
      )}

      <Card className="mb-4">
        <CardTitle>Recent Tasks</CardTitle>
        <div className="space-y-1 text-sm">
          {tasks.length === 0 && <div className="text-muted">No recent tasks.</div>}
          {tasks.slice(0, 8).map((t) => (
            <div key={t.id} className="flex justify-between">
              <span className="text-text">{t.task_type || t.upid.split(":")[1]}</span>
              <StatusBadge status={t.status || "unknown"} />
            </div>
          ))}
        </div>
      </Card>

      <Card className="mb-4">
        <CardTitle>Host Maintenance</CardTitle>
        {hostMaintenance ? <HostMaintenancePanel status={hostMaintenance} nodeId={params.id} /> : (
          <div className="text-sm text-muted py-2">Could not reach the readiness check.</div>
        )}
      </Card>

      <Card>
        <CardTitle>Workloads</CardTitle>
        <NodeWorkloadsTable workloads={workloads} />
      </Card>
      </>
      )}
      </div>
    </div>
  );
}

function HostMaintenancePanel({ status, nodeId }: { status: HostMaintenanceStatus; nodeId: string }) {
  if (!status.provisioned) {
    const reason = !status.credential_configured
      ? "no host-maintenance SSH credential configured for this cluster"
      : !status.management_ip_known
      ? "no management_ip on record yet -- run discovery"
      : !status.host_key_pinned
      ? "SSH host key not pinned yet for this node"
      : status.error || "not reachable";
    return (
      <div className="text-sm">
        <StatusBadge status="unknown" />
        <span className="text-muted ml-2">Not provisioned — {reason}</span>
      </div>
    );
  }

  return (
    <div>
      {status.reboot_required && (
        <a
          href={`/operations/maintenance?node=${nodeId}&action=host-reboots`}
          className="block mb-3 px-3 py-2 rounded border border-warn bg-warn/10 text-warn text-sm font-medium hover:bg-warn/20"
        >
          Reboot required — a newer kernel or other change is installed but not yet running. Click to go reboot this node →
        </a>
      )}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-4 text-sm">
        <Row label="Reachable" value={status.reachable ? "yes" : `no${status.error ? ` — ${status.error}` : ""}`} />
        <Row label="Wrapper version" value={status.wrapper_version || "—"} />
        <Row
          label="Contract version"
          value={
            status.contract_version != null
              ? `${status.contract_version}${status.contract_compatible ? "" : " (incompatible)"}`
              : "—"
          }
        />
        <Row label="Running kernel" value={status.kernel_version || "—"} />
        <Row label="Packages upgradable" value={status.upgradable_count != null ? String(status.upgradable_count) : "—"} />
        <Row label="Reboot required" value={status.reboot_required == null ? "—" : status.reboot_required ? "yes" : "no"} />
        <Row label="Disk free" value={status.disk_free_bytes != null ? formatBytes(status.disk_free_bytes) : "—"} />
        <Row label="Host key fingerprint" value={status.host_key_fingerprint || "—"} />
        <Row label="Host key pinned" value={status.host_key_pinned_at ? <LocalTime iso={status.host_key_pinned_at} /> : "—"} />
        <Row label="Capabilities" value={(status.capabilities || []).join(", ") || "—"} />
        <Row label="Status checked" value={status.status_checked_at ? <LocalTime iso={status.status_checked_at} /> : "—"} />
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between">
      <dt className="text-muted">{label}</dt>
      <dd className="text-text">{value}</dd>
    </div>
  );
}
