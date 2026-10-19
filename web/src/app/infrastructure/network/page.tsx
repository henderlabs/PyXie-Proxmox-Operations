import { apiFetch } from "@/lib/api";
import type { NetworkLayout, NetworkSdnStatus, NodeNetworkTopology, VlanSegment, WorkloadNics } from "@/lib/api";
import { Card, CardTitle, PageHeader } from "@/components/Card";
import VlanOverview from "@/components/VlanOverview";
import NetworkTopology from "@/components/NetworkTopology";
import HostLayouts from "@/components/HostLayouts";
import WorkloadNicsTable from "@/components/tables/WorkloadNicsTable";
import { NetworkIcon } from "@/components/Icons";

export default async function NetworkPage() {
  const [topology, nics, vlans, sdn, layouts] = await Promise.all([
    apiFetch<NodeNetworkTopology[]>("/api/network/topology"),
    apiFetch<WorkloadNics[]>("/api/network/nics"),
    apiFetch<VlanSegment[]>("/api/network/vlans").catch(() => [] as VlanSegment[]),
    apiFetch<NetworkSdnStatus[]>("/api/network/sdn").catch(() => [] as NetworkSdnStatus[]),
    apiFetch<NetworkLayout[]>("/api/network/layout").catch(() => [] as NetworkLayout[]),
  ]);

  return (
    <div>
      <PageHeader
        title="Network"
        subtitle="Host bridges/VLANs are read-only here. Reassigning which VLAN a workload's NIC is on goes through the normal preview-and-approve flow."
        icon={<NetworkIcon className="w-5 h-5" />}
      />
      <Card className="mb-4">
        <CardTitle>Networks in use</CardTitle>
        <VlanOverview segments={vlans} sdn={sdn} />
      </Card>
      <Card className="mb-4">
        <CardTitle>Hosts and links</CardTitle>
        <HostLayouts layouts={layouts} />
        <details className="mt-3">
          <summary className="text-xs text-accent cursor-pointer">Every interface on every host</summary>
          <div className="mt-2"><NetworkTopology topology={topology} /></div>
        </details>
      </Card>
      <Card>
        <CardTitle>Workload NICs</CardTitle>
        <WorkloadNicsTable workloads={nics} />
      </Card>
    </div>
  );
}
