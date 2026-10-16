# Patching a node through PyXie

The procedure used to patch the CRE cluster one node at a time (m503 and
m401 first, 2026-10-02). Every step is previewed, then approved by a person;
nothing here runs unattended. Read
[`adding-a-host.md`](adding-a-host.md) first: the node must already have the
patching wrapper installed and its host key pinned.

## Before you start

1. **One node at a time.** Check that the remaining nodes can absorb the
   node's running RAM with room to spare (the preview shows where each VM
   would land and each destination's score).
2. **Nothing else in flight.** The Tasks panel should show nothing running
   or awaiting approval.
3. **Look for VMs that will not migrate.** A VM whose QEMU control socket
   is dead (it reports `running` but does not answer) hangs a live
   migration. Check the guest agent / status of each VM first; plan to skip
   or stop such VMs by hand and handle them separately.
4. **Know your keep-apart VMs.** Affinity rules (Affinity Rules page) are
   enforced when plans are built (v0.18.3+). Databases should have a rule;
   see below.
5. **Check the PVE-writes switch** (Settings) is on.

## The actions (Maintenance page, Nodes card)

There is no manual maintenance mode to enter first. Four actions, each previewed and approved:

1. **Check for Updates.** A look: runs `apt-get update` through the wrapper and lists what is waiting. Changes nothing else.
2. **Apply Updates.** Checks, then applies automatically if the check is clean (full `dist-upgrade` through the wrapper, about 6 minutes for ~270 packages). It only needs the node to be **idle**: no backup, migration or other task running on it. Running guests keep running through an apt upgrade. A new kernel installs but only takes effect at the reboot, which the row flags as *Reboot Required*.
3. **Reboot Node.** Needs the node idle **and empty of running guests**, and the cluster to keep quorum. If guests are running the preview says so and points you to Full Maintenance. Expect the node offline for about 3 minutes; watch quorum, that it returns on the new kernel, that storages are active and PVE services running.
4. **Full Maintenance.** The whole cycle in one run, each step still reviewed and approved: evacuate the node (live-migrate what can move, shut down in place what cannot), check and apply updates, reboot, bring it back into service, then either restore the VMs to it or leave it empty. VMs move one at a time, smallest first, roughly 45-60 s each; the run stops at the first failure. During the run the node is marked as in maintenance mode.

A node a Full Maintenance run left in maintenance mode (for example after a failure) shows an **Exit Maintenance Mode** button; it restarts what the run shut down and marks the node available again (it does not move anything back).

Backup servers (Proxmox Backup Server) are in the same table: Check for Updates and Apply Updates work on them too, with the same idle rule (no backup, sync, prune, verify or garbage collection running). PyXie never reboots a backup server: *Reboot Required* only tells you.

Balance Load (its own page) proposes moves cluster-wide and favours the largest VMs; review it and do not approve moves you would not make by hand.

## When a VM is locked

The preview lists any VM that has a PVE `lock` as blocked, and the plan
cannot be approved until it is cleared. A `backup` lock clears itself when
the backup finishes: wait, then preview again. A `snapshot`,
`snapshot-delete` or `rollback` lock that does not clear usually means an
interrupted snapshot operation left it behind (for example a backup
product's snapshot that was removed from the disk but never from the VM's
config). On the VM's node, as root:

1. Make sure nothing is working on it: there should be no active task for
   that VM (Node > Tasks, or `pvesh get /nodes/<node>/tasks --source active`).
2. If the snapshot is still listed in `qm listsnapshot <vmid>`, check
   whether it really exists in the disk file:
   `qemu-img snapshot -l -U /mnt/pve/<storage>/images/<vmid>/<disk>.qcow2`
   (`-U` allows reading a disk that is in use). An empty list means the
   entry in the config is stale.
3. Clear the lock and remove the stale entry in one go. A failed delete
   re-takes the lock, so unlock immediately before it:
   `qm unlock <vmid> && qm delsnapshot <vmid> <snapname> --force`
   (`--force` only drops the config entry and is safe once step 2 shows the
   disk has no such snapshot; if the disk still has the snapshot, run
   `qm delsnapshot` without `--force` first so it is merged).
4. Check `qm listsnapshot <vmid>` and `qm config <vmid> | grep -E "lock|parent"`
   are clean, then preview again.

## Things to know

- **A failed operation cannot be resumed.** Start a new one (for example,
  re-run Full Maintenance: already-moved VMs are simply no longer on
  the node).
- **Keep databases apart.** Create a keep-apart rule per pair (workload
  pair) or tag the VMs and use one tag-group rule. Hard block forbids
  sharing a host; soft only applies a scoring penalty and will not stop a
  plan when one host is far emptier. Rules restrict moves PyXie plans; they
  do not move VMs that already share a host, and they are not checked
  against moves made directly in the PVE console.
- **Ballooning changes need a power-cycle** through PVE to take effect (not
  a restart from inside the guest); see README, "Memory readings and
  ballooning".
- **A mixed-version cluster is normal mid-rollout.** The nodes stay in the
  same cluster while you patch them one by one.

## What to watch while it runs

The Tasks panel shows each step with its start time and who started it.
For a deeper check: the operation's stage (`evacuating`, `verifying`,
`completed`), the PVE task log on the node (`qmigrate` tasks), cluster
quorum, and the worker log for errors.
