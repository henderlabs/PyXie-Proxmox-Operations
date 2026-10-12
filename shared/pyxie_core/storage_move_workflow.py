"""Change a VM's storage (workload.move_storage): Proxmox "move disk", one disk at a time, while the guest keeps running.

Same Safety Contract as every other operation: dry-run (what moves, where, is there room) -> approve -> revalidate -> lock ->
execute and monitor each Proxmox task -> read the config back from Proxmox -> verify -> audit. The old copy of a disk is deleted by
Proxmox only after it reports the copy finished, so a failed or cancelled move leaves the VM exactly as it was.
"""

from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .labels import vm_label
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Node, Operation, PveTarget, Workload, WorkloadDisk
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_write_client import (
    MutationsDisabledError, PveMaintenanceClient, SelfProtectionError, TaskTimeoutError, update_migration_progress,
)
from . import rollback

MIN_TASK_TIMEOUT = 1800
BYTES_PER_SECOND_FLOOR = 20 * 1024 * 1024  # a move is allowed to run at this speed before it counts as stuck
FREE_SPACE_MARGIN = 1.10  # the target must have 10% more room than the disks need


class StorageMoveError(Exception):
    pass


def _load_context(db, workload: Workload):
    node = db.query(Node).filter(Node.id == workload.node_id).one()
    cluster = db.query(Cluster).filter(Cluster.id == workload.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    return node, cluster, target


def movable_disks(db, workload: Workload, target_storage: str) -> list[dict]:
    """The VM's disks that are not already on the target (CD-ROMs and empty drives are never moved)."""
    rows = db.query(WorkloadDisk).filter(WorkloadDisk.workload_id == workload.id, WorkloadDisk.is_cdrom.is_(False)).all()
    return sorted(
        ({"slot": d.slot, "storage": d.storage, "size_bytes": d.size_bytes or 0} for d in rows if d.storage and d.storage != target_storage and d.volume != "none"),
        key=lambda d: d["slot"],
    )


def check_target(storage_rows: list[dict], name: str, needed_bytes: int) -> list[str]:
    """Why the target cannot take the disks, from Proxmox's own list of the storages on the VM's node. Pure."""
    row = next((s for s in storage_rows if s.get("storage") == name), None)
    if row is None:
        return [f"storage '{name}' is not available on this VM's host"]
    problems = []
    if not row.get("active", 1):
        problems.append(f"storage '{name}' is not active on this host")
    if "images" not in str(row.get("content") or ""):
        problems.append(f"storage '{name}' is not set up to hold VM disks")
    avail = row.get("avail")
    if avail is not None and needed_bytes * FREE_SPACE_MARGIN > avail:
        problems.append(f"storage '{name}' has {avail / 1024 ** 3:.0f} GB free, the disks need {needed_bytes / 1024 ** 3:.0f} GB plus a 10% margin")
    return problems


def dry_run_storage_move(db, workload: Workload, *, target_storage: str, actor: str, correlation_id=None) -> Operation:
    if workload.type != "vm":
        raise StorageMoveError("only VM (qemu) storage changes are implemented, not containers")
    node, cluster, target = _load_context(db, workload)
    disks = movable_disks(db, workload, target_storage)
    if not disks:
        raise StorageMoveError(f"none of {vm_label(workload)}'s disks needs to move: they are all on '{target_storage}' already")
    was_running = workload.status == "running"
    needed = sum(d["size_bytes"] for d in disks)
    context = {
        "node_id": str(node.id), "node": node.name, "vmid": workload.vmid, "target_storage_name": target_storage,
        "disks": disks, "was_running": was_running, "completed_disks": [], "total_bytes": needed,
    }
    op = create_operation(db, "workload.move_storage", cluster_id=cluster.id, node_id=node.id, workload_id=workload.id,
                          correlation_id=correlation_id, context=context, created_by=actor)

    reasons: list[str] = []
    blocking: list[str] = []
    if workload.status not in ("running", "stopped"):
        reasons.append(f"workload status is '{workload.status}': not safe to move its disks right now")
        blocking.append("SAFE-MIGRATE-001")
    free = None
    try:
        client, _cred = build_pve_client(db, target)
        with client:
            rows = client.storage_list(node.name)
        problems = check_target(rows, target_storage, needed)
        free = next((s.get("avail") for s in rows if s.get("storage") == target_storage), None)
    except Exception as exc:  # noqa: BLE001
        problems = [f"could not read the storages on {node.name} to check there is room: {str(exc)[:120]}"]
    if problems:
        reasons.extend(problems)
        blocking.append("SAFE-STORAGE-002")
    reasons.append(
        f"{len(disks)} disk{'s' if len(disks) != 1 else ''} ({needed / 1024 ** 3:.0f} GB) will be copied to '{target_storage}' one at a time"
        + (" while the VM keeps running (it may be slower during the copy)" if was_running else " (the VM is stopped)")
        + "; each old copy is deleted only after Proxmox reports its copy finished."
    )
    from .pve_write_client import self_vmid_from_env
    if str(workload.vmid) == (self_vmid_from_env() or ""):
        reasons.append("this is the VM PyXie itself runs on: PyXie keeps running during the copy, and a failed copy leaves it unchanged")
    dry_run_result = {
        "vmid": workload.vmid, "workload_name": workload.name, "node": node.name, "target_storage": target_storage,
        "disks": disks, "total_bytes": needed, "target_free_bytes": free, "will_move_while_running": was_running,
        "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": blocking,
    }
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=dry_run_result, blocking_safety_rules=blocking, actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=dry_run_result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def _timeout_for(size_bytes: int) -> float:
    return max(MIN_TASK_TIMEOUT, size_bytes / BYTES_PER_SECOND_FLOOR * 1.5)


def execute_storage_move(db, operation_id) -> Operation:
    """Resumable: completed disks are remembered in the operation's context, and a running Proxmox task is picked up again by its UPID."""
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise StorageMoveError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "monitoring", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    workload = db.query(Workload).filter(Workload.id == op.workload_id).one()
    node, cluster, target = _load_context(db, workload)
    ctx = dict(op.context or {})
    target_storage = ctx["target_storage_name"]
    disks = ctx["disks"]

    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            read_client, _cred = build_pve_client(db, target)
            with read_client:
                live = next((v for v in read_client.qemu_list(node.name) if int(v.get("vmid", -1)) == workload.vmid), None)
                rows = read_client.storage_list(node.name)
            if live is None:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"{vm_label(workload)} is no longer listed on {node.name}")
            left = [d for d in disks if d["slot"] not in ctx.get("completed_disks", [])]
            problems = check_target(rows, target_storage, sum(d["size_bytes"] for d in left))
            if problems:
                release_locks_for_operation(db, op.id)
                return block_operation(db, op, blocking_safety_rules=["SAFE-STORAGE-002"], actor="system")
            try:
                acquire_lock(db, resource_type="workload", resource_id=workload.id, operation_id=op.id, reason="workload.move_storage")
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")

        creds = load_pve_credentials(db, target, "maintenance")
        if op.status in ("executing", "monitoring"):
            with PveMaintenanceClient(creds) as write_client:
                for d in disks:
                    if d["slot"] in (op.context or {}).get("completed_disks", []):
                        continue
                    if not op.pve_upid:
                        op.pve_upid = write_client.move_disk(node.name, workload.vmid, d["slot"], target_storage, delete_source=True)
                        db.commit()
                        op = enter_stage(db, op, status="monitoring", stage="monitoring", pve_upid=op.pve_upid)
                    upid = op.pve_upid
                    import time
                    deadline = time.monotonic() + _timeout_for(d["size_bytes"])
                    last_n = 0
                    while True:
                        res = write_client.task_status(node.name, upid)
                        try:
                            lines = write_client.task_log(node.name, upid, start=last_n)
                        except Exception:  # noqa: BLE001
                            lines = []
                        if lines:
                            last_n = max(l.get("n", last_n) for l in lines)
                            progress = update_migration_progress(op.progress, lines)
                            if progress and progress is not op.progress:
                                op.progress = progress
                                db.commit()
                        if res.status != "running":
                            break
                        if time.monotonic() > deadline:
                            raise TaskTimeoutError(f"moving {d['slot']} on {node.name} did not finish within {int(_timeout_for(d['size_bytes']))}s")
                        time.sleep(3)
                    if not res.succeeded:
                        release_locks_for_operation(db, op.id)
                        return fail_operation(db, op, error=f"moving {d['slot']} to '{target_storage}' did not succeed: {res.raw} -- the VM was left as it was")
                    done = list((op.context or {}).get("completed_disks", [])) + [d["slot"]]
                    op.context = {**(op.context or {}), "completed_disks": done}
                    op.pve_upid = None
                    db.commit()
            if op.progress and op.progress.get("storage"):
                op.progress = {**op.progress, "storage": {**op.progress["storage"], "done": True, "pct": 100.0}, "phase": "storage", "pct": 100.0}
                db.commit()
            op = enter_stage(db, op, status="verifying", stage="verifying")

        if op.status == "verifying":
            read_client, _cred = build_pve_client(db, target)
            with read_client:
                config = read_client.qemu_config(node.name, workload.vmid)
            problems = [f"{d['slot']} is on '{str(config.get(d['slot'], '')).split(':')[0]}', expected '{target_storage}'" for d in disks
                        if str(config.get(d["slot"], "")).split(":")[0] != target_storage]
            op.verification_result = {"disks": {d["slot"]: str(config.get(d["slot"], "")).split(",")[0] for d in disks}}
            db.commit()
            release_locks_for_operation(db, op.id)
            if problems:
                return fail_operation(db, op, error="read-back after the move didn't match: " + "; ".join(problems))
            try:
                from .discovery import run_discovery
                run_discovery(db, target, actor="operation")
            except Exception:  # noqa: BLE001
                pass
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE)

        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")
    except (MutationsDisabledError, TaskTimeoutError, SelfProtectionError) as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error: {exc}")
