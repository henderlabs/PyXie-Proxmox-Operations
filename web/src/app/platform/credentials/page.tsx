import { apiFetch } from "@/lib/api";
import type { Cluster, Credential, HostMaintenanceCredentialRecord, Node, PveTarget } from "@/lib/api";
import { Card, CardTitle, EmptyState, PageHeader } from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import ActionButton from "@/components/ActionButton";
import HostMaintenanceSetup from "@/components/HostMaintenanceSetup";
import AddCredentialForm from "@/components/AddCredentialForm";
import EditCredentialForm from "@/components/EditCredentialForm";
import DeleteCredentialButton from "@/components/DeleteCredentialButton";
import PbsCredentials from "@/components/PbsCredentials";
import { HostWrapperBuilder, PveAccountsBuilder } from "@/components/HostSetupBuilder";
import type { SetupStatus } from "@/components/SetupGuide";
import { KeyIcon } from "@/components/Icons";
import Link from "next/link";

export default async function CredentialsPage() {
  const [targets, clusters, nodes, setupStatus] = await Promise.all([
    apiFetch<PveTarget[]>("/api/pve-targets"),
    apiFetch<Cluster[]>("/api/clusters"),
    apiFetch<Node[]>("/api/nodes"),
    apiFetch<SetupStatus>("/api/setup/status").catch(() => null),
  ]);
  const tlsMode = setupStatus?.tls_mode ?? "";
  const credentialsByTarget = await Promise.all(
    targets.map((t) => apiFetch<Credential[]>(`/api/pve-targets/${t.id}/credentials`))
  );
  const hostMaintCredByTarget = await Promise.all(
    targets.map((t) =>
      apiFetch<HostMaintenanceCredentialRecord | null>(`/api/pve-targets/${t.id}/host-maintenance-credential`).catch(() => null)
    )
  );

  const clusterIdsByTarget = (targetId: string) => clusters.filter((c) => c.pve_target_id === targetId).map((c) => c.id);
  const nodesForTarget = (targetId: string) => {
    const clusterIds = new Set(clusterIdsByTarget(targetId));
    return nodes.filter((n) => clusterIds.has(n.cluster_id));
  };

  return (
    <div>
      <PageHeader title="Credentials" subtitle="API tokens per access level, for Proxmox VE clusters and Proxmox Backup Servers: secret material is never redisplayed" icon={<KeyIcon className="w-5 h-5" />} />
      {targets.length === 0 && (
        <Card>
          <EmptyState
            message={
              <>
                No PVE targets configured yet.{" "}
                <Link href="/platform/providers#targets" className="text-accent hover:underline">
                  Connect your cluster in the Setup guide
                </Link>
                .
              </>
            }
          />
        </Card>
      )}
      <div className="text-xs font-semibold uppercase tracking-wider text-muted mb-2">Proxmox VE (clusters)</div>
      <Card className="mb-4" id="builder-accounts">
        <details>
          <summary className="cursor-pointer select-none text-sm font-medium text-text">Create the Proxmox accounts (script you run on a node)</summary>
          <div className="mt-3"><PveAccountsBuilder /></div>
        </details>
      </Card>
      {targets.map((t, i) => (
        <Card key={t.id} className="mb-4">
          <CardTitle><span className="mr-2 px-1.5 py-0.5 rounded bg-proxmox/15 text-proxmox normal-case tracking-normal">PVE</span>{t.name}</CardTitle>
          <div className="divide-y divide-border mb-4">
            {credentialsByTarget[i].map((c) => (
              <div key={c.id} className="py-2 text-sm">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-text font-medium">{c.slot_name}</div>
                    <div className="text-xs text-muted">
                      {c.token_user}!{c.token_id}
                    </div>
                  </div>
                  <div className="text-xs text-muted font-mono">{c.masked_secret}</div>
                  <StatusBadge status={c.status} />
                  <div className="text-xs text-muted">
                    {c.last_validated_at ? `validated ${new Date(c.last_validated_at).toLocaleString()}` : "never validated"}
                  </div>
                  <ActionButton href={`/api/pve-targets/${t.id}/credentials/${c.id}/test-connection`} label="Test Connection" />
                </div>
                <div className="flex items-start gap-3">
                  <EditCredentialForm targetId={t.id} credential={c} />
                  <DeleteCredentialButton targetId={t.id} credentialId={c.id} slotName={c.slot_name} />
                </div>
              </div>
            ))}
          </div>

          <div className="divide-y divide-border mb-4 -mt-2">
            <div className="py-2 text-sm">
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-text font-medium">ssh (host patching)</div>
                  <div className="text-xs text-muted">{hostMaintCredByTarget[i] ? `${hostMaintCredByTarget[i]!.ssh_username} · key created ${new Date(hostMaintCredByTarget[i]!.created_at).toLocaleDateString()}` : "no key generated yet"}</div>
                </div>
                <div className="text-xs text-muted font-mono">{hostMaintCredByTarget[i] ? "SSH key (private half stored encrypted)" : ""}</div>
                {hostMaintCredByTarget[i] && <StatusBadge status={hostMaintCredByTarget[i]!.status} />}
                <div className="text-xs text-muted">{nodesForTarget(t.id).length} nodes · set up below under Host Maintenance</div>
              </div>
              <div className="text-xs text-muted">Installs package updates through the restricted pyxie-maint wrapper. The Proxmox API can only list updates, not install them.</div>
            </div>
          </div>

          <div className="mb-4">
            <AddCredentialForm targetId={t.id} existingSlots={credentialsByTarget[i].map((c) => c.slot_name)} />
          </div>

          <div className="border-t border-border pt-3">
            <div className="text-xs font-semibold uppercase tracking-wider text-proxmox mb-2">
              Host Maintenance — Connect a Host
            </div>
            <HostMaintenanceSetup targetId={t.id} nodes={nodesForTarget(t.id)} initialCredential={hostMaintCredByTarget[i]} />
            <details className="mt-3" id={i === 0 ? "builder-host" : undefined}>
              <summary className="cursor-pointer select-none text-sm font-medium text-text">Install or remove the host wrapper (script you run on each node)</summary>
              <div className="mt-3"><HostWrapperBuilder targets={[{ id: t.id, name: t.name }]} tlsMode={tlsMode} /></div>
            </details>
          </div>
        </Card>
      ))}
      <div className="text-xs font-semibold uppercase tracking-wider text-muted mb-2 mt-6">Proxmox Backup Servers</div>
      <PbsCredentials tlsMode={tlsMode} />
    </div>
  );
}
