"""One reviewable plan for shutting down, starting or force-stopping several guests at once.

Previewing N guests used to create N separate plans, each with its own approval in the banner and the Tasks panel. This is one
operation (`workload.bulk_lifecycle`) instead: the preview lists every guest that can take the action and every one that cannot (with
the reason), it is approved once, and it then runs the guests several at a time (Settings > Workers > Limits, default 3). Each guest still runs as its own normal
`workload.shutdown` / `workload.start` / `workload.force_stop` child operation (same checks, locks, read-back and audit trail as a single-guest
action), so nothing about how a guest is stopped or started differs. Like every batch here it can be cancelled between guests (the ones already running finish), never in the
middle of one, and it resumes after a worker restart.

Guests are independent, so one that fails does not stop the others: the plan finishes the rest, then ends as failed and names what did not
complete. Force Stop is never an automatic fallback: it is only reached by choosing it, with a justification, as its own plan.
"""

import logging
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from sqlalchemy.orm import Session

from . import rollback
from .labels import vm_label
from .lifecycle_workflow import (
    DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    LifecycleWorkflowError,
    blocking_reasons,
    dry_run_force_stop,
    dry_run_shutdown,
    dry_run_start,
    execute_lifecycle_action,
)
from .models import Cluster, Node, Operation, Policy, Workload
from .operations_engine import (
    OperationError,
    approve_operation,
    cancel_operation,
    check_cancel_requested,
    create_operation,
    enter_stage,
    fail_operation,
)

OPERATION_TYPE = "workload.bulk_lifecycle"
MAX_GUESTS = 50
# action -> (child operation type, status a guest must be in)
ACTIONS = {
    "shutdown": ("workload.shutdown", "running"),
    "start": ("workload.start", "stopped"),
    "force_stop": ("workload.force_stop", "running"),
}
_EXECUTABLE = ("approved", "revalidating", "executing", "monitoring", "verifying")


log = logging.getLogger(__name__)

POLICY_KEY = "limits.power_parallel"
DEFAULT_PARALLEL = 3
MAX_PARALLEL = 10
POLL_SECONDS = 2.0


class BulkLifecycleError(Exception):
    pass


def clamp_parallel(value) -> int:
    """Pure. How many guests may be started or stopped at once; anything unreadable falls back to the default."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = DEFAULT_PARALLEL
    return max(1, min(MAX_PARALLEL, v))


def power_parallel(db: Session) -> int:
    p = db.query(Policy).filter(Policy.key == POLICY_KEY, Policy.scope_type == "organization").one_or_none()
    return clamp_parallel(p.value.get("count") if p and isinstance(p.value, dict) else DEFAULT_PARALLEL)


def classify(workloads, action: str) -> tuple[list[dict], list[dict]]:
    """Pure (no database): split the guests into the ones the action will run on and the ones it cannot, with the reasons."""
    if action not in ACTIONS:
        raise BulkLifecycleError("action must be 'shutdown', 'start' or 'force_stop'")
    child_type, require_status = ACTIONS[action]
    plan: list[dict] = []
    skipped: list[dict] = []
    for w in workloads:
        item = {"workload_id": str(w.id), "vmid": w.vmid, "name": w.name, "node_id": str(w.node_id), "power": w.status}
        reasons, rules = blocking_reasons(w, child_type, require_status)
        if reasons:
            skipped.append({**item, "reasons": reasons, "blocking_safety_rules": rules})
        else:
            plan.append(item)
    return plan, skipped


def dry_run_bulk_lifecycle(
    db: Session, workloads: list[Workload], *, action: str, actor: str,
    timeout_seconds: int = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS, justification: str | None = None,
) -> Operation:
    if action not in ACTIONS:
        raise BulkLifecycleError("action must be 'shutdown', 'start' or 'force_stop'")
    if action == "force_stop" and not (justification or "").strip():
        raise BulkLifecycleError("a justification is required to force-stop workloads")
    if not workloads:
        raise BulkLifecycleError("no workloads selected")
    if len(workloads) > MAX_GUESTS:
        raise BulkLifecycleError(f"select at most {MAX_GUESTS} workloads at a time")
    if any(w.is_missing for w in workloads):
        raise BulkLifecycleError("a selected workload no longer exists in Proxmox")
    if len({w.cluster_id for w in workloads}) > 1:
        raise BulkLifecycleError("the selected workloads are in different clusters; do one cluster at a time")

    cluster = db.query(Cluster).filter(Cluster.id == workloads[0].cluster_id).one()
    nodes = {n.id: n.name for n in db.query(Node).filter(Node.cluster_id == cluster.id).all()}
    plan, skipped = classify(workloads, action)
    for it in plan + skipped:
        it["node"] = nodes.get(next(w.node_id for w in workloads if str(w.id) == it["workload_id"]))

    context = {
        "mode": "bulk_lifecycle", "action": action, "plan": plan, "skipped": skipped,
        "completed": [], "results": {},
    }
    if action == "shutdown":
        context["shutdown_timeout_seconds"] = timeout_seconds
    if justification:
        context["justification"] = justification
    op = create_operation(db, OPERATION_TYPE, cluster_id=cluster.id, context=context, created_by=actor)

    dry_run_result = {"action": action, "plan": plan, "skipped": skipped, "eligible": bool(plan), "count": len(plan), "reasons": [], "blocking_safety_rules": []}
    if not plan:
        rules = sorted({r for s in skipped for r in s.get("blocking_safety_rules", [])})
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=dry_run_result, blocking_safety_rules=rules, actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=dry_run_result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def _child_for(db: Session, op: Operation, item: dict, action: str, ctx: dict) -> Operation:
    """The child operation for one guest: reuse one a worker restart left behind, else create it."""
    existing = (
        db.query(Operation)
        .filter(Operation.parent_operation_id == op.id, Operation.workload_id == uuid.UUID(item["workload_id"]))
        .order_by(Operation.created_at.desc()).first()
    )
    if existing is not None:
        return existing
    workload = db.query(Workload).filter(Workload.id == uuid.UUID(item["workload_id"])).one()
    actor = f"{OPERATION_TYPE}:{op.id}"
    kw = {"actor": actor, "parent_operation_id": op.id, "correlation_id": op.correlation_id}
    if action == "shutdown":
        return dry_run_shutdown(db, workload, timeout_seconds=ctx.get("shutdown_timeout_seconds", DEFAULT_SHUTDOWN_TIMEOUT_SECONDS), **kw)
    if action == "start":
        return dry_run_start(db, workload, **kw)
    return dry_run_force_stop(db, workload, justification=ctx.get("justification", ""), **kw)


def execute_bulk_lifecycle(db: Session, operation_id) -> Operation:
    """Resumable: progress is kept in op.context, and each guest's own child operation is looked up before one is created."""
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise BulkLifecycleError(f"operation {operation_id} not found")
    ctx = dict(op.context or {})
    action = ctx.get("action", "")
    plan = ctx.get("plan", [])
    results: dict = dict(ctx.get("results", {}))
    approved_by = f"{OPERATION_TYPE}:{op.id}"

    def save():
        op.context = {**(op.context or {}), "plan": plan, "results": results, "completed": [k for k, v in results.items() if v.get("status") == "completed"]}
        db.commit()

    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            op = enter_stage(db, op, status="executing", stage="executing")

        if op.status == "executing":
            k = power_parallel(db)
            pending = [it for it in plan if results.get(it["workload_id"], {}).get("status") not in ("completed", "failed", "skipped")]
            running: dict = {}  # future -> (item, child id)
            cancelled = None

            def settle(item, status, error, rules, child_id):
                wid = item["workload_id"]
                if status == "completed":
                    results[wid] = {"status": "completed", "operation_id": str(child_id)}
                elif status == "blocked":
                    results[wid] = {"status": "skipped", "error": f"{vm_label(item)} was busy or changed state ({', '.join(rules) or 'blocked'})", "operation_id": str(child_id)}
                elif status == "cancelled":
                    results[wid] = {"status": "skipped", "error": "cancelled", "operation_id": str(child_id)}
                else:
                    results[wid] = {"status": "failed", "error": error or status, "operation_id": str(child_id)}
                save()

            def _exec(child_id):
                from .db import SessionLocal
                sess = SessionLocal()
                try:
                    c = execute_lifecycle_action(sess, child_id)
                    return c.status, c.error, list(c.blocking_safety_rules or [])
                finally:
                    sess.close()

            with ThreadPoolExecutor(max_workers=max(1, k)) as pool:
                while pending or running:
                    if cancelled is None:
                        cancelled = check_cancel_requested(db, op)  # between guests only; the ones already running finish
                    if cancelled is None and pending and len(running) < k:
                        item = pending.pop(0)
                        try:
                            child = _child_for(db, op, item, action, ctx)
                        except LifecycleWorkflowError as exc:
                            settle(item, "failed", str(exc), [], "")
                            continue
                        if child.status == "blocked":
                            results[item["workload_id"]] = {"status": "skipped", "error": "; ".join((child.dry_run_result or {}).get("reasons", [])) or "blocked"}
                            save()
                            continue
                        if child.status == "awaiting_approval":
                            child = approve_operation(db, child, approved_by=approved_by)
                        if child.status in _EXECUTABLE:
                            running[pool.submit(_exec, child.id)] = (item, child.id)
                        else:
                            settle(item, child.status, child.error, list(child.blocking_safety_rules or []), child.id)
                        continue
                    if not running:
                        break
                    done, _ = wait(list(running), timeout=POLL_SECONDS, return_when=FIRST_COMPLETED)
                    for fut in done:
                        item, child_id = running.pop(fut)
                        try:
                            status, error, rules = fut.result()
                        except Exception as exc:  # noqa: BLE001
                            log.exception("guest power action %s crashed", child_id)
                            db.rollback()
                            status, error, rules = "failed", f"unexpected error: {exc}", []
                        db.expire_all()
                        settle(item, status, error, rules, child_id)
            if cancelled is not None:
                return cancelled
            op = enter_stage(db, op, status="verifying", stage="verifying")

        if op.status == "verifying":
            failed = [(it, results.get(it["workload_id"], {})) for it in plan if results.get(it["workload_id"], {}).get("status") == "failed"]
            if failed:
                names = "; ".join(f"{vm_label(it)}: {r.get('error', 'failed')}" for it, r in failed[:5])
                more = f" (and {len(failed) - 5} more)" if len(failed) > 5 else ""
                return fail_operation(db, op, error=f"{len(failed)} of {len(plan)} did not complete: {names}{more}")
            classification = rollback.IRREVERSIBLE if action == "force_stop" else rollback.AUTO_REVERSIBLE
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=classification)

        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status}, stage={op.stage})")
    except Exception as exc:  # noqa: BLE001
        return fail_operation(db, op, error=f"unexpected error at stage {op.stage}: {exc}")
