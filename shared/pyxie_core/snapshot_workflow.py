"""VM snapshots (workload.snapshot_create / snapshot_delete / snapshot_rollback): take, remove or go back to a Proxmox snapshot.

Same Safety Contract as every other operation: dry-run (what will happen, can this VM and its storage do it) -> approve ->
revalidate -> lock -> execute and monitor the Proxmox task -> read the snapshot list back from Proxmox -> verify -> audit.
Proxmox's own feature check (`/qemu/{vmid}/feature?feature=snapshot`) decides whether the VM's disks can be snapshotted at all, so a
VM that cannot (for example a TPM disk on NFS) is told so in the preview and not after a failed task.
"""

import re
from datetime import datetime, timezone

from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .labels import vm_label
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Node, Operation, PveTarget, Workload, WorkloadSnapshot
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient, SelfProtectionError, TaskTimeoutError, self_vmid_from_env
from . import guest_kind as gk
from . import rollback
from .pve_permissions import friendly_error, missing_privileges, permission_reason

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{1,39}$")  # Proxmox: starts with a letter, letters/digits/_/-, 2 to 40 characters
TASK_TIMEOUT = {"create": 1800.0, "delete": 1800.0, "rollback": 1800.0}
OP_TYPE = {"create": "workload.snapshot_create", "delete": "workload.snapshot_delete", "rollback": "workload.snapshot_rollback"}
ACTION_OF = {v: k for k, v in OP_TYPE.items()}
MANY_SNAPSHOTS = 3


class SnapshotError(Exception):
    pass


def _load_context(db, workload: Workload):
    node = db.query(Node).filter(Node.id == workload.node_id).one()
    cluster = db.query(Cluster).filter(Cluster.id == workload.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    return node, cluster, target


def valid_name(name: str) -> str | None:
    """Why the name cannot be used, or None. Pure."""
    if name is None or not NAME_RE.match(name):
        return "a snapshot name starts with a letter, uses only letters, digits, - and _, and is 2 to 40 characters long"
    if name.lower() == "current":
        return "'current' is Proxmox's name for the running state and cannot be a snapshot name"
    return None


def real_snapshots(rows: list[dict]) -> list[dict]:
    """Proxmox lists the running state as a pseudo-snapshot called 'current'; it is not one."""
    return [s for s in (rows or []) if s.get("name") != "current"]


def suggest_name(now: datetime | None = None) -> str:
    return "pyxie-" + (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M")


def refresh_stored_snapshots(db, workload: Workload, rows: list[dict]) -> None:
    """Replace PyXie's stored copy of this VM's snapshots with what Proxmox just said (the Reporting inventory does the same on its schedule)."""
    db.query(WorkloadSnapshot).filter(WorkloadSnapshot.workload_id == workload.id).delete()
    t = datetime.now(timezone.utc)
    for s in real_snapshots(rows):
        ts = s.get("snaptime")
        db.add(WorkloadSnapshot(
            workload_id=workload.id, name=s.get("name"), description=(s.get("description") or "").strip() or None, parent=s.get("parent"),
            snapshot_time=datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None,
            includes_ram=bool(s.get("vmstate")) if s.get("vmstate") is not None else None, collected_at=t,
        ))
    db.commit()


def dry_run_snapshot(db, workload: Workload, *, action: str, name: str, description: str = "", include_ram: bool = False,
                     actor: str, correlation_id=None) -> Operation:
    if action not in OP_TYPE:
        raise SnapshotError("action must be 'create', 'delete' or 'rollback'")
    name = (name or "").strip()
    node, cluster, target = _load_context(db, workload)
    was_running = workload.status == "running"
    context = {"node_id": str(node.id), "node": node.name, "vmid": workload.vmid, "action": action, "snapshot_name": name,
               "description": (description or "").strip(), "include_ram": bool(include_ram and was_running and workload.type == "vm"), "was_running": was_running}
    op = create_operation(db, OP_TYPE[action], cluster_id=cluster.id, node_id=node.id, workload_id=workload.id,
                          correlation_id=correlation_id, context=context, created_by=actor)

    reasons: list[str] = []
    blocking: list[str] = []
    snaps: list[dict] = []
    existing = None
    if workload.status not in ("running", "stopped"):
        reasons.append(f"workload status is '{workload.status}': not safe to snapshot right now")
        blocking.append("SAFE-MIGRATE-001")
    if action == "create":
        problem = valid_name(name)
        if problem:
            reasons.append(problem)
            blocking.append("SAFE-SNAPSHOT-001")
    elif not name:
        reasons.append("choose a snapshot")
        blocking.append("SAFE-SNAPSHOT-001")

    try:
        client, _cred = build_pve_client(db, target)
        with client:
            snaps = real_snapshots(gk.snapshots(client, workload, node.name))
            config = gk.config(client, workload, node.name)
            can_snapshot = (gk.can_snapshot(client, workload, node.name) is not False) if action == "create" else True
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"could not read {vm_label(workload)} from Proxmox: {str(exc)[:140]}")
        blocking.append("SAFE-STORAGE-002")
        snaps, config, can_snapshot = [], {}, True

    existing = next((s for s in snaps if s.get("name") == name), None)
    need = ["VM.Snapshot.Rollback"] if action == "rollback" else ["VM.Snapshot"]
    lacking = missing_privileges(db, target, workload.vmid, need)
    if lacking:
        reasons.append(permission_reason(lacking, {"create": "take snapshots", "delete": "delete snapshots", "rollback": "roll back snapshots"}[action]))
        blocking.append("SAFE-PERMISSION-001")
    if config.get("lock"):
        reasons.append(f"Proxmox has the VM locked ('{config.get('lock')}'): another task is working on it")
        blocking.append("SAFE-LOCK-001")
    if action == "create":
        if existing:
            reasons.append(f"a snapshot named '{name}' already exists")
            blocking.append("SAFE-SNAPSHOT-001")
        if not can_snapshot:
            reasons.append("Proxmox says this VM cannot be snapshotted: one of its disks (or its TPM / EFI disk) is on storage without snapshot support")
            blocking.append("SAFE-STORAGE-002")
        if include_ram and not was_running:
            reasons.append("the VM is stopped, so there is no memory to save: the snapshot will be disk only")
        if len(snaps) >= MANY_SNAPSHOTS:
            reasons.append(f"the VM already has {len(snaps)} snapshots: a long chain slows the disk and holds space until the snapshots are removed")
        if context["include_ram"]:
            reasons.append("saving the memory pauses the VM briefly and writes its RAM to storage; rolling back resumes it exactly where it was")
        if str(workload.vmid) == (self_vmid_from_env() or "") and context["include_ram"]:
            reasons.append("this is the VM PyXie itself runs on: a memory snapshot pauses it, so PyXie freezes for the duration")
        reasons.append(f"a snapshot '{name}' of {vm_label(workload)} will be taken" + (" with its memory" if context["include_ram"] else " (disks only)") + "; it can be deleted afterwards without any effect on the VM.")
        classification = rollback.MANUAL_REVERSIBLE
    elif action == "delete":
        if not existing:
            reasons.append(f"snapshot '{name}' no longer exists on the VM")
            blocking.append("SAFE-SNAPSHOT-001")
        else:
            reasons.append(f"snapshot '{name}' will be removed. The VM and its other snapshots are not touched, and the point in time it holds cannot be returned to afterwards.")
        classification = rollback.IRREVERSIBLE
    else:
        if str(workload.vmid) == (self_vmid_from_env() or ""):
            reasons.append("this is the VM PyXie itself runs on: rolling it back would stop the very process doing it")
            blocking.append("SAFE-SNAPSHOT-001")
        if not existing:
            reasons.append(f"snapshot '{name}' no longer exists on the VM")
            blocking.append("SAFE-SNAPSHOT-001")
        else:
            has_ram = bool(existing.get("vmstate"))
            newer = [s["name"] for s in snaps if (s.get("snaptime") or 0) > (existing.get("snaptime") or 0)]
            reasons.append(f"{vm_label(workload)} goes back to snapshot '{name}': everything it has written since is lost.")
            reasons.append("the snapshot includes memory, so the VM resumes running as it was" if has_ram else "the snapshot has no memory state, so Proxmox leaves the VM powered off afterwards; it needs starting")
            if newer:
                reasons.append(f"newer snapshots ({', '.join(newer)}) may be discarded by the storage when going back past them")
        classification = rollback.IRREVERSIBLE
    context["rollback_classification"] = classification
    op.context = context
    db.commit()

    snaps_view = [{"name": s.get("name"), "description": s.get("description"), "snaptime": s.get("snaptime"), "vmstate": bool(s.get("vmstate")), "parent": s.get("parent")} for s in snaps]
    dry_run_result = {"vmid": workload.vmid, "workload_name": workload.name, "node": node.name, "action": action, "snapshot_name": name,
                      "include_ram": context["include_ram"], "was_running": was_running, "existing_snapshots": snaps_view,
                      "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": blocking}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=dry_run_result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=dry_run_result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_snapshot(db, operation_id) -> Operation:
    """Resumable: a running Proxmox task is picked up again by its UPID."""
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise SnapshotError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "monitoring", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    workload = db.query(Workload).filter(Workload.id == op.workload_id).one()
    node, cluster, target = _load_context(db, workload)
    ctx = dict(op.context or {})
    action, name = ctx["action"], ctx["snapshot_name"]

    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            read_client, _cred = build_pve_client(db, target)
            with read_client:
                snaps = real_snapshots(gk.snapshots(read_client, workload, node.name))
                config = gk.config(read_client, workload, node.name)
            present = any(s.get("name") == name for s in snaps)
            problem = None
            if config.get("lock"):
                problem = f"the VM is locked by Proxmox ('{config.get('lock')}')"
            elif action == "create" and present:
                problem = f"a snapshot named '{name}' now exists"
            elif action != "create" and not present:
                problem = f"snapshot '{name}' no longer exists"
            if problem:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"revalidation failed: {problem}")
            try:
                acquire_lock(db, resource_type="workload", resource_id=workload.id, operation_id=op.id, reason=OP_TYPE[action])
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")

        if op.status in ("executing", "monitoring"):
            creds = load_pve_credentials(db, target, "maintenance")
            with PveMaintenanceClient(creds).for_guest(gk.kind(workload)) as write_client:
                if not op.pve_upid:
                    if action == "create":
                        op.pve_upid = write_client.create_snapshot(node.name, workload.vmid, name, description=ctx.get("description", ""), include_ram=bool(ctx.get("include_ram")))
                    elif action == "delete":
                        op.pve_upid = write_client.delete_snapshot(node.name, workload.vmid, name)
                    else:
                        op.pve_upid = write_client.rollback_snapshot(node.name, workload.vmid, name)
                    db.commit()
                    op = enter_stage(db, op, status="monitoring", stage="monitoring", pve_upid=op.pve_upid)
                res = write_client.wait_for_task(node.name, op.pve_upid, timeout=TASK_TIMEOUT[action])
            if not res.succeeded:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"Proxmox reported the {action} of snapshot '{name}' did not succeed: {res.exit_status or res.raw}")
            op = enter_stage(db, op, status="verifying", stage="verifying")

        if op.status == "verifying":
            read_client, _cred = build_pve_client(db, target)
            with read_client:
                rows = gk.snapshots(read_client, workload, node.name)
                status = gk.status_current(read_client, workload, node.name).get("status")
            snaps = real_snapshots(rows)
            present = any(s.get("name") == name for s in snaps)
            op.verification_result = {"snapshot_present": present, "snapshots": [s.get("name") for s in snaps], "vm_status": status}
            db.commit()
            refresh_stored_snapshots(db, workload, rows)
            release_locks_for_operation(db, op.id)
            if action == "delete" and present:
                return fail_operation(db, op, error=f"snapshot '{name}' is still listed by Proxmox after the delete")
            if action != "delete" and not present:
                return fail_operation(db, op, error=f"snapshot '{name}' is not listed by Proxmox after the {action}")
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=ctx.get("rollback_classification", rollback.UNKNOWN))

        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")
    except (MutationsDisabledError, TaskTimeoutError, SelfProtectionError) as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=friendly_error(exc) if "Permission check failed" in str(exc) else f"unexpected error: {exc}")
