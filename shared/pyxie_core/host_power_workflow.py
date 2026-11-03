"""Shut a host down (host.shutdown): only while it is in maintenance mode, with nothing running on it and the cluster staying quorate.

Same pre-checks as a reboot (reboot_workflow.dry_run_reboot) plus maintenance mode and the host name typed to confirm. PyXie cannot power a host
back on: the operation completes once the host is confirmed offline, and says so.
"""

import time

from sqlalchemy.orm import Session

from . import rollback
from .credentials import load_pve_credentials
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .maintenance import _quorum_after_removal
from .models import Cluster, Node, Operation, PveTarget, PveTask, Workload
from .operation_log import log_stage
from .operations_engine import approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient
from .reboot_workflow import POLL_INTERVAL_SECONDS, _node_status_tolerant

OFFLINE_TIMEOUT_SECONDS = 300


def _context(db: Session, node: Node):
    cluster = db.query(Cluster).filter(Cluster.id == node.cluster_id).one()
    return cluster, db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()


def dry_run_shutdown(db: Session, node: Node, *, actor: str, correlation_id=None) -> Operation:
    cluster, target = _context(db, node)
    op = create_operation(db, "host.shutdown", cluster_id=cluster.id, node_id=node.id, correlation_id=correlation_id,
                          context={"node": node.name, "confirm_name": node.name}, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    if not node.maintenance_mode:
        reasons.append("Shut Down is only available while the host is in maintenance mode: use Evacuate / Maint Mode first")
        blocking.append("SAFE-MAINT-002")
    quorum = _quorum_after_removal(db, cluster, target, node.name, avoid_node_id=node.id)
    if quorum.get("evaluated") and not quorum["quorum_holds"]:
        reasons.append("shutting this host down would leave the cluster without enough votes to keep quorum")
        blocking.append("SAFE-QUORUM-001")
    running = db.query(Workload).filter(Workload.node_id == node.id, Workload.is_missing.is_(False), Workload.status == "running").count()
    if running:
        reasons.append(f"{running} guest(s) are still running on this host: move or stop them first")
        blocking.append("SAFE-ROLLBACK-001")
    active = db.query(PveTask).filter(PveTask.node_id == node.id, PveTask.status == "running").count()
    if active:
        reasons.append(f"{active} Proxmox task(s) are still running on this host: wait for them to finish")
        blocking.append("SAFE-BACKUP-001")
    if not blocking:
        if quorum.get("evaluated"):
            reasons.append("Checked now: nothing running here and the cluster stays quorate without this host.")
        reasons.append("The host stays OFF until someone powers it on at the machine. PyXie cannot start it again.")
    result = {"node": node.name, "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": blocking, "confirm_name": node.name}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=blocking, actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_shutdown(db: Session, operation_id) -> Operation:
    """Resumable: the command-sent flag is committed with the stage change, so a restarted worker never sends a second shutdown."""
    op = db.query(Operation).filter(Operation.id == operation_id).one()
    node = db.query(Node).filter(Node.id == op.node_id).one()
    cluster, target = _context(db, node)
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            log_stage(op.id, f"Approved. Re-checking {node.name}: maintenance mode, nothing running, quorum.")
            if not node.maintenance_mode:
                return block_operation(db, op, blocking_safety_rules=["SAFE-MAINT-002"], actor="system")
            if db.query(PveTask).filter(PveTask.node_id == node.id, PveTask.status == "running").count():
                return block_operation(db, op, blocking_safety_rules=["SAFE-BACKUP-001"], actor="system")
            if db.query(Workload).filter(Workload.node_id == node.id, Workload.is_missing.is_(False), Workload.status == "running").count():
                return block_operation(db, op, blocking_safety_rules=["SAFE-ROLLBACK-001"], actor="system")
            reachable, _online, _ = _node_status_tolerant(db, target, node.name, avoid_node_id=node.id)
            if not reachable:
                return block_operation(db, op, blocking_safety_rules=["SAFE-QUORUM-001"], actor="system")
            quorum = _quorum_after_removal(db, cluster, target, node.name, avoid_node_id=node.id)
            if quorum.get("evaluated") and not quorum["quorum_holds"]:
                return block_operation(db, op, blocking_safety_rules=["SAFE-QUORUM-001"], actor="system")
            try:
                acquire_lock(db, resource_type="node", resource_id=node.id, operation_id=op.id, reason="host.shutdown", ttl_seconds=900)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")
        if op.status == "executing" and not (op.context or {}).get("shutdown_command_sent"):
            creds = load_pve_credentials(db, target, "maintenance", avoid_node_id=node.id)
            log_stage(op.id, f"Checks passed. Sending the shutdown command to {node.name}.")
            with PveMaintenanceClient(creds) as w:
                w.shutdown_node(node.name)
            op = enter_stage(db, op, status="monitoring", stage="monitoring", context={**(op.context or {}), "shutdown_command_sent": True})
        if op.status == "monitoring":
            log_stage(op.id, f"Shutdown command sent. Waiting for {node.name} to go offline.")
            deadline = time.monotonic() + OFFLINE_TIMEOUT_SECONDS
            offline = False
            while time.monotonic() < deadline:
                reachable, online, _ = _node_status_tolerant(db, target, node.name, avoid_node_id=node.id)
                if reachable and not online:  # the cluster is answering and no longer lists this host as online
                    offline = True
                    break
                time.sleep(POLL_INTERVAL_SECONDS)
            release_locks_for_operation(db, op.id)
            if not offline:
                return fail_operation(db, op, error=f"{node.name} did not go offline within {OFFLINE_TIMEOUT_SECONDS}s of the shutdown command: check it at the machine")
            op.verification_result = {"offline": True}
            db.commit()
            log_stage(op.id, f"{node.name} is offline. It stays off until someone powers it on at the machine.")
            try:
                from .discovery import run_discovery
                run_discovery(db, target, actor="operation")
            except Exception:  # noqa: BLE001
                pass
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.IRREVERSIBLE)
        raise RuntimeError(f"operation {op.id} reached an unexpected state ({op.status})")
    except MutationsDisabledError as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error: {exc}")
