import { apiFetch } from "@/lib/api";
import type { StorageDefinition, StorageItem } from "@/lib/api";
import { PageHeader, StatTile } from "@/components/Card";
import StorageTable from "@/components/tables/StorageTable";
import BackupStorage from "@/components/BackupStorage";
import { StorageIcon } from "@/components/Icons";
import { formatBytes } from "@/lib/format";

export default async function DatastoresPage() {
  const [storage, defs, nodes] = await Promise.all([
    apiFetch<StorageItem[]>("/api/storage"),
    apiFetch<StorageDefinition[]>("/api/storage-setup/definitions").catch(() => [] as StorageDefinition[]),
    apiFetch<{ id: string; name: string }[]>("/api/nodes").catch(() => [] as { id: string; name: string }[]),
  ]);

  // Excludes is_missing -- a storage entry PVE no longer reports isn't real capacity.
  const present = storage.filter((s) => !s.is_missing);
  const totalCapacity = present.reduce((sum, s) => sum + (s.capacity_bytes ?? 0), 0);
  const totalUsed = present.reduce((sum, s) => sum + (s.used_bytes ?? 0), 0);
  const isoStores = defs.filter((d) => d.content.includes("iso") && !d.disabled).length;

  return (
    <div>
      <PageHeader title="Datastores" subtitle="Storage · every datastore, what it holds and where it lives. Datastores that hold ISO images are marked; manage those under ISO library." icon={<StorageIcon className="w-5 h-5" />} />
      <div className="grid grid-cols-5 gap-3 mb-5">
        <StatTile label="Datastores" value={present.length} />
        <StatTile label="ISO datastores" value={isoStores} />
        <StatTile label="Total Capacity" value={formatBytes(totalCapacity)} />
        <StatTile label="Total Used" value={formatBytes(totalUsed)} />
        <StatTile label="Free" value={formatBytes(totalCapacity - totalUsed)} />
      </div>
      <BackupStorage title="Backup server datastores" />
      <StorageTable storage={storage} defs={defs} nodeNames={Object.fromEntries(nodes.map((n) => [n.id, n.name]))} />
    </div>
  );
}
