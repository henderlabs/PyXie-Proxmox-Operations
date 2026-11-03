"""Stale VM locks: a Proxmox `lock:` (backup, migrate, snapshot, ...) with no task behind it.

Proxmox sets the lock when a task starts and removes it when the task ends. If the task dies or cannot write the
guest's config at that moment (the 02:00 backup when the cluster filesystem is briefly cut off), the lock stays and every
later backup, migration or snapshot of that guest is refused. Clearing it needs `qm unlock` on the node (shell only for a
non-root account), see clear_lock_workflow.py.

States: "active" a task for this guest is running right now (never clear); "recent" locked for less than the stale
threshold with no task (give it time); "stale" locked at least that long with no task running anywhere.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .models import Cluster, Node, PveTask, Workload

DEFAULT_STALE_AFTER_MINUTES = 30
MIN_STALE_AFTER_MINUTES = 10


def classify_lock(since: datetime | None, now: datetime, task_running: bool, stale_after_minutes: int = DEFAULT_STALE_AFTER_MINUTES) -> str:
    """Pure. `since` None (never seen with a lock) is treated as just seen."""
    if task_running:
        return "active"
    limit = timedelta(minutes=max(MIN_STALE_AFTER_MINUTES, stale_after_minutes))
    if since is not None and now - since >= limit:
        return "stale"
    return "recent"


def age_text(since: datetime | None, now: datetime) -> str:
    if since is None:
        return "just now"
    mins = max(0, int((now - since).total_seconds() // 60))
    if mins < 60:
        return f"{mins} min"
    if mins < 2880:
        return f"{mins // 60} h {mins % 60} min"
    return f"{mins // 1440} d {(mins % 1440) // 60} h"


def running_task_vmids(db: Session, cluster_id) -> set[int]:
    """VMIDs with a Proxmox task in flight right now (task id in the UPID is the guest's VMID). Conservative on purpose: ANY
    running task for the guest, whatever its type, counts as holding the lock."""
    from .findings import vmid_from_upid
    out: set[int] = set()
    # last_seen: every poll refreshes a task that Proxmox still lists, so a row nobody has refreshed lately is not really running
    fresh = datetime.now(timezone.utc) - timedelta(minutes=15)
    rows = db.query(PveTask.upid).filter(
        PveTask.cluster_id == cluster_id, PveTask.ended_at.is_(None), PveTask.exit_status.is_(None), PveTask.last_seen >= fresh,
    ).all()
    for (upid,) in rows:
        v = vmid_from_upid(upid)
        if v is not None:
            out.add(v)
    return out


def locked_guests(db: Session, now: datetime | None = None, stale_after_minutes: int = DEFAULT_STALE_AFTER_MINUTES) -> list[dict]:
    """Every present guest that has a lock, classified. Read only."""
    now = now or datetime.now(timezone.utc)
    out: list[dict] = []
    running_by_cluster: dict = {}
    nodes = {n.id: n for n in db.query(Node).all()}
    for wl in db.query(Workload).filter(Workload.pve_lock.isnot(None), Workload.is_missing.is_(False)).all():
        if wl.cluster_id not in running_by_cluster:
            running_by_cluster[wl.cluster_id] = running_task_vmids(db, wl.cluster_id)
        state = classify_lock(wl.pve_lock_since, now, wl.vmid in running_by_cluster[wl.cluster_id], stale_after_minutes)
        node = nodes.get(wl.node_id)
        out.append({
            "workload": wl, "node": node.name if node else None, "lock": wl.pve_lock, "since": wl.pve_lock_since,
            "age": age_text(wl.pve_lock_since, now), "state": state,
        })
    return out


def lock_state_for(db: Session, wl: Workload, now: datetime | None = None, stale_after_minutes: int = DEFAULT_STALE_AFTER_MINUTES) -> str | None:
    """"active" / "recent" / "stale" for one guest, or None if PyXie has not recorded a lock for it."""
    if not wl.pve_lock:
        return None
    now = now or datetime.now(timezone.utc)
    return classify_lock(wl.pve_lock_since, now, wl.vmid in running_task_vmids(db, wl.cluster_id), stale_after_minutes)


def lock_context(db: Session, wl: Workload) -> dict:
    """Keyword arguments for maintenance.vm_lock_reason: {"state", "age"} for a guest PyXie has recorded a lock for, else {}."""
    state = lock_state_for(db, wl)
    if state is None:
        return {}
    return {"state": state, "age": age_text(wl.pve_lock_since, datetime.now(timezone.utc))}
