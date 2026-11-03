# Workers and limits: how PyXie runs its tasks

Whenever you ask PyXie to change something in Proxmox (shut down a VM, move a disk, migrate a guest, empty a host, update a host), it becomes a **task**. A **worker** is the part of PyXie that carries tasks out. With one worker, tasks go one after another, so a quick job such as shutting down a VM can wait behind a long disk copy.

Choose how PyXie runs tasks under **Platform > Settings > Workers**. The choice starts the next time PyXie's worker service restarts (for example when PyXie is updated); nothing restarts when you press Save, and a task that is running carries on from where it was.

| Choice | Workers | When to use it |
|---|---|---|
| **One worker** | one for everything | The original behavior. Existing installs stay on it until you choose. |
| **Two workers** | one for quick jobs, one for long jobs | New installs start here. A shutdown, reboot, tag, snapshot, resize or setting edit never waits behind a disk copy. |
| **Several workers for long jobs** | quick worker plus 2 to 4 long-job workers | Only when several long jobs really can run together: moves between different pairs of hosts, different storage. |

Quick jobs: start, shut down, force stop, reboot, tags, snapshots, resizing, network, CD-ROM and setting edits, clearing locks, backup-plan membership. Long jobs: everything else (migrations, moving disks, restores, clones, evacuating a host, host updates). The list lives in `shared/pyxie_core/op_lanes.py`.

## Limits: how much may happen at the same time
More workers do not make a slow network faster. Two big copies over one shared network just slow each other down, and a live move can fail to catch up. So PyXie has limits, all **1** by default (one thing at a time, as it always was), maximum 4:

- **Guests moved at once when you empty a host** (Evacuate / Maint Mode and Evacuate). Full Maintenance and Balance Load always move one at a time.
- **Moves out of one host at once** and **moves into one host at once.** To move two guests off one host together, "out" must be 2 or more too.
- **Disk copies per storage at once.**

Rule of thumb: about one move at a time for every 1 Gb/s of network that is free for moving guests, minus one if the cluster's heartbeat shares that network. PyXie cannot measure free bandwidth for you.

## What happens when something is busy
- A task you started that needs a guest or host another task is using **waits** and tries again by itself (about every minute, up to 6 hours). It shows "Waiting for ..." and can be cancelled while it waits.
- Automatic work (auto-balance, automatic lock clearing) and the steps inside a bigger run skip or stop as before.
- A host that has moves in flight cannot be updated or put into maintenance mode by another task until they finish; a host being updated cannot start moves. An evacuation's own moves are exempt.

## When a worker dies
- Every running task is stamped with the worker running it and a heartbeat (every 15 seconds). A second worker never runs a task whose heartbeat is fresh.
- Locks and places are taken for 10 minutes and renewed by the heartbeat, so a crashed worker frees its hosts and guests within minutes. A task whose worker stopped is taken over after 2 minutes (at most 3 times, then it fails and asks you to check it in Proxmox).

## Watching it
Health shows each lane (running, waiting, longest wait in the last 7 days), the live workers and how many places are in use. A warning appears if an approved task has not started after 30 minutes (policy `workers.wait_warning_minutes`, 0 turns it off).
