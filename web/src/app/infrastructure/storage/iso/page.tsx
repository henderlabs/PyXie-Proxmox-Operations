import { PageHeader } from "@/components/Card";
import IsoLibrary from "@/components/IsoLibrary";
import { StorageIcon } from "@/components/Icons";

export default function IsoPage() {
  return (
    <div>
      <PageHeader title="ISO library" subtitle="Storage · installation media on your Proxmox storages: add by URL, delete, switch ISO images on for a storage" icon={<StorageIcon className="w-5 h-5" />} />
      <IsoLibrary />
    </div>
  );
}
