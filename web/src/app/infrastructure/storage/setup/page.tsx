import { PageHeader } from "@/components/Card";
import StorageSetup from "@/components/StorageSetup";
import { StorageIcon } from "@/components/Icons";

export default function StorageSetupPage() {
  return (
    <div>
      <PageHeader title="Storage setup" subtitle="Storage · add a datastore (NFS, iSCSI, directory, LVM / Fibre Channel, LVM-thin, ZFS) or remove a definition" icon={<StorageIcon className="w-5 h-5" />} />
      <StorageSetup />
    </div>
  );
}
