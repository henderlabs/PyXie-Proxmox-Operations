# Protection: the 3-2-1 scorecard

**Protection** answers one question per guest: does it have **3 copies** of its data, on **2 kinds of storage**, with **1 copy offsite**?

Proxmox VE and Proxmox Backup Server (PBS) run the backups. PyXie reads what they did and scores it. Nothing on this page changes a schedule, a retention rule or a datastore.

## What the page shows

- **Totals**: guests that meet 3-2-1, guests that are partly protected, and guests with no recent backup.
- **Where the copies live**: the live guest plus each PBS datastore, with how many guests and backups it holds, its newest backup, what it keeps (the PBS prune job), whether a verify job checks it, and for offsite stores the sync job that feeds it. Warnings appear here, for example "no verify job: backups are never checked for corruption". "Open in PBS" jumps to the datastore.
- **Every guest, gaps first**: the age of the newest backup on each datastore, which backup job covers the guest, and what is missing.
- **Backup jobs**: the Proxmox backup jobs that write to PBS, with "Open in Proxmox". Schedule, mode and retention are edited there.
- **Which guests a job backs up**: the one thing PyXie edits. Add or remove a guest, switch a job to all guests, or use "Add every guest tagged ..." to tick all guests with a tag at once. Every change uses the Safety Contract: preview, approve, revalidate, write, read back, audit. Tag adds are one-time; they are not a standing rule, so a guest tagged later is not added automatically.

## How a copy is scored

- A datastore is **offsite** when it is S3-backed or its name contains "offsite".
- A backup counts when it is under 48 hours old. An offsite copy counts while it is within what its feeding job's schedule allows: 35 days for a monthly job, 9 days for a weekly one, 48 hours for a daily one. PyXie takes the schedule from the datastore's sync job, or its prune job if no sync job is visible.
- 3-2-1 is met when there are at least 3 copies (the live guest plus two fresh backups), at least 2 kinds of storage (for example disk and S3), and a fresh offsite copy.

A Proxmox Backup Server is managed from this page too (status, jobs, patching, access): see [adding-a-pbs.md](adding-a-pbs.md).

## What PyXie needs

Read access to PBS through the "inventory" token on the PBS target (Audit on the datastores and the prune, verify and sync jobs; without Remote.Audit PBS hides sync jobs that pull from a remote, and the page then says no sync job is visible), and the Proxmox privileges listed in [adding-a-host.md](adding-a-host.md).

## Earlier versions

v0.37.0 had a backup policy builder (named bundles of schedule, retention and mode that PyXie turned into jobs). It duplicated what Proxmox and PBS already own, so v0.37.1 removed it from the UI. Jobs it created are named `pyxie-...` and keep working; edit or delete them in Proxmox.
