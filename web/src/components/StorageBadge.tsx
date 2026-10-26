import { formatBytes } from "@/lib/format";
import type { StorageInfo } from "@/lib/storagePlacement";

/** "shared NFS" or "local to st-pve106", so a storage name says what it means for moving the guest. */
export function StorageBadge({ info }: { info: StorageInfo }) {
  if (info.scope === "unknown") return null;
  const cls = info.scope === "shared" ? "bg-accent/15 text-accent" : "bg-warn/15 text-warn";
  return <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs ml-2 ${cls}`} title={info.note}>{info.label}</span>;
}

/** Free space with a thin used-space bar; nothing when Proxmox did not report a size. */
export function StorageFree({ info }: { info: StorageInfo }) {
  if (info.scope === "unknown" || info.freeBytes === null || info.capacityBytes === null) return null;
  const hot = (info.usedPct ?? 0) >= 90;
  return (
    <div className="mt-1 max-w-[12rem]" title={`${formatBytes(info.freeBytes)} free of ${formatBytes(info.capacityBytes)}`}>
      <div className="text-xs text-muted">{formatBytes(info.freeBytes)} free of {formatBytes(info.capacityBytes)}</div>
      <div className="h-1 rounded bg-surface2 overflow-hidden mt-0.5"><div className={`h-full ${hot ? "bg-bad" : "bg-accent"}`} style={{ width: `${info.usedPct ?? 0}%` }} /></div>
    </div>
  );
}
