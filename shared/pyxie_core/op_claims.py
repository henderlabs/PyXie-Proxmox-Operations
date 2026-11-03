"""Operation claims, heartbeat, lock leases, waiting on a busy resource, and taking over a task whose worker died (multi-worker phase 2).

- A worker that starts an operation stamps it (claimed_by = the worker's stable slot id such as "bulk-1", claimed_at, heartbeat_at) and refreshes
  heartbeat_at every 15 s from a thread inside the job process. A second worker never runs an operation whose claim is fresh.
- Locks are taken for a short lease (10 min) and the same heartbeat renews the lease of every lock the operation (and its children) holds, so a
  worker that dies frees its hosts and guests within minutes instead of the worst-case hours.
- A task you started that finds its guest or host busy WAITS (status stays approved, `context.deferred`) and is retried, instead of stopping with
  SAFE-LOCK-001. Automatic work and steps inside a bigger run keep the old behavior.
"""

import logging
import os
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, update

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 15
STALE_SECONDS = 120
LEASE_SECONDS = 600
DEFER_MAX_SECONDS = 6 * 3600
DEFER_BACKOFF = (60, 120, 240, 300)  # seconds between retries, last value repeats
MAX_RECLAIMS = 3
LOCK_RULE = "SAFE-LOCK-001"


def worker_id() -> str:
    return os.environ.get("PYXIE_WORKER_ID", "bulk-1")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# -- pure -----------------------------------------------------------------------------------------------------------------------------

def claim_is_live(claimed_by, heartbeat_at, now: datetime, *, me: str | None = None) -> bool:
    """Is the claim held by ANOTHER worker that is still alive? Mine (a worker restarts with the same slot id) is never live: it is a dead predecessor."""
    if not claimed_by or claimed_by == me:
        return False
    return heartbeat_at is not None and (now - heartbeat_at) < timedelta(seconds=STALE_SECONDS)


def should_defer(op_status: str | None, approved_by: str | None, parent_id, rules, started_at: datetime | None, now: datetime) -> bool:
    """Pure. A lock-contention stop becomes a wait only for a task a person approved (an e-mail address), not one run inside a bigger operation
    or by automatic work, and only for DEFER_MAX_SECONDS."""
    if list(rules or []) != [LOCK_RULE] or parent_id is not None:
        return False
    if not approved_by or "@" not in approved_by:
        return False
    return started_at is None or (now - started_at).total_seconds() < DEFER_MAX_SECONDS


def next_retry(deferrals: int, now: datetime) -> datetime:
    return now + timedelta(seconds=DEFER_BACKOFF[min(deferrals, len(DEFER_BACKOFF) - 1)])


# -- claims ---------------------------------------------------------------------------------------------------------------------------

def try_claim(db, operation_id, me: str | None = None) -> bool:
    """Atomically claim an operation unless another live worker holds it."""
    from .models import Operation
    me = me or worker_id()
    now = _now()
    res = db.execute(update(Operation).where(
        Operation.id == operation_id,
        or_(Operation.claimed_by.is_(None), Operation.claimed_by == me, Operation.heartbeat_at.is_(None), Operation.heartbeat_at < now - timedelta(seconds=STALE_SECONDS)),
    ).values(claimed_by=me, claimed_at=now, heartbeat_at=now))
    db.commit()
    return res.rowcount == 1


def release_claim(db, operation_id) -> None:
    from .models import Operation
    db.execute(update(Operation).where(Operation.id == operation_id).values(claimed_by=None, heartbeat_at=None))
    db.commit()


def _family_ids(db, operation_id) -> list:
    """The operation and every descendant (an evacuation's guest moves hold locks of their own)."""
    from .models import Operation
    ids, frontier = [operation_id], [operation_id]
    for _ in range(4):
        kids = [i for (i,) in db.query(Operation.id).filter(Operation.parent_operation_id.in_(frontier)).all()] if frontier else []
        ids += kids
        frontier = kids
    return ids


def touch(db, operation_id) -> None:
    """One heartbeat: stamp the operation and renew the lease of every lock it or its children hold."""
    from .models import CapacitySlot, Operation, ResourceLock
    now = _now()
    db.execute(update(Operation).where(Operation.id == operation_id).values(heartbeat_at=now))
    db.query(ResourceLock).filter(ResourceLock.operation_id.in_(_family_ids(db, operation_id)), ResourceLock.released_at.is_(None)).update(
        {"expires_at": now + timedelta(seconds=LEASE_SECONDS)}, synchronize_session=False)
    db.query(CapacitySlot).filter(CapacitySlot.operation_id.in_(_family_ids(db, operation_id)), CapacitySlot.released_at.is_(None)).update(
        {"expires_at": now + timedelta(seconds=LEASE_SECONDS)}, synchronize_session=False)
    db.commit()


def start_heartbeat(operation_id):
    """Run touch() every HEARTBEAT_SECONDS on a thread of its own until the returned function is called. Also switches this process's lock
    acquisition to short leases (the heartbeat is what keeps them alive)."""
    from . import locks
    from .db import SessionLocal
    locks.enable_lease()
    stop = threading.Event()

    def run():
        while not stop.wait(HEARTBEAT_SECONDS):
            try:
                s = SessionLocal()
                try:
                    touch(s, operation_id)
                finally:
                    s.close()
            except Exception:  # noqa: BLE001 -- keep trying: a missed beat is harmless, a dead thread would let the leases lapse
                log.exception("operation heartbeat failed for %s", operation_id)

    t = threading.Thread(target=run, daemon=True, name=f"op-heartbeat-{operation_id}")
    t.start()
    return stop.set


# -- waiting on a busy resource -------------------------------------------------------------------------------------------------------

def find_blocker(db, op) -> dict | None:
    """Best label for what holds the guest or host this operation wants: another operation's active lock on its guest, host or cluster."""
    from .models import Operation, ResourceLock
    from .notifications import OPERATION_LABELS
    now = _now()
    ids = [i for i in (op.workload_id, op.node_id, op.cluster_id) if i is not None]
    if not ids:
        return None
    mine = set(_family_ids(db, op.id))
    from .slots import active_slots_on_node
    if op.node_id is not None:  # moves in flight on this host (capacity slots)
        for s in active_slots_on_node(db, op.node_id):
            if s.operation_id not in mine:
                other = db.query(Operation).filter(Operation.id == s.operation_id).one_or_none()
                if other is not None:
                    return {"id": str(other.id), "operation_type_id": other.operation_type_id, "label": OPERATION_LABELS.get(other.operation_type_id, other.operation_type_id), "running_minutes": None, "progress_pct": None}
    for lock in db.query(ResourceLock).filter(ResourceLock.resource_id.in_(ids), ResourceLock.released_at.is_(None), ResourceLock.expires_at > now).all():
        if lock.operation_id in mine:
            continue
        other = db.query(Operation).filter(Operation.id == lock.operation_id).one_or_none()
        if other is None:
            continue
        return {"id": str(other.id), "operation_type_id": other.operation_type_id, "label": OPERATION_LABELS.get(other.operation_type_id, other.operation_type_id),
                "running_minutes": None, "progress_pct": (other.progress or {}).get("pct") if isinstance(other.progress, dict) else None}
    return None


def defer_operation(db, op):
    """Put a lock-blocked operation back to waiting: status approved, locks released, retry time set. One audit event the first time."""
    from .audit import write_audit_event
    from .locks import release_locks_for_operation
    now = _now()
    ctx = dict(op.context or {})
    first = not ctx.get("deferred")
    n = int(ctx.get("deferrals") or 0)
    waiting_on = find_blocker(db, op) or ctx.get("waiting_on") or {"id": None, "operation_type_id": None, "label": "a free place on a host or storage (see the limits in Settings > Workers)", "running_minutes": None, "progress_pct": None}
    ctx.update({"deferred": True, "deferrals": n + 1, "deferred_since": ctx.get("deferred_since") or now.isoformat(),
                "retry_at": next_retry(n, now).isoformat(), "waiting_on": waiting_on})
    release_locks_for_operation(db, op.id)
    op.context = ctx
    op.status = "approved"
    op.blocking_safety_rules = None
    op.completed_at = None
    db.commit()
    db.refresh(op)
    if first:
        write_audit_event(db, event_category="operation", event_type=f"{op.operation_type_id}.waiting", actor="system", correlation_id=op.correlation_id, cluster_id=op.cluster_id, workload_id=op.workload_id,
                          metadata={"waiting_on": (waiting_on or {}).get("label")})
    return op


def deferred_due(db, now: datetime | None = None) -> list:
    """Operations waiting on a busy resource whose retry time has come."""
    from .models import Operation
    now = now or _now()
    out = []
    for op in db.query(Operation).filter(Operation.status == "approved").all():
        ctx = op.context or {}
        if ctx.get("deferred") and ctx.get("retry_at"):
            try:
                if datetime.fromisoformat(ctx["retry_at"]) <= now:
                    out.append(op)
            except ValueError:
                out.append(op)
    return out


def stale_claims(db, now: datetime | None = None) -> list:
    """Running operations whose worker stopped heartbeating."""
    from .models import Operation
    now = now or _now()
    return (db.query(Operation).filter(Operation.claimed_by.isnot(None), Operation.heartbeat_at < now - timedelta(seconds=STALE_SECONDS),
                                       Operation.status.notin_(("completed", "failed", "blocked", "cancelled", "approved", "awaiting_approval", "pending", "dry_run"))).all())
