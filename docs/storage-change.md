# Changing a VM's storage

**Workloads > (a VM) > Change storage…** moves a VM's disks to another storage on its host (Proxmox "move disk"), one disk at a time, while the guest keeps running. It is a normal PyXie operation: preview, approve, run, verify.

1. **Choose the storage.** The list shows the cluster's shared storages and the host's own local storages, with free space.
2. **Preview.** PyXie checks, against Proxmox's own list for that host, that the storage is active, accepts VM disks and has room for the disks plus a 10% margin. It lists the disks that will move (disks already on the chosen storage, CD-ROMs and empty drives are skipped). A target that fails a check blocks the move (rule `SAFE-STORAGE-002`) and says why.
3. **Approve.** Each disk is copied with Proxmox's move disk; the old copy is deleted by Proxmox only after it reports the copy finished, so a failed move leaves the VM exactly as it was.
4. **Watch.** The Tasks panel and the workload page show a disk-copy progress bar (all disks summed, with a rate). The VM stays up; copying a large disk is limited by the storage and the network. A size-based time limit applies to each disk.
5. **Verify.** PyXie reads the VM's config back from Proxmox and checks each moved disk is on the chosen storage, then refreshes its inventory.

Notes: VMs only (not containers). It needs the writes switch on and the maintenance credential. Moving the VM PyXie itself runs on is allowed: PyXie keeps running while the copy happens. Balance Load's storage rules (a guest on shared storage stays on it; a local guest goes to the destination host's preferred storage) decide storage for *migrations*; this action is the manual way to change it afterwards. API: `POST /api/operations/workload-storage-moves/dry-run` (`workload_id`, `target_storage`), then the usual `/approve`. Operation type `workload.move_storage` (migration 0052).
