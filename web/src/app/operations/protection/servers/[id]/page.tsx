import ProtectionTabs from "@/components/ProtectionTabs";
import PbsServer from "@/components/PbsServer";
import { PageHeader } from "@/components/Card";
import { ShieldIcon } from "@/components/Icons";

export default function PbsServerPage({ params }: { params: { id: string } }) {
  return (
    <div>
      <ProtectionTabs />
      <PageHeader title="Backup server" subtitle="A Proxmox Backup Server, managed like a host: status, jobs, patching and access." icon={<ShieldIcon className="w-5 h-5" />} />
      <PbsServer id={params.id} />
    </div>
  );
}
