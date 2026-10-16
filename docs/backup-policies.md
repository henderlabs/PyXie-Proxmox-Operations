# Backup policies

A **backup policy** is a named bundle: where backups go (a Proxmox Backup Server or other backup datastore), **when** they run, the **mode**, and **how long to keep** them (retention).
PyXie turns each policy into one Proxmox backup job (named `pyxie-<policy>`) and keeps that job's VM list in step with who the policy applies to. Proxmox itself only has retention
per job, not per VM, so this is how PyXie gives you per-cluster, per-tag and per-VM retention without you maintaining a pile of jobs by hand.

**Where:** Operations > **Protection** (policies, assignments, every VM) and each workload's **Backup** tab (its policy, the jobs that cover it, its restore points).

## What a policy sets
| Setting | Meaning |
|---|---|
| Datastore | Where the backups go. Any datastore that holds backups; Proxmox Backup Server is the usual one. |
| When | A Proxmox calendar event: every day at a time, a weekday, a day of the month, or a custom expression such as `mon..fri 22:30`. The form builds it for you and shows the exact text. |
| Mode | **Snapshot** (default: a running VM is backed up without stopping), **Suspend**, or **Stop**. |
| Retention | Keep the last N, and one per hour, day, week, month or year for N periods. Backups are pruned after each run. Leaving all boxes empty keeps everything forever (the datastore will fill up), so the form warns. |
| Notes | Optional text added to every backup; Proxmox fills in `{{guestname}}`, `{{node}}`, `{{vmid}}`, `{{cluster}}`. |
| Enabled | A switched-off policy keeps its job but Proxmox does not run it. |

## Who gets which policy
Three levels; the **most specific wins**: **VM**, then **tag** (between tags the higher priority, then the policy name), then the **cluster** default. A VM can also be set to **No backup**
(an exemption that beats a tag or the cluster default). Changing the assignments changes the VM list of the policies' jobs: PyXie works out the exact Proxmox job changes and shows
them in the preview before anything is written.

## Safety
Every change is previewed (which jobs are created, updated or deleted, which VMs join or leave), approved, re-checked just before it runs, written, and **read back from Proxmox**.
PyXie's own records change only after Proxmox accepted the job changes. If a write fails halfway, the operation says what was done, and the page shows the difference as **drift** with a
**Preview sync** button. A tag added to a VM or a VM created after the last sync also shows as drift: PyXie never changes backup jobs silently.

**Other jobs.** A VM may already be in a job PyXie did not create (for example an "every guest" nightly job). By default the preview also takes the VM out of those jobs (adds it to the
job's exclude list, or removes it from the job's VM list) so it is not backed up twice; untick the box to leave them alone, and the preview then warns about each double backup. PyXie
never edits a job's schedule or retention unless it created the job.

Deleting a policy deletes its job. Backups already taken stay on the datastore and are kept or pruned by whatever other job or datastore setting still applies.

## Privileges
Backup jobs need `VM.Backup` (put VMs into jobs), `Datastore.AllocateSpace` on the backup datastore and `Sys.Modify`. If one is missing the preview names it and shows the exact `pveum` command:
`pveum role modify PyXieAdmin --privs VM.Backup --append 1`.

## Not covered
Restoring a backup is done in Proxmox (VM > Backup) or Proxmox Backup Server, and the datastore-side prune and verify jobs of Proxmox Backup Server are managed there.
