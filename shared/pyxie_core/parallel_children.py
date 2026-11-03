"""Run an evacuation's guest moves several at a time (multi-worker phase 3), never more than the limits allow.

The parent operation's own loop stays in charge: it prepares each move (dry run + approval, quick, one after another, on its own database session) and
hands the slow part (execute) to a small thread pool, each thread with a session of its own. With the default of 1 move at a time this is the same
order and the same behavior as the old one-after-another loop. A failure stops new moves from starting, lets the ones already running finish, and then
reports the first failure; a cancel is passed to the running moves by the cancel endpoint and ends the run as cancelled.
"""

import logging
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from sqlalchemy.orm import Session

from .slots import DEFAULT_LIMIT, MAX_LIMIT, limit_for
from .models import Operation, Policy

log = logging.getLogger(__name__)

POLICY_KEY = "limits.evac_parallel"
POLL_SECONDS = 2.0


def parallel_k(db: Session) -> int:
    p = db.query(Policy).filter(Policy.key == POLICY_KEY, Policy.scope_type == "organization").one_or_none()
    try:
        v = int(p.value.get("count")) if p and isinstance(p.value, dict) else DEFAULT_LIMIT
    except (TypeError, ValueError):
        v = DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, v))


def pick_next(pending: list, running_dest: dict, running_total: int, k: int, out_limit: int, in_limit: int, dest_of) -> int | None:
    """Pure. Index in `pending` of the first move that may start now: fewer than `k` running, fewer than `out_limit` leaving the source host, and fewer
    than `in_limit` arriving at its destination. Order is kept as far as the limits allow."""
    if running_total >= k or running_total >= out_limit:
        return None
    for i, item in enumerate(pending):
        if running_dest.get(dest_of(item), 0) < in_limit:
            return i
    return None


def run_parallel(db: Session, op, items: list, *, k: int, prepare, execute, on_done, on_failure, check_cancel, dest_of):
    """Returns None when every move completed, else the finished parent operation to return from the workflow.

    prepare(item) -> ("done", None) | ("exec", child) | ("stop", result_op)    (caller's thread, on `db`)
    execute(session, child_id) -> child                                       (pool thread, own session)
    on_done(item)                       record progress for a completed move  (caller's thread)
    on_failure(item, child) -> op       the parent's failure for the first failed move
    check_cancel() -> op | None         the parent's cooperative cancel check
    """
    from .db import SessionLocal

    out_limit, in_limit = limit_for(db, "node_out"), limit_for(db, "node_in")
    pending = list(items)
    running: dict = {}  # future -> (item, child_id, destination)
    running_dest: dict = {}
    failure = None  # (item, child)
    stop_result = None
    cancelled = None

    def _exec(child_id):
        s = SessionLocal()
        try:
            return execute(s, child_id).status
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=max(1, k)) as pool:
        while pending or running:
            healthy = failure is None and stop_result is None and cancelled is None
            if healthy:
                c = check_cancel()
                if c is not None:
                    cancelled = c
                    healthy = False
            if healthy:
                idx = pick_next(pending, running_dest, len(running), k, out_limit, in_limit, dest_of)
                if idx is not None:
                    item = pending.pop(idx)
                    kind, payload = prepare(item)
                    if kind == "done":
                        on_done(item)
                    elif kind == "stop":
                        stop_result = payload
                    else:
                        dest = dest_of(item)
                        fut = pool.submit(_exec, payload.id)
                        running[fut] = (item, payload.id, dest)
                        running_dest[dest] = running_dest.get(dest, 0) + 1
                    continue
            if not running:
                break  # nothing running and (unhealthy or nothing startable): finish below
            done, _ = wait(list(running), timeout=POLL_SECONDS, return_when=FIRST_COMPLETED)
            for fut in done:
                item, child_id, dest = running.pop(fut)
                running_dest[dest] = max(0, running_dest.get(dest, 1) - 1)
                try:
                    status = fut.result()
                except Exception:  # noqa: BLE001
                    log.exception("parallel move %s crashed", child_id)
                    status = "failed"
                    db.rollback()
                db.expire_all()
                child = db.query(Operation).filter(Operation.id == child_id).one()
                if status == "completed":
                    on_done(item)
                elif status == "cancelled":
                    cancelled = cancelled or child
                elif failure is None:
                    failure = (item, child)
    if stop_result is not None:
        return stop_result
    if failure is not None:
        return on_failure(*failure)
    if cancelled is not None:
        return cancelled if getattr(cancelled, "status", None) in ("cancelled",) and cancelled.operation_type_id == op.operation_type_id else _as_parent_cancel(db, op)
    return None


def _as_parent_cancel(db: Session, op):
    from .locks import release_locks_for_operation
    from .operations_engine import cancel_operation
    release_locks_for_operation(db, op.id)
    return cancel_operation(db, op, note="Guests already moved stay where they are.")
