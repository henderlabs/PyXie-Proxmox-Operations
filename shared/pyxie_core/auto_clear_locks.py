"""Automatic clearing of stale backup and snapshot locks (off by default; Platform > Policies).

A guest lock that no task is running for makes the nightly backup fail and blocks migration. When the operator turns this on, a lock of
type backup or snapshot that has been stale for the configured time is cleared through the ordinary host.clear_locks operation (same
dry run, same checks, same audit trail), approved by PyXie itself. Never a migrate or any other lock type, never more than
MAX_RUNS_PER_HOUR runs an hour, never when Proxmox changes are disabled, and only on a node whose host wrapper is 1.2.0 or newer.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from . import host_kit
from .findings import _org_policy
from .models import AppSettings, Node, Operation
from .operations_engine import approve_operation, cancel_operation
from .vm_locks import MIN_STALE_AFTER_MINUTES, locked_guests

log = logging.getLogger(__name__)

AUTOMATIC_ACTOR = "PyXie (automatic)"
SAFE_LOCKS = frozenset({"backup", "snapshot"})
MAX_RUNS_PER_HOUR = 5
NEEDS_WRAPPER = "1.2.0"
_LIVE = ("pending", "dry_run", "awaiting_approval", "approved", "revalidating", "executing", "verifying", "monitoring")


def candidates_by_node(stale: list[dict]) -> dict:
    """{node name: [vmid, ...]} for the stale guests whose lock is one this feature may clear. Pure: `stale` rows are locked_guests() output."""
    out: dict = {}
    for g in stale:
        if g["state"] == "stale" and g["lock"] in SAFE_LOCKS and g["node"]:
            out.setdefault(g["node"], []).append(g["workload"].vmid)
    return out


def plan_is_safe(will_clear: list[dict], wanted_vmids: set) -> bool:
    """Pure. The dry run may clear only what we asked for: every guest on it a backup/snapshot lock that PyXie also sees as stale."""
    return bool(will_clear) and all(r.get("lock") in SAFE_LOCKS and r.get("vmid") in wanted_vmids for r in will_clear)


def evaluate(db: Session, enqueue=None, now: datetime | None = None) -> dict:
    from .clear_lock_workflow import dry_run_clear_locks

    now = now or datetime.now(timezone.utc)
    if not _org_policy(db, "locks.auto_clear_enabled", "enabled", False):
        return {"result": "off"}
    if not bool(db.query(AppSettings.pve_mutations_enabled).filter(AppSettings.id == 1).scalar()):
        return {"result": "skipped", "reason": "Proxmox changes are disabled"}
    if enqueue is None:
        return {"result": "skipped", "reason": "the worker queue is not available"}
    minutes = max(MIN_STALE_AFTER_MINUTES, _org_policy(db, "locks.auto_clear_minutes", "minutes", 60))
    wanted = candidates_by_node(locked_guests(db, now, minutes))
    if not wanted:
        return {"result": "nothing", "minutes": minutes}
    runs = db.query(Operation).filter(Operation.operation_type_id == "host.clear_locks", Operation.created_by == AUTOMATIC_ACTOR,
                                      Operation.created_at >= now - timedelta(hours=1)).count()
    done: list[dict] = []
    for node in db.query(Node).filter(Node.is_missing.is_(False)).all():
        vmids = wanted.get(node.name)
        if not vmids:
            continue
        if runs >= MAX_RUNS_PER_HOUR:
            done.append({"node": node.name, "result": "skipped", "reason": f"{MAX_RUNS_PER_HOUR} automatic runs already this hour"})
            continue
        if node.wrapper_version is None or host_kit.is_outdated(node.wrapper_version, NEEDS_WRAPPER):
            done.append({"node": node.name, "result": "skipped", "reason": f"host wrapper is {node.wrapper_version or 'not installed'}; clearing needs {NEEDS_WRAPPER}"})
            continue
        if db.query(Operation).filter(Operation.operation_type_id == "host.clear_locks", Operation.node_id == node.id, Operation.status.in_(_LIVE)).count():
            done.append({"node": node.name, "result": "skipped", "reason": "a lock clearing is already in progress or waiting"})
            continue
        runs += 1
        op = dry_run_clear_locks(db, node, actor=AUTOMATIC_ACTOR)
        if op.status != "awaiting_approval":
            done.append({"node": node.name, "result": "not_needed", "status": op.status})
            continue
        if (op.dry_run_result or {}).get("stop_tasks") or not plan_is_safe((op.dry_run_result or {}).get("will_clear") or [], set(vmids)):
            cancel_operation(db, op, actor=AUTOMATIC_ACTOR, note="Automatic clearing only handles backup and snapshot locks PyXie also sees as stale; left for a person.")
            done.append({"node": node.name, "result": "left_for_review"})
            continue
        op.context = {**(op.context or {}), "auto_approved": True, "auto_cleared_vmids": sorted(vmids)}
        db.commit()
        op = approve_operation(db, op, approved_by=AUTOMATIC_ACTOR)
        enqueue(op)
        done.append({"node": node.name, "result": "approved", "operation_id": str(op.id), "vmids": sorted(vmids)})
        log.info("automatic lock clearing approved on %s for %s", node.name, vmids)
    return {"result": "ran", "nodes": done}
