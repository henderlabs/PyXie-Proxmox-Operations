"""Delete a VM or container (workload.delete): the one irreversible guest action, so it has the most gates.

dry-run -> approve -> revalidate -> lock -> destroy (one Proxmox task) -> read back (gone from the cluster) -> mark missing -> audit.
Refused while: the guest is running (shut it down first), Proxmox protection is on, it is HA-managed (take it out of HA first), it is the VM PyXie itself runs on,
or the person has not typed its exact name. Disks owned by the guest are destroyed with it; `purge` also removes it from backup jobs, replication and HA
configuration so nothing is left referencing a guest that no longer exists. Existing backups stay in the backup storage. PyXie never deletes on its own.
"""

from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .labels import vm_label
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Node, Operation, PveTarget, Workload
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_permissions import friendly_error, missing_privileges, permission_reason
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient, TaskTimeoutError, self_vmid_from_env
from .snapshot_workflow import real_snapshots
from . import guest_kind as gk
from . import rollback

OP_TYPE = "workload.delete"
GIB = 1 << 30


class DeleteError(Exception):
    pass


def kind_of(workload: Workload) -> str:
    return "qemu" if workload.type == "vm" else "lxc"


def _load(db, workload: Workload):
    node = db.query(Node).filter(Node.id == workload.node_id).one()
    cluster = db.query(Cluster).filter(Cluster.id == workload.cluster_id).one()
    return node, cluster, db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()


def _config(client, kind: str, node: str, vmid: int) -> dict:
    return (client.qemu_config(node, vmid) if kind == "qemu" else client.lxc_config(node, vmid)) or {}


def owned_disks(config: dict, kind: str) -> list[dict]:
    """The disks this guest owns (volumes on a storage, not CD-ROMs or host paths), for the preview. Pure."""
    import re
    keys = re.compile(r"^(scsi|virtio|sata|ide|efidisk|tpmstate|unused)\d*$" if kind == "qemu" else r"^(rootfs|mp\d+|unused\d+)$")
    out = []
    for k, v in sorted(config.items()):
        if not keys.match(k):
            continue
        first = str(v).split(",", 1)[0]
        if ":" not in first or "media=cdrom" in str(v) or first.startswith("/") or first.startswith("none"):
            continue
        size = next((p[5:] for p in str(v).split(",")[1:] if p.startswith("size=")), None)
        out.append({"slot": k, "storage": first.split(":", 1)[0], "size": size})
    return out


def dry_run_delete(db, workload: Workload, *, confirm_name: str, purge: bool, destroy_unreferenced: bool, actor: str, correlation_id=None) -> Operation:
    node, cluster, target = _load(db, workload)
    kind = kind_of(workload)
    ctx = {"node_id": str(node.id), "node": node.name, "vmid": workload.vmid, "guest_kind": kind, "purge": bool(purge), "destroy_unreferenced": bool(destroy_unreferenced), "name": workload.name}
    op = create_operation(db, OP_TYPE, cluster_id=cluster.id, node_id=node.id, workload_id=workload.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    expected = (workload.name or "").strip()
    if not expected:
        reasons.append("this guest has no name in Proxmox, so there is nothing to type to confirm: give it a name first")
        blocking.append("SAFE-DELETE-001")
    elif (confirm_name or "").strip() != expected:
        reasons.append(f"type the guest's exact name ({expected}) to confirm you mean this one")
        blocking.append("SAFE-DELETE-001")
    if workload.ha_state:
        reasons.append(f"it is managed by Proxmox HA ({workload.ha_state}): remove it from HA first")
        blocking.append("SAFE-HA-001")
    if str(workload.vmid) == (self_vmid_from_env() or ""):
        reasons.append("this is the VM PyXie itself runs on: PyXie will not delete it")
        blocking.append("SAFE-DELETE-001")
    disks, snaps, config = [], [], {}
    try:
        client, _cred = build_pve_client(db, target)
        with client:
            config = _config(client, kind, node.name, workload.vmid)
            snaps = real_snapshots(gk.snapshots(client, workload, node.name))
            # the guest's own real-time state: PyXie's inventory and Proxmox's cluster list both lag by up to a minute, too long for a delete
            live_status = gk.status_current(client, workload, node.name).get("status") or workload.status
            if live_status != "stopped":
                reasons.append(f"{vm_label(workload)} is {live_status}: shut it down first, then delete it")
                blocking.append("SAFE-DELETE-001")
        if str(config.get("protection", "0")) in ("1", "true"):
            reasons.append("Proxmox protection is switched on for this guest: turn it off in Proxmox first (that switch exists to stop exactly this)")
            blocking.append("SAFE-DELETE-001")
        if config.get("lock"):
            reasons.append(f"Proxmox has the guest locked ('{config.get('lock')}'): another task is working on it")
            blocking.append("SAFE-LOCK-001")
        disks = owned_disks(config, kind)
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"could not read {vm_label(workload)} from Proxmox: {str(exc)[:140]}")
        blocking.append("SAFE-STORAGE-002")
    if not blocking:
        reasons.insert(0, f"{vm_label(workload)} on {node.name} is destroyed for good" + (f" with its {len(disks)} disk{'s' if len(disks) != 1 else ''} (" + ", ".join(f"{d['slot']} {d['size'] or '?'} on {d['storage']}" for d in disks) + ")" if disks else "") + ". This cannot be undone.")
    if snaps:
        reasons.append(f"its {len(snaps)} snapshot{'s' if len(snaps) != 1 else ''} go with it")
    if purge:
        reasons.append("it is also removed from backup jobs, replication and HA configuration, so nothing keeps pointing at it")
    reasons.append("backups already taken stay in the backup storage (Proxmox Backup Server or a backup directory): they are not deleted")
    if destroy_unreferenced:
        reasons.append("disks on the storages that carry this guest's ID but are not in its configuration are destroyed as well")
    if workload.tags and any(t.lower() in ("prod", "production", "critical") for t in workload.tags):
        reasons.append(f"it is tagged {', '.join(t for t in workload.tags if t.lower() in ('prod', 'production', 'critical'))}: be sure")
    lacking = missing_privileges(db, target, workload.vmid, ["VM.Allocate"])
    if lacking:
        reasons.append(permission_reason(lacking, "delete guests"))
        blocking.append("SAFE-PERMISSION-001")
    result = {"vmid": workload.vmid, "workload_name": workload.name, "node": node.name, "disks": disks, "snapshots": len(snaps), "eligible": not blocking, "reasons": reasons,
              "blocking_safety_rules": sorted(set(blocking))}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_delete(db, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise DeleteError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    workload = db.query(Workload).filter(Workload.id == op.workload_id).one()
    node, cluster, target = _load(db, workload)
    ctx = dict(op.context or {})
    kind = ctx["guest_kind"]
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            rc, _cred = build_pve_client(db, target)
            with rc:
                live = next((r for r in rc.cluster_resources("vm") or [] if int(r.get("vmid", -1)) == workload.vmid), None)
                config = _config(rc, kind, node.name, workload.vmid) if live else {}
                real_status = gk.status_current(rc, workload, node.name).get("status") if live else None
            if live is None:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"{vm_label(workload)} is no longer listed in Proxmox; nothing was changed")
            if (real_status or live.get("status")) != "stopped" or str(config.get("protection", "0")) in ("1", "true") or config.get("lock") or live.get("hastate") or (live.get("name") or "") != (workload.name or ""):
                release_locks_for_operation(db, op.id)
                return block_operation(db, op, blocking_safety_rules=["SAFE-DELETE-001"], actor="system")  # something changed since the preview: a person should look again
            try:
                acquire_lock(db, resource_type="workload", resource_id=workload.id, operation_id=op.id, reason=OP_TYPE)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")
        if op.status == "executing":
            with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as wc:
                if not op.pve_upid:
                    op.pve_upid = wc.delete_guest(node.name, workload.vmid, guest_type=kind, purge=ctx.get("purge", True), destroy_unreferenced=ctx.get("destroy_unreferenced", False))
                    db.commit()
                res = wc.wait_for_task(node.name, op.pve_upid, timeout=1800)
            op.pve_upid = None
            db.commit()
            if not res.finished_ok:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"Proxmox reported the delete did not succeed: {res.exit_status or res.raw}")
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            rc, _cred = build_pve_client(db, target)
            with rc:
                still = any(int(r.get("vmid", -1)) == workload.vmid for r in rc.cluster_resources("vm") or [])
            op.verification_result = {"vmid": workload.vmid, "gone": not still}
            db.commit()
            release_locks_for_operation(db, op.id)
            if still:
                return fail_operation(db, op, error=f"Proxmox still lists VM {workload.vmid} after the delete")
            try:
                from .discovery import run_discovery
                run_discovery(db, target, actor="operation")
            except Exception:  # noqa: BLE001
                pass
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.IRREVERSIBLE)
        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")
    except TaskTimeoutError as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"{exc}. The task may still finish on the host: look at the Tasks panel and the guest list before trying again. PyXie did not verify the result.")
    except MutationsDisabledError as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=friendly_error(exc) if "Permission check failed" in str(exc) else f"unexpected error: {exc}")
