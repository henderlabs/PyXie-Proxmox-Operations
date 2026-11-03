"""Operation workers: the profile (single worker, or quick + bulk lanes), live lane statistics, and which workers are alive."""
import os
from datetime import datetime, timezone

import redis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pyxie_core.audit import write_audit_event
from pyxie_core.models import Operation, Policy
from pyxie_core.op_lanes import BULK_QUEUE, QUICK_QUEUE, quick_lane_active
from pyxie_core.slots import MAX_LIMIT, slots_in_use
from pyxie_core.queue_stats import RUNNING_STATUSES, lane_stats

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db

router = APIRouter(prefix="/api/workers", tags=["workers"], dependencies=[Depends(get_current_user)])

PROFILES = ("single", "standard", "large")
LIMIT_KEYS = {"evac_parallel": "limits.evac_parallel", "node_out": "limits.node_out", "node_in": "limits.node_in", "storage_moves": "limits.storage_moves", "bulk_workers": "limits.bulk_workers"}


def _conn():
    return redis.from_url(os.environ["REDIS_URL"])


def profile_of(db: Session) -> str:
    p = db.query(Policy).filter(Policy.key == "workers.profile", Policy.scope_type == "organization").one_or_none()
    value = p.value.get("profile") if p and isinstance(p.value, dict) else None
    return value if value in PROFILES else "single"


def _count(db: Session, key: str, default: int = 1) -> int:
    p = db.query(Policy).filter(Policy.key == key, Policy.scope_type == "organization").one_or_none()
    try:
        return int(p.value.get("count")) if p and isinstance(p.value, dict) else default
    except (TypeError, ValueError):
        return default


def _alive(conn) -> list[dict]:
    from rq import Worker
    out = []
    now = datetime.now(timezone.utc)
    try:
        for w in Worker.all(connection=conn):
            queues = w.queue_names()
            if BULK_QUEUE not in queues and QUICK_QUEUE not in queues:
                continue
            hb = w.last_heartbeat
            if hb is not None and hb.tzinfo is None:
                hb = hb.replace(tzinfo=timezone.utc)
            out.append({"queues": queues, "state": w.get_state(), "heartbeat_seconds": int((now - hb).total_seconds()) if hb else None})
    except Exception:  # noqa: BLE001
        return []
    return out


@router.get("")
def status(db: Session = Depends(get_db)):
    conn = _conn()
    running = db.query(Operation).filter(Operation.status.in_(RUNNING_STATUSES), Operation.parent_operation_id.is_(None)).all()
    return {
        "profile": profile_of(db),
        "limits": {name: _count(db, key) for name, key in LIMIT_KEYS.items()},
        "slots_in_use": slots_in_use(db),
        "quick_lane_running": quick_lane_active(conn),
        "workers": _alive(conn),
        "running_now": [{"id": str(o.id), "operation_type_id": o.operation_type_id, "claimed_by": o.claimed_by} for o in running],
        "lanes": lane_stats(db),
    }


class WorkersUpdate(BaseModel):
    profile: str | None = None
    limits: dict[str, int] | None = None


def _set_count(db: Session, key: str, value: int) -> None:
    p = db.query(Policy).filter(Policy.key == key, Policy.scope_type == "organization").one_or_none()
    if p is None:
        db.add(Policy(scope_type="organization", scope_id=None, key=key, value={"count": value}))
    else:
        p.value = {"count": value}


@router.put("", dependencies=[Depends(require_admin)])
def update(payload: WorkersUpdate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    changes: dict = {}
    if payload.profile is not None:
        if payload.profile not in PROFILES:
            raise HTTPException(422, "profile must be 'single', 'standard' or 'large'")
        before = profile_of(db)
        p = db.query(Policy).filter(Policy.key == "workers.profile", Policy.scope_type == "organization").one_or_none()
        if p is None:
            db.add(Policy(scope_type="organization", scope_id=None, key="workers.profile", value={"profile": payload.profile}))
        else:
            p.value = {"profile": payload.profile}
        changes["profile"] = {"from": before, "to": payload.profile}
    for name, value in (payload.limits or {}).items():
        if name not in LIMIT_KEYS:
            raise HTTPException(422, f"unknown limit '{name}'")
        if not 1 <= value <= MAX_LIMIT:
            raise HTTPException(422, f"'{name}' must be between 1 and {MAX_LIMIT}")
        changes.setdefault("limits", {})[name] = {"from": _count(db, LIMIT_KEYS[name]), "to": value}
        _set_count(db, LIMIT_KEYS[name], value)
    if changes.get("profile", {}).get("to") == "large" and _count(db, "limits.bulk_workers") < 2 and "bulk_workers" not in (payload.limits or {}):
        raise HTTPException(422, "choose how many long-job workers (2 to 4) when selecting Large")
    if not changes:
        return {"unchanged": True}
    write_audit_event(db, event_category="settings", event_type="workers.settings.changed", actor=user.email, actor_type="user", metadata=changes)
    db.commit()
    return {"saved": True, "profile_applies": "at the next worker restart (restart the worker container, or update PyXie)" if "profile" in changes or "bulk_workers" in (payload.limits or {}) else "immediately for new tasks"}
