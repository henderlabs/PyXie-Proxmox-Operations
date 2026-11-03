"""Clear stale VM locks on one node (host.clear_locks), through the host-maintenance wrapper (1.2.0+).

Proxmox's API cannot remove a guest lock for a non-root account, so this goes through the restricted wrapper's `locks`
(read) and `unlock-stale` (clear) subcommands. Flow: dry run reads `locks` from the node and lists exactly which guests
would be cleared (and which are left alone, with why) -> approval -> revalidate -> `unlock-stale` -> read `locks` again to
verify -> refresh inventory. A lock whose task is running is never cleared: the wrapper checks, and PyXie checks too.
"""

from sqlalchemy.orm import Session

from .credentials import load_host_maintenance_credentials
from .host_maintenance_client import (
    HostMaintenanceClient, HostMaintenanceConnectionError, HostMaintenanceProtocolError, MutationsDisabledError,
)
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, HostMaintenanceCredential, Node, Operation, PveTarget, Workload
from .operations_engine import approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .vm_locks import age_text, lock_state_for

NEEDED_CAPABILITIES = ("locks", "unlock-stale")
STUCK_BACKUP_SECONDS = 6 * 3600  # a backup running longer than this on a guest that is locked for backup is treated as hung


def dur(seconds) -> str:
    s = int(seconds or 0)
    if s < 120:
        return f"{s} s"
    if s < 7200:
        return f"{s // 60} min"
    if s < 172800:
        return f"{s // 3600} h {(s % 3600) // 60} min"
    return f"{s // 86400} d {(s % 86400) // 3600} h"


def clean_status(raw) -> str:
    """Pure. The status text of a finished task as the host lists it ("<end time hex> OK" or "<end time hex> job errors"): without the time."""
    import re
    return re.sub(r"^(?:[0-9A-Fa-f]{8}\s+)+", "", str(raw or "")).strip() or "unknown"


def stuck_backup(row: dict) -> bool:
    """Pure. A backup task that really is running (its process exists) but has run far longer than a backup should."""
    t = row.get("task") or {}
    return bool(row.get("task_running")) and row.get("lock") == "backup" and t.get("type") == "vzdump" and int(t.get("age_seconds") or 0) >= STUCK_BACKUP_SECONDS


def explain_row(row: dict, wrapper_version: str | None) -> str:
    """Pure. One plain sentence on what the host reported for a locked guest and what will happen, so nobody has to look on the host."""
    t = row.get("task") or {}
    state = row.get("task_state")
    kind = {"vzdump": "backup", "qmsnapshot": "snapshot", "qmigrate": "migration"}.get(t.get("type"), t.get("type") or "task")
    if row.get("task_running") and state is None:  # a 1.2.0 wrapper: it counts a finished task that is still listed as running
        return ("The host reports a task for it, but its wrapper (" + str(wrapper_version or "1.2.0") + ") cannot tell a finished task from a running one. "
                "Update the maintenance wrapper on this host (Update the wrapper in Health) and preview again.")
    if stuck_backup(row):
        return (f"A backup has been running for {dur(t.get('age_seconds'))}, far longer than a backup should take, so it is hung. "
                "Approving stops that backup through Proxmox, then clears the lock.")
    if row.get("task_running"):
        return f"A {kind} really is running for it (started {dur(t.get('age_seconds'))} ago). Left alone."
    if state == "finished":
        return (f"The {kind} the host still lists for this guest (started {dur(t.get('age_seconds'))} ago) finished with status '{clean_status(t.get('status'))}', "
                "and nothing is running now, so the lock is left over.")
    if state == "dead":
        return f"The {kind} for this guest stopped without finishing (its process is gone) and nothing is running now, so the lock is left over."
    if row.get("stale"):
        return "No task is running or recorded for this guest, so the lock is left over."
    return "The lock is recent, or of a type that is not cleared automatically. Left alone."


def _load_context(db: Session, node: Node):
    cluster = db.query(Cluster).filter(Cluster.id == node.cluster_id).one()
    return cluster, db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()


def stop_candidates(rows: list[dict]) -> list[dict]:
    """Pure. Hung backups the plan may stop first."""
    return [r for r in rows if stuck_backup(r)]


def split_candidates(rows: list[dict], pyxie_state: dict[int, str | None]) -> tuple[list[dict], list[dict]]:
    """Pure. (will_clear, leave_alone). A guest is cleared only when the wrapper says stale AND PyXie does not see a task
    running for it. `pyxie_state` maps vmid -> "active"/"recent"/"stale"/None from PyXie's own records."""
    clear, leave = [], []
    for r in rows:
        mine = pyxie_state.get(r["vmid"])
        if r.get("task_running") or mine == "active":
            leave.append({**r, "why": "a task is running for it"})
        elif not r.get("stale"):
            leave.append({**r, "why": "not stale yet (the wrapper waits until the config has been untouched for 30 minutes) or not a lock type it clears"})
        else:
            clear.append(r)
    return clear, leave


def _names(db: Session, cluster_id, vmids) -> dict[int, str]:
    return {w.vmid: (w.name or str(w.vmid)) for w in db.query(Workload).filter(Workload.cluster_id == cluster_id, Workload.vmid.in_(list(vmids) or [0])).all()}


def _read_locks(db: Session, node: Node, target: PveTarget):
    hm = load_host_maintenance_credentials(db, target, node)
    with HostMaintenanceClient(hm) as client:
        info = client.version()
        caps = info.get("capabilities") or []
        if any(c not in caps for c in NEEDED_CAPABILITIES):
            return info, None
        return info, client.locks().get("locks", [])


def dry_run_clear_locks(db: Session, node: Node, *, actor: str, correlation_id=None) -> Operation:
    cluster, target = _load_context(db, node)
    op = create_operation(db, "host.clear_locks", cluster_id=cluster.id, node_id=node.id, correlation_id=correlation_id,
                          context={"node": node.name}, created_by=actor)
    reasons: list[str] = []
    rules: list[str] = []

    def blocked(extra=None):
        res = {"node": node.name, "eligible": False, "reasons": reasons, "blocking_safety_rules": rules, **(extra or {})}
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=res, blocking_safety_rules=rules, actor=actor)

    if db.query(HostMaintenanceCredential).filter(HostMaintenanceCredential.pve_target_id == target.id).one_or_none() is None:
        reasons.append("no host-maintenance SSH credential for this cluster -- see Platform > Credentials")
        rules.append("SAFE-HOSTCRED-001")
        return blocked()
    try:
        info, rows = _read_locks(db, node, target)
    except (HostMaintenanceConnectionError, HostMaintenanceProtocolError, Exception) as exc:  # noqa: BLE001
        from .wrapper_state import missing_wrapper_hint
        reasons.append(f"could not read the locks on {node.name} through the host wrapper: {str(exc)[:200]}. {missing_wrapper_hint(node.name)}")
        rules.append("SAFE-HOSTCONN-001")
        return blocked()
    if rows is None:
        reasons.append(f"the host wrapper on {node.name} is {info.get('wrapper_version')}: clearing locks needs wrapper 1.2.0 or newer. "
                       "Update the maintenance wrapper on that host (Update the wrapper, on the host's page or in Health), or run `qm unlock <vmid>` on the node yourself.")
        rules.append("SAFE-HOSTCONN-001")
        return blocked({"wrapper_version": info.get("wrapper_version")})

    mine = {w.vmid: lock_state_for(db, w) for w in db.query(Workload).filter(Workload.cluster_id == cluster.id, Workload.is_missing.is_(False)).all()}
    clear, leave = split_candidates(rows, mine)
    stops = stop_candidates(leave)
    leave = [r for r in leave if r not in stops]
    names = _names(db, cluster.id, [r["vmid"] for r in rows])
    wv = info.get("wrapper_version")
    view = lambda r: {"vmid": r["vmid"], "name": names.get(r["vmid"]), "lock": r["lock"], "age": age_text_seconds(r.get("config_age_seconds")), "why": r.get("why"),
                      "explain": explain_row(r, wv), "task": r.get("task")}
    result = {"node": node.name, "eligible": bool(clear or stops), "wrapper_version": wv,
              "will_clear": [view(r) for r in clear], "left_alone": [view(r) for r in leave],
              "stop_tasks": [{**view(r), "upid": (r.get("task") or {}).get("upid")} for r in stops],
              "reasons": reasons, "blocking_safety_rules": []}
    if stops:
        reasons.append("; ".join(f"{names.get(r['vmid'], r['vmid'])}: {explain_row(r, wv)}" for r in stops))
    if not clear and not stops:
        reasons.append(f"no stale locks to clear on {node.name} right now" + ((": " + " ".join(f"{names.get(r['vmid'], r['vmid'])} ({r['lock']}): {explain_row(r, wv)}" for r in leave)) if leave else ""))
        return enter_stage(db, op, status="completed", stage="audit", dry_run_result=result, actor=actor)
    if clear:
        reasons.append(f"{len(clear)} guest(s) will be unlocked with `qm unlock`: "
                       + "; ".join(f"{names.get(r['vmid'], r['vmid'])} ({r['lock']}): {explain_row(r, wv)}" for r in clear)
                       + " A lock with a running task is never touched; each is re-checked right before it is cleared.")
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result,
                     context={"node": node.name, "planned_vmids": [r["vmid"] for r in clear] + [r["vmid"] for r in stops],
                              "planned_stops": [{"vmid": r["vmid"], "upid": (r.get("task") or {}).get("upid")} for r in stops]}, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def age_text_seconds(seconds) -> str:
    from datetime import datetime, timedelta, timezone
    if seconds is None:
        return "unknown"
    now = datetime.now(timezone.utc)
    return age_text(now - timedelta(seconds=int(seconds)), now)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def _stop_hung_backups(db, op, node, target, rows, planned_stops):
    """Stop the hung backup tasks the operator approved (through the Proxmox API, the same Stop button Proxmox itself has), but only the ones that are
    STILL running, with the same task id, and still hung. Then read the locks again from the host."""
    import time
    from .credentials import load_pve_credentials
    from .operation_log import log_stage
    from .pve_write_client import PveMaintenanceClient
    todo = []
    for p in planned_stops:
        now = next((r for r in rows if r["vmid"] == p["vmid"]), None)
        if now is not None and stuck_backup(now) and (now.get("task") or {}).get("upid") == p.get("upid"):
            todo.append(p)
    if not todo:
        return rows
    with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as client:
        for p in todo:
            log_stage(op.id, f"Stopping the hung backup task for VM {p['vmid']} on {node.name}.")
            client.stop_task(node.name, p["upid"])
        for _ in range(30):
            if not any(client.task_status(node.name, p["upid"]).status == "running" for p in todo):
                break
            time.sleep(2)
    _i, again = _read_locks(db, node, target)
    return again if again is not None else rows


def execute_clear_locks(db: Session, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one()
    node = db.query(Node).filter(Node.id == op.node_id).one()
    cluster, target = _load_context(db, node)
    if op.status == "approved":
        op = enter_stage(db, op, status="revalidating", stage="revalidating")
    try:
        _info, rows = _read_locks(db, node, target)
        if rows is None:
            return block_operation(db, op, blocking_safety_rules=["SAFE-HOSTCONN-001"], actor="system")
        planned_stops = (op.context or {}).get("planned_stops") or []
        if planned_stops:
            rows = _stop_hung_backups(db, op, node, target, rows, planned_stops)
        mine = {w.vmid: lock_state_for(db, w) for w in db.query(Workload).filter(Workload.cluster_id == cluster.id, Workload.is_missing.is_(False)).all()}
        planned = set((op.context or {}).get("planned_vmids") or [])
        clear, _leave = split_candidates(rows, mine)
        if not [r for r in clear if r["vmid"] in planned]:
            return enter_stage(db, op, status="completed", stage="audit", verification_result={"cleared": [], "note": "nothing left to clear: the locks went away or a task started"}, actor="system")
        try:
            acquire_lock(db, resource_type="node", resource_id=node.id, operation_id=op.id, reason="host.clear_locks", ttl_seconds=600)
        except LockContention:
            return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
        op = enter_stage(db, op, status="executing", stage="executing")
        hm = load_host_maintenance_credentials(db, target, node)
        with HostMaintenanceClient(hm) as client:
            res = client.unlock_stale()
        cleared = [r["vmid"] for r in res.get("cleared", [])]
        op = enter_stage(db, op, status="verifying", stage="verifying")
        _i, after = _read_locks(db, node, target)
        still = [r["vmid"] for r in (after or []) if r["vmid"] in cleared]
        release_locks_for_operation(db, op.id)
        try:
            from .discovery import run_discovery
            run_discovery(db, target, actor="operation")
        except Exception:  # noqa: BLE001
            pass
        if still:
            return fail_operation(db, op, error=f"unlock ran but these guests are still locked: {still}")
        return enter_stage(db, op, status="completed", stage="audit", verification_result={"cleared": res.get("cleared", []), "skipped": res.get("skipped", [])}, actor="system")
    except MutationsDisabledError as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error: {exc}")
