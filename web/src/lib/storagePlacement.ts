import type { StorageItem } from "@/lib/api";

export type StorageInfo = {
  scope: "shared" | "local" | "unknown";
  /** "shared NFS" or "local to st-pve106"; empty when the storage is not known. */
  label: string;
  /** What that means for moving the guest. */
  note: string;
  freeBytes: number | null;
  capacityBytes: number | null;
  usedPct: number | null;
};

const TYPE_LABEL: Record<string, string> = {
  nfs: "NFS", cifs: "SMB", lvmthin: "LVM-thin", lvm: "LVM", dir: "directory", zfspool: "ZFS", zfs: "ZFS", rbd: "Ceph", cephfs: "CephFS", iscsi: "iSCSI",
};

const UNKNOWN: StorageInfo = { scope: "unknown", label: "", note: "", freeBytes: null, capacityBytes: null, usedPct: null };

/** Whether a storage name is shared by every host or local to the guest's own host, and how much room it has.
 *  Storage names repeat across hosts (every host has local-lvm), so a local storage only counts when it is this guest's host's own. */
export function storageInfo(name: string, items: StorageItem[], clusterId: string, nodeId: string, nodeName: string): StorageInfo {
  const matches = items.filter((s) => !s.is_missing && s.cluster_id === clusterId && s.name === name);
  const shared = matches.find((s) => s.scope !== "node-local");
  const local = matches.find((s) => s.scope === "node-local" && s.node_id === nodeId);
  const s = shared ?? local;
  if (!s) return UNKNOWN;
  const type = s.type ? TYPE_LABEL[s.type] ?? s.type : null;
  const capacity = s.capacity_bytes ?? null;
  const free = capacity === null ? null : Math.max(capacity - (s.used_bytes ?? 0), 0);
  const usedPct = capacity ? Math.min(100, Math.round(((capacity - (free ?? 0)) / capacity) * 100)) : null;
  return shared
    ? { scope: "shared", label: `shared${type ? ` ${type}` : ""}`, note: "Any host can run this guest without copying its disk.", freeBytes: free, capacityBytes: capacity, usedPct }
    : { scope: "local", label: `local to ${nodeName || "this host"}`, note: `${type ? `${type}: ` : ""}the disk lives on this host only, so moving the guest to another host copies it.`, freeBytes: free, capacityBytes: capacity, usedPct };
}
