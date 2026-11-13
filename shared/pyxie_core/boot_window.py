"""Start at boot, for the length of a maintenance window.

A stopped guest with Start at boot on is powered on by Proxmox itself when its host reboots, even while PyXie has that host in maintenance mode
(st-pve105, 2026-11-13: ST-PRTG and ST-Tools came up after Full Maintenance). Both Evacuate / Maint Mode and Full Maintenance therefore offer, per
guest, "Leave stopped": Start at boot goes off before anything moves, and Exit Maintenance Mode puts it back exactly as it was.

The default is Leave stopped when the host will be left in maintenance mode (or empty) and PyXie may change the setting; otherwise it stays "leave as is",
which is what a plain reboot that returns the host to service has always done. Every function here is plain data in, plain data out, so it is unit-tested
without Proxmox.
"""

from __future__ import annotations

from .credentials import load_pve_credentials
from .pve_permissions import missing_privileges
from .pve_write_client import PveMaintenanceClient

PRIV = "VM.Config.Options"


def set_default(db, target, items: list[dict], *, window: bool) -> None:
    """Mark `boot_off` on every plan item that starts on boot and is not being moved. `window` is True when the host stays in maintenance mode or empty
    after the run. When the PyXie token lacks VM.Config.Options (cre-pyxie), the choice is off and the row says why: it would fail the run otherwise."""
    flagged = [i for i in items if i.get("starts_on_boot") and not i.get("included")]
    if not flagged:
        return
    missing = missing_privileges(db, target, flagged[0]["vmid"], [PRIV])
    can_change = missing == []  # None = could not be determined: keep today's behaviour
    for item in flagged:
        item["boot_off_available"] = can_change
        if not can_change:
            item["boot_off"] = False
            if missing:
                item["reasons"] = [*(item.get("reasons") or []), f"PyXie cannot turn Start at boot off here: the PyXie admin token lacks {PRIV} in Proxmox."]
            continue
        item["boot_off"] = bool(window)


def pending(ctx: dict, items: list[dict]) -> list[dict]:
    """The items still to turn off: chosen, not moved, not done yet."""
    done = set(ctx.get("boot_disabled", []))
    return [i for i in items if i.get("boot_off") and not i.get("included") and i["workload_id"] not in done]


def apply_off(db, target, node_name: str, ctx: dict, items: list[dict], save_progress) -> str | None:
    """Turn Start at boot off for the chosen guests, recording each one in ctx['boot_disabled'] as it is done. Returns an error sentence, or None."""
    todo = pending(ctx, items)
    if not todo:
        return None
    boot_disabled = list(ctx.get("boot_disabled", []))
    try:
        with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as writer:
            for item in todo:
                writer.for_guest("lxc" if item.get("guest_type") == "lxc" else "qemu").set_vm_config_keys(node_name, item["vmid"], {"onboot": 0})
                boot_disabled.append(item["workload_id"])
                ctx["boot_disabled"] = boot_disabled
                save_progress()
    except Exception as exc:  # noqa: BLE001
        return f"could not turn Start at boot off for the maintenance window (the PVE account needs {PRIV}): {exc}"
    return None


def restore_on(db, target, node_name: str, rows: list[dict]) -> str | None:
    """Put Start at boot back on for rows [{workload_id, vmid, name, type}]. Returns an error sentence naming what to fix by hand, or None."""
    if not rows:
        return None
    try:
        with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as writer:
            for row in rows:
                writer.for_guest("lxc" if row.get("type") == "lxc" else "qemu").set_vm_config_keys(node_name, row["vmid"], {"onboot": 1})
    except Exception as exc:  # noqa: BLE001
        return f"could not turn Start at boot back on: {exc}. Set it back on by hand for: " + ", ".join(r.get("name") or str(r["vmid"]) for r in rows)
    return None


def restore_rows(db, node) -> list[dict]:
    """Guests whose Start at boot was turned off during this maintenance window and not yet turned back on: [{workload_id, vmid, name, type}].
    Read from every completed Evacuate / Maint Mode and Full Maintenance run on this host since it entered maintenance mode."""
    from .models import Operation, Workload

    query = db.query(Operation).filter(
        Operation.node_id == node.id, Operation.status == "completed",
        Operation.operation_type_id.in_(["node.enter_maintenance", "maintenance.run"]),
    )
    if node.maintenance_mode_since is not None:
        # Compared on when the run FINISHED: Evacuate / Maint Mode is created before the host is flagged "in maintenance since" (the flag is set at its
        # end), so filtering on created_at skipped the very run that turned Start at boot off (found live 2026-11-13: Exit restored nothing).
        query = query.filter(Operation.completed_at >= node.maintenance_mode_since)
    rows, seen = [], set()
    for op in query.order_by(Operation.created_at).all():
        ctx = op.context or {}
        restored = set(ctx.get("boot_restored", []))
        for wid in ctx.get("boot_disabled", []):
            if wid in restored or wid in seen:
                continue
            seen.add(wid)
            workload = db.query(Workload).filter(Workload.id == wid).one_or_none()
            if workload is not None and not workload.is_missing and workload.node_id == node.id:
                rows.append({"workload_id": str(workload.id), "vmid": workload.vmid, "name": workload.name, "type": workload.type})
    return rows


def started_by_themselves(vms: list[dict] | None, cts: list[dict] | None) -> list[str]:
    """Names of the guests running on a host right after it rebooted. Reboot Node and Full Maintenance need the host idle first (nothing running), so
    anything running afterwards was powered on by Proxmox itself: a guest with Start at boot on. Pure: the caller passes Proxmox's two guest lists."""
    names = []
    for kind, rows in (("VM", vms), ("CT", cts)):
        for row in rows or []:
            if row.get("status") == "running":
                names.append(f"{row.get('name') or row.get('vmid')} ({kind} {row.get('vmid')})")
    return sorted(names)


def wait_for_startall(client, node_name: str, uptime_seconds, *, timeout: float = 100.0, interval: float = 4.0, clock=None, sleep=None) -> bool:
    """Wait until Proxmox has finished its boot-time "start all guests" task on `node_name` (it begins roughly 20 to 40 seconds after the host is up, so a check
    made right after the reboot verifies sees nothing yet: found live 2026-11-13, uptime 18 s, container started afterwards). True once a startall task that began
    since the boot has finished, False when none showed up within `timeout` (the caller then reports what it can see)."""
    import time as _time

    clock = clock or _time.monotonic
    sleep = sleep or _time.sleep
    deadline = clock() + timeout
    booted_at = _time.time() - float(uptime_seconds or 0) - 15
    while True:
        try:
            active = [t for t in client.tasks(node_name, limit=50, source="active") or [] if t.get("type") == "startall"]
            if not active:
                done = [t for t in client.tasks(node_name, limit=50) or [] if t.get("type") == "startall" and float(t.get("starttime") or 0) >= booted_at]
                if done:
                    return True
        except Exception:  # noqa: BLE001
            pass
        if clock() >= deadline:
            return False
        sleep(interval)
