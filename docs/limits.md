# What PyXie does not do (yet)

PyXie is an operations console for Proxmox. This page says plainly where it stops, so nobody assumes more than is there. Findings in the v0.41.0 user-interface review that are limits and not defects are recorded here.

## Backups and recovery

- **A backup is checked by checksum, not by booting it.** *Verify* makes the backup server re-read a backup and match its checksums. PyXie does not boot a restored guest in isolation or run an application check, and it keeps no history of test restores. Do a real test restore (to a new guest, network disconnected, which is the default) on a schedule that suits you.
- **"3-2-1 copies met" describes copies and their age**, not recoverability. It also looks at each guest's disks: a disk with backup turned off makes the guest partly protected.
- **Recovery point objective.** The target PyXie uses is the schedule of the plan: a backup older than its schedule allows (48 hours for a nightly job, 9 days for weekly, 35 days for monthly copies) makes the guest partly protected. There is no separate per-service target, breach history or report.
- **Copies live on one backup server.** A plan can copy to another datastore of the same Proxmox Backup Server (including S3), and a namespace is not a separate repository. Copying to a second backup server is not built, and PyXie does not check object lock or immutability on S3, so it cannot tell you that a copy survives the loss or compromise of the backup server or its credentials.
- **Restore.** Guests that PyXie knew are restored from the Removed guests list; guests it never saw are listed but restored from Proxmox. File-level and application-item restore are done in Proxmox Backup Server.
- **Replication and orchestrated disaster recovery** (replicas, boot order, network mapping, failback) are not part of PyXie. A monthly copy to Wasabi is a backup, not a replica.

## Hosts and clusters

- **High availability.** The Capacity page has an *If a host fails* table: memory only, a calculation and not a failover test. It does not model CPU, storage or HA rules. Proxmox restarts a failed host's guests by itself only when they are managed by HA; the table shows how many are.
- **Live migration and maintenance** are previewed and checked, but PyXie does not simulate a migration failing half way.

## Roles

There are two roles, Admin and Viewer. The server enforces them, not just the buttons: every one of the 128 API routes that changes something requires an Admin. The only changes open to a Viewer are signing in and out, editing their own profile, and sending feedback; a handful of read-only pages that expose set-up detail (users, update status, the host kit script, storage scans and pools) are Admin-only too. There are no per-pool, per-site or per-team permissions, no delegated approval, and no separation of duties between the person who previews and the person who approves.

## Reports

The Reporting page exports reports as Excel or CSV on demand. There are no scheduled or emailed reports and no compliance report. Times in exports are UTC.
