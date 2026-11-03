# Change a guest's settings from PyXie (the guest's Config tab)

Open **Workloads**, click a VM or container, then **Config**. Settings that already have their own place stay there (memory and CPU count under **Resize**, CPU type, machine type and firmware under **Hardware**, disks, network adapters, CD/ISO and tags); the Config tab shows them read-only with a pointer to where they are edited.

## What can be changed

| Group | VM | Container |
|---|---|---|
| **General** | name, description, start at boot, start order and delays, protection (blocks delete and disk removal), OS type\*, QEMU guest agent\* | hostname\*, description, start at boot, start order and delays, protection, time zone\* |
| **CPU** | CPU limit (0 = none), CPU weight, NUMA\* | CPU limit, CPU weight |
| **Memory** | ballooning shares (0 = off) | swap (MB) |
| **Boot, firmware and hardware** | display\*, USB tablet pointer\*, keyboard layout\*, clock uses local time\*, ACPI\*, KVM hardware virtualization\*, start date\*, hotplug\*, boot order\* | - |
| **Migration** | maximum downtime (seconds), speed limit (MB/s) | - |
| **DNS / Console** | - | DNS servers\*, search domain\*, console mode\*, number of ttys\*, attach a console device\* |

\* applies the next time the guest restarts. Everything else applies at once. The preview says which is which for your change.

Things that are root-only on Proxmox (hook scripts, extra QEMU arguments, device passthrough) are not editable here.

## How a change runs

1. Edit the fields and press **Preview**. PyXie reads the guest's live configuration, checks every value against Proxmox's own limits and shows *old, new* for each setting, plus whether it applies live or after a restart.
2. **Approve.** PyXie re-checks that the configuration has not changed behind its back (Proxmox's configuration digest) and that the guest is not locked, writes the change in one call, then reads it back and verifies every setting.
3. A change that waits for a restart stays **pending**; you can **discard pending changes** and Proxmox's own revert removes them.

A guest with a lock (a backup or migration in progress) cannot be edited until the lock goes; the preview says why.

## Change one setting on many guests

On the Workloads list, tick several guests of the same kind (VMs or containers) and use the change-a-setting action to apply **one setting** to all of them (tags have their own bulk action). PyXie previews each guest separately; only the ones that are ready run when you approve.

## Related

- Resizing memory and CPU, disks, snapshots, clones and delete: [guest-management.md](guest-management.md).
- Moving a guest's disks to another storage: [storage-change.md](storage-change.md).
