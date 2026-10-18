# Protection: the 3-2-1 scorecard

**Protection** answers one question per guest: does it have **3 copies** of its data, on **2 kinds of storage**, with **1 copy offsite**?

Proxmox VE and Proxmox Backup Server (PBS) run the backups. PyXie reads what they did and scores it. Nothing on this page changes a schedule, a retention rule or a datastore.

## The pages

- **Backup plan & guests** (the first tab) is one page: the totals (guests that meet 3-2-1, partly protected, not protected), the **backup plan** in plain words (the default plan and any exceptions, each with Edit and a live diagram in its form; see [retention-policies.md](retention-policies.md)) and the **guests**. Every guest row has a **Plan** drop-down (*Automatic: <plan>*, *No backup*, or any plan); a change shows a preview of every job it touches before anything happens. Guests that need attention open by themselves (up to five) so the problem and the restore points are in front of you; open any other guest the same way. Search by name, ID, plan, result or what is missing.
- **Backup servers** (the second tab): the backup servers, then one box per datastore with labelled rows as pills: *Used by plans* (click a plan name to open its settings), *Pruning* (the keep rules, schedule and last run of each prune job, editable), *Freeing space* (garbage collection schedule, last run, edit and run now), *Verifying*, and *Copies arriving here*. A last box lists the Proxmox backup jobs with the plan that manages each and the retention each prunes to. A plan sets all of this for you; use this page to see or adjust what exists.
- **Storage > Backup namespaces**: folders inside a datastore that keep different retention apart ([backup-namespaces.md](backup-namespaces.md)).

## What the guest table says is missing

Each line names the place and the limit, for example *2-NAS_ST-Archive: newest backup is 3d old, should be under 2 days*, *3-OFFSITE_Wasabi (offsite): newest backup is 40d old, should be under 35 days* or *no backup yet*. A late **offsite** copy is judged against its sync job's schedule (35 days for monthly), a local one against 48 hours.

## Storage used

The space the backups really take (identical data is stored once) is shown on four pages: a tile on **Protection**, a full card with a 30 day trend, guest and backup counts, the estimated full date and the last garbage collection on **Backup servers**, and the same card on **Capacity** and on **Storage > Datastores**. A disk datastore shows used and free space and how full it is. Wasabi (S3) has no fixed capacity, so it shows what is stored, which is what you are billed for. Pruned backups give their space back only after garbage collection runs. There is no per-guest figure: backups share data, so one guest's share cannot be stated honestly.

## What is wrong, and why

The last column lists what is missing and, when PyXie already knows it, **why** (a red line starting *Why:*): the guest is powered off, backups of it failed in the last 7 days (with the count, the last time and the reason in words, for example *the backup ended without finishing, often a dropped connection, the backup server restarting, or a very busy guest*), no backup is chosen for the guest, or no plan applies to it.

## Restore points: what pruning will do

Under each guest every restore point has a **Retention** chip, worked out the way Proxmox Backup Server decides (newest first, the keep rules in order, each period keeping its newest backup): *kept · daily* (or weekly, monthly, yearly, newest) means a keep rule holds it, *will be pruned* means the next prune run deletes it and the next garbage collection frees its space (the tooltip names the job that does it, when it runs and when GC runs), *protected* means it is never pruned, *never pruned* means no rule applies. When a Proxmox job's retention and a datastore prune job both apply, a backup survives only if both keep it. The **Verified** chip shows whether the backup server has re-read the backup and matched its checksums.

## How a copy is scored

- A copy that lives in a **namespace** still counts as a copy on its datastore.
- A datastore is **offsite** when it is S3-backed or its name contains "offsite".
- A backup counts when it is under 48 hours old. An offsite copy counts while it is within what its feeding job's schedule allows: 35 days for a monthly job, 9 days for a weekly one, 48 hours for a daily one. PyXie takes the schedule from the datastore's sync job, or its prune job if no sync job is visible.
- 3-2-1 is met when there are at least 3 copies (the live guest plus two fresh backups), at least 2 kinds of storage (for example disk and S3), and a fresh offsite copy.
- **Disks count too.** A disk with *Backup* turned off (`backup=0` on the disk line) is never saved by Proxmox. PyXie looks in the backups for the newest one that really holds that disk:
  - **Guest running, no backup holds the disk:** Partly protected. A restore gives a guest with empty disks. Turn backup on for the disk (Hardware tab).
  - **Guest running, an older backup holds the disk:** Partly protected. Changes since that backup are not saved.
  - **Guest powered off, an earlier backup holds the disk:** stays as the copies say, with a note naming the last complete backup. PyXie never powers a guest on for a backup (it may be off for a reason); a stopped guest stays in its plan and is backed up as it is.
  - **Guest powered off, no backup holds the disk:** Partly protected, same as running.
  - **Left out on purpose:** use the link on the guest's row. The guest then keeps its result and the row says so. The link only records the decision in PyXie; nothing changes in Proxmox, and you can withdraw it.
  Counts on the Dashboard and the Protection page come from the same calculation, over current guests only.
- **From the Workloads page.** The Workloads list has a **Backup** column (the plan the guest follows, the same setting as the drop-down on this page, and how many of its disks are left out; you can sort and search it). A guest's **Backup** tab lists its disks with *Backed up* or *Left out*, a *Mark on purpose* link, and the plan drop-down; its **Hardware** tab shows *In backups* or *Left out of backups* beside each disk, and clicking it opens the usual preview to change the disk's *Include in backups* setting. When a guest's plan is *No backup*, its disks show *Not backed up (no plan)*.
- 3-2-1 describes copies and their age. It does not prove that a backup can be restored; use *Verify* and a test restore for that.

A Proxmox Backup Server is managed from this page too (status, jobs, patching, access): see [adding-a-pbs.md](adding-a-pbs.md).

## What PyXie needs

Read access to PBS through the "inventory" token on the PBS target (Audit on the datastores and the prune, verify and sync jobs; without Remote.Audit PBS hides sync jobs that pull from a remote, and the page then says no sync job is visible), and the Proxmox privileges listed in [adding-a-host.md](adding-a-host.md).

## Earlier versions

v0.37.0 had a backup policy builder, v0.37.1 removed its UI, and it returns here as **Retention policies**, rebuilt around copies, namespaces and one default policy. Jobs named `pyxie-...` belong to policies; do not edit them by hand (a policy's preview shows what would be put back).
