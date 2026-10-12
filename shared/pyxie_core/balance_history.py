"""History of Balance Load checks (see models.BalanceCheck): what was looked at, what was decided and why.

record_check() never raises: history must not be able to break a balancing run or a preview.
"""

import logging
import re
from datetime import timedelta

from sqlalchemy.orm import Session

from .models import BalanceCheck, Node
from .node_sanity import node_problem
from .balance_config import utcnow

log = logging.getLogger(__name__)

KEEP_DAYS = 90


def reason_key(result: str, reason: str | None) -> str:
    """Same outcome for the same cause, whatever the numbers in the sentence say today."""
    return f"{result}:{re.sub(r'[0-9.]+', '#', (reason or '').lower())[:120]}"


def node_snapshot(db: Session, cluster_id) -> list[dict]:
    """Each node's live load at the time of the check, so a past decision can be read against what it saw."""
    out = []
    for n in db.query(Node).filter(Node.cluster_id == cluster_id, Node.is_missing.is_(False)).order_by(Node.name).all():
        out.append({
            "name": n.name, "status": n.status, "maintenance": bool(n.maintenance_mode),
            "mem_pct": None if n.mem_usage_pct is None else round(n.mem_usage_pct, 1), "cpu_pct": None if n.cpu_usage_pct is None else round(n.cpu_usage_pct, 1),
            "problem": node_problem(db, n),
        })
    return out


def record_check(
    db: Session, cluster_id, *, trigger: str, result: str, reason: str | None = None, actor: str | None = None, mode: str | None = None,
    level: str | None = None, balance_score: int | None = None, trigger_score: int | None = None, moves: int = 0, operation_id=None,
    details: dict | None = None, now=None,
) -> None:
    now = now or utcnow()
    try:
        key = reason_key(result, reason)
        merged = None
        if result not in ("plan_created", "plan_auto_approved", "stopped", "error") and operation_id is None:
            last = (
                db.query(BalanceCheck).filter(BalanceCheck.cluster_id == cluster_id, BalanceCheck.trigger == trigger)
                .order_by(BalanceCheck.last_checked_at.desc()).first()
            )
            if last is not None and last.reason_key == key and last.mode == mode and last.level == level:
                merged = last
        snapshot = {**(details or {})}
        if "rules" not in snapshot:
            from .rule_review import review_rules  # every check records what the affinity rules and pins looked like, whatever it decided
            snapshot["rules"] = review_rules(db, cluster_id).summary()
        if "nodes" not in snapshot:
            snapshot["nodes"] = node_snapshot(db, cluster_id)
        if merged is not None:
            merged.last_checked_at = now
            merged.repeat_count = (merged.repeat_count or 1) + 1
            merged.reason = reason
            merged.balance_score = balance_score
            merged.trigger_score = trigger_score
            merged.details = snapshot
        else:
            db.add(BalanceCheck(
                cluster_id=cluster_id, first_checked_at=now, last_checked_at=now, trigger=trigger, actor=actor, mode=mode, level=level,
                result=result, reason=reason, reason_key=key, balance_score=balance_score, trigger_score=trigger_score, moves=moves,
                operation_id=operation_id, details=snapshot,
            ))
            db.query(BalanceCheck).filter(BalanceCheck.last_checked_at < now - timedelta(days=KEEP_DAYS)).delete(synchronize_session=False)
        db.commit()
    except Exception:  # noqa: BLE001
        log.exception("could not record the balance check")
        db.rollback()
