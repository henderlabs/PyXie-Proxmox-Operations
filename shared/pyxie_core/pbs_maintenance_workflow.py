"""pbs.maintenance: the Full Maintenance run for a Proxmox Backup Server: install updates, reboot, verify.

A backup server has no guests to move, so this is two of the single-step operations in order, run under one approval
of the whole cycle: pbs.update (skipped when nothing is waiting) and then pbs.reboot. Each child keeps its own safety
checks (idle, plan unchanged, lock, read-back); a blocked or failed child stops the run and the server is left as it is.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import rollback
from .audit import write_audit_event
from .models import Operation, ProtectionTarget
from .operation_log import log_stage
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pbs_reboot_workflow import dry_run_pbs_reboot, execute_pbs_reboot
from .pbs_update_workflow import dry_run_pbs_update

now = lambda: datetime.now(timezone.utc)


def dry_run_pbs_maintenance(db: Session, target: ProtectionTarget, *, actor: str) -> Operation:
    op = create_operation(db, "pbs.maintenance", created_by=actor, context={"protection_target_id": str(target.id), "target": target.name, "node": target.name})
    # Both previews are read-only looks; they are closed again, the run creates fresh ones when it starts.
    upd = dry_run_pbs_update(db, target, actor=actor, check_only=True)
    reboot = dry_run_pbs_reboot(db, target, actor=actor)
    reasons: list[str] = []
    rules: list[str] = []
    for child in (upd, reboot):
        for r in (child.dry_run_result or {}).get("blocking_safety_rules", []) or []:
            if r not in rules:
                rules.append(r)
    for child in (upd, reboot):
        if child.status == "awaiting_approval":
            child.status = "cancelled"
        if hasattr(child, "dismissed"):
            child.dismissed = True  # these two previews are only the parent's look; keep them out of the task list
        db.commit()
    pkgs = (upd.dry_run_result or {}).get("planned_package_count") or 0
    if rules:
        reasons.extend((upd.dry_run_result or {}).get("reasons", []) + (reboot.dry_run_result or {}).get("reasons", []))
    else:
        reasons.append(f"Full maintenance of {target.name}: " + (f"install {pkgs} package(s), " if pkgs else "no packages waiting, ") + "reboot, then check the server is back on the newest kernel with its datastores.")
        reasons.append("The server is offline for about 2 to 3 minutes during the reboot; backups starting then fail and run again at their next schedule.")
    result = {"target": target.name, "node": target.name, "eligible": not rules, "reasons": reasons, "blocking_safety_rules": rules,
              "planned_package_count": pkgs, "planned_packages": (upd.dry_run_result or {}).get("planned_packages", [])}
    ctx = {"protection_target_id": str(target.id), "target": target.name, "node": target.name}
    if rules:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=rules, context=ctx, actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, context=ctx, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def _run_child(db: Session, parent: Operation, key: str, make, execute) -> Operation | None:
    """Create (once), approve under the parent's approval and run one child; remembers its id so a restart resumes it."""
    ctx = dict(parent.context or {})
    child = db.query(Operation).filter(Operation.id == ctx[key]).one_or_none() if ctx.get(key) else None
    if child is None:
        child = make()
        ctx[key] = str(child.id)
        parent.context = ctx
        db.commit()
        if child.status == "awaiting_approval":
            child = approve_operation(db, child, approved_by=f"pbs.maintenance:{parent.id}")
    if child.status in ("approved", "revalidating", "executing", "verifying"):
        child = execute(db, child.id)
    return child


def execute_pbs_maintenance(db: Session, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise OperationError(f"operation {operation_id} not found")
    if op.status not in ("approved", "executing"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    ctx = op.context or {}
    target = db.query(ProtectionTarget).filter(ProtectionTarget.id == ctx["protection_target_id"]).one()
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="executing", stage="executing")
        actor = f"pbs.maintenance:{op.id}"
        pkgs = (op.dry_run_result or {}).get("planned_package_count") or 0
        if pkgs:
            log_stage(op.id, f"Step 1 of 2: installing {pkgs} package(s) on {target.name}.")
            child = _run_child(db, op, "update_id", lambda: dry_run_pbs_update(db, target, actor=actor, check_only=False), __import__("pyxie_core.pbs_update_workflow", fromlist=["x"]).execute_pbs_update)
            if child is None or child.status != "completed":
                if child is not None and child.status == "blocked":
                    return block_operation(db, op, blocking_safety_rules=child.blocking_safety_rules or ["SAFE-PBS-001"])
                return fail_operation(db, op, error=f"the update step ended as {child.status if child else 'missing'}: {getattr(child, 'error', None) or ''}".strip())
        else:
            log_stage(op.id, "Step 1 of 2: no packages waiting, skipping the update.")
        log_stage(op.id, f"Step 2 of 2: rebooting {target.name}.")
        child = _run_child(db, op, "reboot_id", lambda: dry_run_pbs_reboot(db, target, actor=actor, parent_operation_id=op.id), execute_pbs_reboot)
        if child is None or child.status != "completed":
            if child is not None and child.status == "blocked":
                return block_operation(db, op, blocking_safety_rules=child.blocking_safety_rules or ["SAFE-PBS-001"])
            return fail_operation(db, op, error=f"the reboot step ended as {child.status if child else 'missing'}: {getattr(child, 'error', None) or ''}".strip())
        op.verification_result = {"updated_packages": pkgs, "reboot": child.verification_result}
        db.commit()
        write_audit_event(db, event_category="protection", event_type="pbs.maintenance", actor=op.approved_by or "system", actor_type="user",
                          metadata={"target": target.name, "packages": pkgs})
        return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE)
    except Exception as exc:  # noqa: BLE001
        return fail_operation(db, op, error=f"unexpected error: {exc}")
