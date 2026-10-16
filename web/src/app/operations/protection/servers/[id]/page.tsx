import Link from "next/link";
import PbsServer from "@/components/PbsServer";
import { PageHeader } from "@/components/Card";
import { ShieldIcon } from "@/components/Icons";

export default function PbsServerPage({ params }: { params: { id: string } }) {
  return (
    <div>
      <PageHeader title="Backup server" subtitle="A Proxmox Backup Server, managed like a host: status, jobs, patching and access." icon={<ShieldIcon className="w-5 h-5" />} />
      <div className="mb-3 text-xs"><Link href="/operations/protection" className="text-accent hover:underline">&larr; Protection</Link></div>
      <PbsServer id={params.id} />
    </div>
  );
}
