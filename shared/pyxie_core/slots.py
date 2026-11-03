"""Capacity slots: how many guest moves or disk copies may touch one host or one storage at the same time (multi-worker phase 3).

A move out of a host takes one `node_out` slot on it, a move into a host one `node_in` slot, a disk copy one `storage` slot per storage it reads
or writes. Each kind has a limit (Platform > Settings > Workers > Limits, every one 1 by default = one at a time). A slot is held like a lock: short
lease renewed by the operation's heartbeat, released with the operation's locks. Slots and the exclusive host lock (host update, reboot,
maintenance mode) exclude each other: a host with moves in flight cannot be updated, and a host being updated cannot start moves, unless the
holder is a parent of the one asking (an evacuation's own moves).
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from .locks import LockContention, _is_ancestor_operation, lease_seconds
from .models import CapacitySlot, Policy, ResourceLock

KINDS = ("node_out", "node_in", "storage")
DEFAULT_LIMIT = 1
MAX_LIMIT = 4
POLICY_KEYS = {"node_out": "limits.node_out", "node_in": "limits.node_in", "storage": "limits.storage_moves"}


def limit_for(db: Session, kind: str) -> int:
    p = db.query(Policy).filter(Policy.key == POLICY_KEYS[kind], Policy.scope_type == "organization").one_or_none()
    try:
        v = int(p.value.get("count")) if p and isinstance(p.value, dict) else DEFAULT_LIMIT
    except (TypeError, ValueError):
        v = DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, v))


def node_gate_key(node_id) -> str:
    return f"nodecap:{node_id}"


def advisory(db: Session, key: str) -> None:
    """Serialise the check-then-insert of everything touching one host or storage (released at the next commit)."""
    db.execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": key})


def _active(db: Session, kind: str, key: str):
    now = datetime.now(timezone.utc)
    return db.query(CapacitySlot).filter(CapacitySlot.kind == kind, CapacitySlot.resource_key == key, CapacitySlot.released_at.is_(None), CapacitySlot.expires_at > now).all()


def active_slots_on_node(db: Session, node_id) -> list:
    now = datetime.now(timezone.utc)
    return db.query(CapacitySlot).filter(CapacitySlot.kind.in_(("node_out", "node_in")), CapacitySlot.resource_key == str(node_id),
                                         CapacitySlot.released_at.is_(None), CapacitySlot.expires_at > now).all()


def acquire_slot(db: Session, *, kind: str, resource_key: str, operation_id, ttl_seconds: int = 16200, node: bool = False) -> CapacitySlot:
    """Take one slot, or raise LockContention (so every workflow's existing handling, and waiting for a person's task, applies)."""
    advisory(db, node_gate_key(resource_key) if node else f"slot:{kind}:{resource_key}")
    mine = [s for s in _active(db, kind, resource_key) if s.operation_id == operation_id]
    if mine:
        db.commit()
        return mine[0]
    if node:  # an exclusive host lock held by someone who is not my parent stops new moves on that host
        now = datetime.now(timezone.utc)
        lock = db.query(ResourceLock).filter(ResourceLock.resource_type == "node", ResourceLock.resource_id == uuid.UUID(str(resource_key)),
                                              ResourceLock.released_at.is_(None), ResourceLock.expires_at > now).first()
        if lock is not None and lock.operation_id != operation_id and not _is_ancestor_operation(db, lock.operation_id, operation_id):
            db.rollback()
            raise LockContention("node", uuid.UUID(str(resource_key)), lock.operation_id)
    held = [s for s in _active(db, kind, resource_key) if not _is_ancestor_operation(db, s.operation_id, operation_id)]
    if len(held) >= limit_for(db, kind):
        db.rollback()
        raise LockContention(kind, uuid.UUID(int=0) if not _is_uuid(resource_key) else uuid.UUID(str(resource_key)), held[0].operation_id)
    now = datetime.now(timezone.utc)
    slot = CapacitySlot(kind=kind, resource_key=resource_key, operation_id=operation_id, acquired_at=now, expires_at=now + timedelta(seconds=lease_seconds(ttl_seconds)))
    db.add(slot)
    db.commit()
    db.refresh(slot)
    return slot


def _is_uuid(v) -> bool:
    try:
        uuid.UUID(str(v))
        return True
    except ValueError:
        return False


def free_for(db: Session, kind: str, resource_key: str, extra_in_use: int = 0) -> bool:
    """Would a new slot be granted right now (plus `extra_in_use` that the caller is about to start)?"""
    return len(_active(db, kind, resource_key)) + extra_in_use < limit_for(db, kind)


def slots_in_use(db: Session) -> list[dict]:
    now = datetime.now(timezone.utc)
    return [{"kind": s.kind, "resource_key": s.resource_key, "operation_id": str(s.operation_id)}
            for s in db.query(CapacitySlot).filter(CapacitySlot.released_at.is_(None), CapacitySlot.expires_at > now).all()]


def release_slots_for_operation(db: Session, operation_id) -> int:
    now = datetime.now(timezone.utc)
    rows = db.query(CapacitySlot).filter(CapacitySlot.operation_id == operation_id, CapacitySlot.released_at.is_(None)).all()
    for s in rows:
        s.released_at = now
    return len(rows)
