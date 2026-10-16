import { PageHeader } from "@/components/Card";
import PoolsManager from "@/components/PoolsManager";
import { WorkloadIcon } from "@/components/Icons";

export default function PoolsPage() {
  return (
    <div>
      <PageHeader title="Pools" subtitle="Workloads · Proxmox resource pools and their guests" icon={<WorkloadIcon className="w-5 h-5" />} />
      <PoolsManager />
    </div>
  );
}
