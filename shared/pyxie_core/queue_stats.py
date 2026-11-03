"""How long approved operations wait for a worker, per lane (the Health page tile and the long-wait finding). Read only."""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .models import Operation
from .op_lanes import lane_for

RUNNING_STATUSES = ("revalidating", "executing", "monitoring", "verifying")
LOOKBACK_DAYS = 7


def lane_stats(db: Session, now: datetime | None = None) -> dict:
    """{"quick"|"bulk": {waiting, running, oldest_waiting_seconds, longest_wait_seconds (last 7 days)}}. Only top-level operations count:
    the guest moves inside an evacuation run inside its job and never wait for a worker of their own."""
    now = now or datetime.now(timezone.utc)
    out = {lane: {"waiting": 0, "running": 0, "oldest_waiting_seconds": None, "longest_wait_seconds": None} for lane in ("quick", "bulk")}
    cutoff = now - timedelta(days=LOOKBACK_DAYS)
    rows = (db.query(Operation.operation_type_id, Operation.status, Operation.approved_at, Operation.started_at)
            .filter(Operation.parent_operation_id.is_(None), Operation.approved_at.isnot(None), Operation.approved_at >= cutoff).all())
    for type_id, status, approved_at, started_at in rows:
        s = out[lane_for(type_id)]
        if status == "approved":
            s["waiting"] += 1
            age = (now - approved_at).total_seconds()
            s["oldest_waiting_seconds"] = max(age, s["oldest_waiting_seconds"] or 0)
            s["longest_wait_seconds"] = max(age, s["longest_wait_seconds"] or 0)
        else:
            if status in RUNNING_STATUSES:
                s["running"] += 1
            if started_at is not None:
                s["longest_wait_seconds"] = max((started_at - approved_at).total_seconds(), s["longest_wait_seconds"] or 0)
    return out


def long_waits(db: Session, minutes: int, now: datetime | None = None) -> list[Operation]:
    """Approved top-level operations still not started after `minutes`."""
    now = now or datetime.now(timezone.utc)
    return (db.query(Operation).filter(Operation.status == "approved", Operation.parent_operation_id.is_(None),
                                       Operation.approved_at.isnot(None), Operation.approved_at <= now - timedelta(minutes=minutes))
            .order_by(Operation.approved_at).all())
