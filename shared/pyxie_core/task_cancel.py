"""When a running task can be cancelled safely. Pure.

A live migration can be stopped while it copies disks or memory (the VM keeps running on its source host and nothing has switched). In the last
seconds, once Proxmox reports the transfer finished and is switching the VM over, stopping can leave it in an odd state, so cancel is held until
the task ends one way or the other.
"""

import re

_FINAL = re.compile(r"(migration status: completed|migration finished|stopping nbd|switch|resum|spice|cleanup|finished successfully)", re.I)


def migration_cancel_state(progress: dict | None) -> tuple[bool, str]:
    """(safe_to_cancel, phase text) from the latest progress record of a running migration."""
    p = progress or {}
    line = str(p.get("raw_line") or "")
    if _FINAL.search(line):
        return False, "finishing the final switchover"
    phase = p.get("phase")
    if phase == "storage":
        return True, "copying disks"
    if phase == "vm":
        return True, "copying memory"
    return True, "copying" if line else "starting"


_MOVE_FINAL = re.compile(r"(mirror is ready|completing block job|completed successfully|removing|deleting|switch)", re.I)


def storage_move_cancel_state(progress: dict | None) -> tuple[bool, str]:
    """(safe_to_cancel, phase text) for a running disk move. While Proxmox copies, stopping it leaves the source disk untouched; once the mirror
    is ready and Proxmox is completing the switch to the new disk, cancel is held until the task ends. Pure."""
    p = progress or {}
    line = str(p.get("raw_line") or "")
    if _MOVE_FINAL.search(line):
        return False, "finishing the switch to the new disk"
    return True, "copying the disk" if line else "starting"
