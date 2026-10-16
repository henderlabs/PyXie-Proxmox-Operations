"""Attach, change or eject the ISO in a VM's CD/DVD drive (workload.cdrom_change), or add a CD/DVD drive.

Changing the medium of a drive that already exists works while the VM runs (Proxmox swaps the disc like a real drive). Adding a new drive to a running VM waits for
its next shutdown and start (IDE cannot be hot-added). The ISO must exist on a storage the VM's host can read; the preview checks that against Proxmox itself.
"""

import re

from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .hw_edit_workflow import pending_value
from .iso_workflow import parse_volid
from .labels import vm_label
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Node, Operation, PveTarget, Workload, WorkloadDisk
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_permissions import friendly_error, missing_privileges, permission_reason
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient
from . import rollback

OP_TYPE = "workload.cdrom_change"
IDE_SLOTS = [f"ide{i}" for i in range(4)]
SLOT_RE = re.compile(r"^(ide[0-3]|sata[0-5])$")


class CdromError(Exception):
    pass


def free_ide_slot(config: dict) -> str | None:
    return next((s for s in IDE_SLOTS if s not in config), None)


def is_cdrom_line(value) -> bool:
    return "media=cdrom" in str(value or "")


def cdrom_line(volid: str | None) -> str:
    """The drive line for an ISO, or an empty drive. Pure."""
    return f"{volid},media=cdrom" if volid else "none,media=cdrom"


def dry_run_cdrom(db, workload: Workload, *, slot: str | None, volid: str | None, boot_first: bool, actor: str, correlation_id=None) -> Operation:
    if workload.type != "vm":
        raise CdromError("CD/DVD drives are only implemented for VMs (qemu), not containers")
    node = db.query(Node).filter(Node.id == workload.node_id).one()
    cluster = db.query(Cluster).filter(Cluster.id == workload.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    was_running = workload.status == "running"
    ctx = {"node_id": str(node.id), "node": node.name, "vmid": workload.vmid, "slot": slot, "volid": volid or None, "boot_first": bool(boot_first), "was_running": was_running}
    op = create_operation(db, OP_TYPE, cluster_id=cluster.id, node_id=node.id, workload_id=workload.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    if workload.status not in ("running", "stopped"):
        reasons.append(f"workload status is '{workload.status}': not safe to change its drives right now")
        blocking.append("SAFE-MIGRATE-001")
    old = None
    try:
        client, _cred = build_pve_client(db, target)
        with client:
            config = client.qemu_config(node.name, workload.vmid)
            if volid:
                parsed = parse_volid(volid)
                if not parsed:
                    reasons.append("the ISO must be a Proxmox volume like storage:iso/name.iso")
                    blocking.append("SAFE-CDROM-001")
                elif volid not in {c.get("volid") for c in client.storage_content(node.name, parsed[0], "iso") or []}:
                    reasons.append(f"{volid} is not on a storage {node.name} can read")
                    blocking.append("SAFE-CDROM-001")
        if config.get("lock"):
            reasons.append(f"Proxmox has the VM locked ('{config.get('lock')}'): another task is working on it")
            blocking.append("SAFE-LOCK-001")
        adding = slot in (None, "", "new")
        if adding:
            slot = free_ide_slot(config)
            if slot is None:
                reasons.append("all four IDE slots are in use")
                blocking.append("SAFE-CDROM-001")
            elif was_running:
                reasons.append("a new drive on a running VM appears at its next shutdown and start (IDE drives cannot be hot-added)")
        elif not SLOT_RE.match(slot or ""):
            reasons.append(f"'{slot}' is not an IDE or SATA slot")
            blocking.append("SAFE-CDROM-001")
        else:
            old = config.get(slot)
            if old is None or not is_cdrom_line(old):
                reasons.append(f"{slot} is not a CD/DVD drive of this VM")
                blocking.append("SAFE-CDROM-001")
        ctx["slot"] = slot
        if old is not None and not blocking:
            cur = str(old).split(",")[0]
            if (cur if cur != "none" else None) == (volid or None):
                reasons.append("the drive already holds exactly this")
                blocking.append("SAFE-CDROM-001")
        if not blocking:
            if volid:
                reasons.append(f"{slot} gets {volid}" + (f" (now: {str(old).split(',')[0]})" if old and str(old).split(",")[0] != "none" else "") + (": the running VM sees the disc change like a real drive" if was_running and not adding else ""))
                order = str(config.get("boot", ""))
                if not boot_first and slot not in order:
                    reasons.append(f"{slot} is not in the boot order, so the VM will not boot from it; tick 'boot from it next start' if it should")
            else:
                reasons.append(f"{slot} is emptied: the ISO is ejected" + (" (a guest that has the disc locked may refuse until it lets go)" if was_running else ""))
            if boot_first and volid:
                reasons.append(f"the boot order is changed to try {slot} first (next start)")
        lacking = missing_privileges(db, target, workload.vmid, ["VM.Config.CDROM"])
        if lacking:
            reasons.append(permission_reason(lacking, "change CD/DVD drives"))
            blocking.append("SAFE-PERMISSION-001")
        ctx["old"] = old
        ctx["boot_order"] = str(config.get("boot", "")) if boot_first else None
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"could not read {vm_label(workload)} from Proxmox: {str(exc)[:140]}")
        blocking.append("SAFE-STORAGE-002")
    op.context = ctx
    db.commit()
    result = {"vmid": workload.vmid, "workload_name": workload.name, "node": node.name, "slot": slot, "volid": volid, "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": sorted(set(blocking))}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def legacy_boot_devices(current: str, config: dict | None) -> list[str]:
    """Old-style boot setting 'cdn' (c = first hard disk, d = CD-ROM, n = network) as a device list, using the VM's config to name the devices. Pure."""
    cfg = config or {}
    disk = cfg.get("bootdisk") or next((k for k in sorted(cfg) if k.startswith(("scsi", "virtio", "sata", "ide")) and "media=cdrom" not in str(cfg[k])), None)
    cd = next((k for k in sorted(cfg) if k.startswith(("scsi", "virtio", "sata", "ide")) and "media=cdrom" in str(cfg[k])), None)
    net = next((k for k in sorted(cfg) if k.startswith("net")), None)
    return [d for d in ({"c": disk, "d": cd, "n": net}.get(ch) for ch in current) if d]


def new_boot_order(current: str, slot: str, config: dict | None = None) -> str:
    """'order=scsi0;ide2;net0' -> 'order=<slot>;scsi0;ide2;net0' with the slot moved to the front. An old-style 'cdn' setting is converted first. Pure."""
    current = current or ""
    if current and "=" not in current and ";" not in current and set(current) <= set("cdn"):
        devs = legacy_boot_devices(current, config)
    else:
        devs = current.replace("order=", "", 1).split(";")
    return "order=" + ";".join([slot] + [d for d in devs if d and d != slot])


def execute_cdrom(db, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise CdromError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    workload = db.query(Workload).filter(Workload.id == op.workload_id).one()
    node = db.query(Node).filter(Node.id == workload.node_id).one()
    cluster = db.query(Cluster).filter(Cluster.id == workload.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    ctx = dict(op.context or {})
    slot, volid = ctx["slot"], ctx.get("volid")
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            rc, _cred = build_pve_client(db, target)
            with rc:
                config = rc.qemu_config(node.name, workload.vmid)
            if config.get("lock") or (config.get(slot) if ctx.get("old") else None) != ctx.get("old") or (not ctx.get("old") and slot in config):
                release_locks_for_operation(db, op.id)
                return block_operation(db, op, blocking_safety_rules=["SAFE-CDROM-001"], actor="system")  # changed since the preview
            try:
                acquire_lock(db, resource_type="workload", resource_id=workload.id, operation_id=op.id, reason=OP_TYPE)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")
        if op.status == "executing":
            live_cfg = {}
            if ctx.get("boot_order") is not None and volid:
                rc, _cred = build_pve_client(db, target)
                with rc:
                    live_cfg = rc.qemu_config(node.name, workload.vmid)
            with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as wc:
                wc.set_vm_config_keys(node.name, workload.vmid, {slot: cdrom_line(volid), **({"boot": new_boot_order(ctx["boot_order"], slot, live_cfg)} if ctx.get("boot_order") is not None and volid else {})})
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            rc, _cred = build_pve_client(db, target)
            with rc:
                config = rc.qemu_config(node.name, workload.vmid)
                pending = rc.qemu_pending(node.name, workload.vmid)
            got = str(pending_value(pending, config, slot) or "")
            want = cdrom_line(volid)
            op.verification_result = {"slot": slot, "line": got}
            db.commit()
            release_locks_for_operation(db, op.id)
            if got.split(",")[0] != want.split(",")[0] or "media=cdrom" not in got:
                return fail_operation(db, op, error=f"read-back after the change shows '{got}', expected '{want}'")
            row = db.query(WorkloadDisk).filter(WorkloadDisk.workload_id == workload.id, WorkloadDisk.slot == slot).first()
            if row is None:  # a drive added just now: record it so the ISO library knows it is in use before the next inventory pass
                row = WorkloadDisk(workload_id=workload.id, slot=slot, bus=slot.rstrip("0123456789"), is_cdrom=True)
                db.add(row)
            row.volume = want.split(",")[0]
            row.storage = parse_volid(volid)[0] if volid else None
            db.commit()
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE)
        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")
    except MutationsDisabledError as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=friendly_error(exc) if "Permission check failed" in str(exc) else f"unexpected error: {exc}")
