import { apiFetch } from "@/lib/api";
import type { NodeNetworkTopology, VlanSegment, WorkloadNics } from "@/lib/api";
import { Card, CardTitle, PageHeader } from "@/components/Card";
import VlanOverview from "@/components/VlanOverview";
import NetworkTopology from "@/components/NetworkTopology";
import WorkloadNicsTable from "@/components/tables/WorkloadNicsTable";
import { NetworkIcon } from "@/components/Icons";

export default async function NetworkPage() {
  const [topology, nics, vlans] = await Promise.all([
    apiFetch<NodeNetworkTopology[]>("/api/network/topology"),
    apiFetch<WorkloadNics[]>("/api/network/nics"),
    apiFetch<VlanSegment[]>("/api/network/vlans").catch(() => [] as VlanSegment[]),
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
        <VlanOverview segments={vlans} />
      </Card>
      <Card className="mb-4">
        <CardTitle>Topology</CardTitle>
        <NetworkTopology topology={topology} />
      </Card>
      <Card>
        <CardTitle>Workload NICs</CardTitle>
        <WorkloadNicsTable workloads={nics} />
      </Card>
    </div>
  );
}
