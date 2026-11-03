import Link from "next/link";
import PbsServer from "@/components/PbsServer";
import { PageHeader } from "@/components/Card";
import { ShieldIcon } from "@/components/Icons";

export default function BackupServerPage({ params }: { params: { id: string } }) {
  return (
    <div>
      <div className="text-xs text-muted mb-1"><Link href="/infrastructure/nodes" className="hover:text-text">Hosts &amp; Clusters</Link> &gt; Backup server</div>
      <PageHeader title="Backup server" subtitle="A Proxmox Backup Server, managed like a host: status, graphs, datastores, jobs, patching and access." icon={<ShieldIcon className="w-5 h-5" />} />
      <PbsServer id={params.id} />
    </div>
  );
}
