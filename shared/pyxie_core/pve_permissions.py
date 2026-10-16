"""What the PyXie admin (maintenance) token may do in Proxmox, checked BEFORE an action is offered, and Proxmox's 403 turned into words.

Proxmox answers a write the token has no privilege for with 403 "Permission check failed (/vms/ID, PRIV)". The preview asks first
(`GET /access/permissions?path=/vms/ID`), so a missing privilege is named in the preview with the fix, and not after a failed task.
"""

import re

from .credentials import load_pve_credentials
from .pve_write_client import PveMaintenanceClient

ROLE = "PyXieAdmin"
_DENIED = re.compile(r"Permission check failed \(([^,)]+),\s*([^)]+)\)")


def fix_command(privs: list[str]) -> str:
    return f"pveum role modify {ROLE} --privs {','.join(privs)} --append 1"


def missing_privileges(db, target, vmid: int, needed: list[str], *, path: str | None = None) -> list[str] | None:
    """The privileges in `needed` the maintenance token lacks on this VM, or None when that could not be determined (then the action
    is not blocked: Proxmox itself still decides). Read-only."""
    try:
        with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as client:
            perms = client._get("/access/permissions", params={"path": path or f"/vms/{vmid}"}) or {}
    except Exception:  # noqa: BLE001
        return None
    held: set[str] = set()
    for granted in perms.values():
        held.update(k for k, v in (granted or {}).items() if v)
    return [p for p in needed if p not in held]


def permission_reason(missing: list[str], what: str) -> str:
    return (f"the PyXie admin token is not allowed to {what}: its Proxmox role lacks {', '.join(missing)}. "
            f"Add it in Proxmox (Datacenter > Permissions > Roles > {ROLE}) or run on a node: {fix_command(missing)}")


def friendly_error(exc: Exception) -> str:
    """A Proxmox 'Permission check failed' as a sentence with the fix; any other error unchanged."""
    text = str(exc)
    m = _DENIED.search(text)
    if m:
        priv = m.group(2).strip()
        return (f"Proxmox refused: the PyXie admin token does not have {priv} on {m.group(1).strip()}. "
                f"Add it to the {ROLE} role (Datacenter > Permissions > Roles) or run on a node: {fix_command([priv])}")
    return text
