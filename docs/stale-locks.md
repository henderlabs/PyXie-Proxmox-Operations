# Stale guest locks: what they are and how PyXie clears them

Proxmox puts a **lock** on a guest while a backup, migration or snapshot runs and removes it when the task ends. If the task dies, the lock stays. A stale lock makes the nightly backup fail ("VM is locked (backup)") and blocks migration and evacuation.

## How you see it

**Health** shows a warning "VM 'name' has a stale backup lock on host" with how long the lock has been there. PyXie calls a lock stale only when it sees **no task running** for that guest on any host.

## Clear it from Health

1. Press **Preview clearing stale locks on <host>**. PyXie asks the host what it really sees and says, in plain words, which of three cases it is: a finished task, a dead one, or a hung backup. It lists exactly which guests would be unlocked and which are left alone, with the reason.
2. Read the list, then approve in the Tasks panel. Each lock is **re-checked right before it is cleared**; a lock with a running task is never touched.
3. The finding closes by itself once the lock is gone (within a couple of scans).

A hung backup (running longer than 6 hours on a guest locked for backup) is offered as a stop of that backup task first, through Proxmox's own Stop.

## What the host-side tool does (and does not)

Clearing needs the **maintenance wrapper 1.2.0 or newer** on that host ([add-wrapper.md](add-wrapper.md)); Proxmox does not let a non-root account remove a guest lock. The wrapper has two fixed commands, `locks` (read only) and `unlock-stale`, and a lock is cleared only when all of these hold: no task is running for it (a task counts as running only when it has no end time and its process still exists), the lock is a clearable type (`backup`, `migrate`, `snapshot`, `snapshot-delete`, `rollback`), and the guest's configuration is at least 30 minutes old.

Know before you rely on it:

- `unlock-stale` works on the **whole host**: it clears every lock that passes those checks, not only the ones in your preview. The approval step re-reads the host first, so a new stale lock appearing between preview and approval is the only case, and it is subject to the same checks.
- **Containers (LXC) are not covered**; only VMs.
- The wrapper takes no guest name or number from outside; the number comes from the configuration file name and is checked to be digits.

## Clear them automatically (off by default)

**Platform > Policies** has *Clear stale backup and snapshot locks automatically* (policy `locks.auto_clear_enabled`) and *after how many minutes* (default 60, minimum 10). When on, a lock of type **backup** or **snapshot** that has stayed stale for that long is cleared through the same task (same preview, same checks, same audit trail), approved by "PyXie (automatic)". Never a migrate or any other type; at most **5 runs an hour**; never while *Write to Proxmox* is off; only on hosts with wrapper 1.2.0 or newer. This was covered by automated tests; it has not yet been watched on a real stale lock in a lab.

## Related

- Emptying a host and why a locked guest blocks it: [host-lifecycle.md](host-lifecycle.md).
- Technical reference for the wrapper: [host-kit.md](host-kit.md).
