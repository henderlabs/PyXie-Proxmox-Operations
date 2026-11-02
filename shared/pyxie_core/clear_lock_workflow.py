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


def _load_context(db: Session, node: Node):
    cluster = db.query(Cluster).filter(Cluster.id == node.cluster_id).one()
    return cluster, db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()


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
        reasons.append(f"could not read the locks on {node.name} through the host wrapper: {str(exc)[:200]}")
        rules.append("SAFE-HOSTCONN-001")
        return blocked()
    if rows is None:
        reasons.append(f"the host wrapper on {node.name} is {info.get('wrapper_version')}: clearing locks needs wrapper 1.2.0 or newer. "
                       "Update the host kit (Integrations > Host wrapper), or run `qm unlock <vmid>` on the node yourself.")
        rules.append("SAFE-HOSTCONN-001")
        return blocked({"wrapper_version": info.get("wrapper_version")})

    mine = {w.vmid: lock_state_for(db, w) for w in db.query(Workload).filter(Workload.cluster_id == cluster.id, Workload.is_missing.is_(False)).all()}
    clear, leave = split_candidates(rows, mine)
    names = _names(db, cluster.id, [r["vmid"] for r in rows])
    view = lambda r: {"vmid": r["vmid"], "name": names.get(r["vmid"]), "lock": r["lock"], "age": age_text_seconds(r.get("config_age_seconds")), "why": r.get("why")}
    result = {"node": node.name, "eligible": bool(clear), "wrapper_version": info.get("wrapper_version"),
              "will_clear": [view(r) for r in clear], "left_alone": [view(r) for r in leave],
              "reasons": reasons, "blocking_safety_rules": []}
    if not clear:
        reasons.append(f"no stale locks to clear on {node.name} right now" + (f" ({len(leave)} locked guest(s) left alone, see below)" if leave else ""))
        return enter_stage(db, op, status="completed", stage="audit", dry_run_result=result, actor=actor)
    reasons.append(f"{len(clear)} guest(s) will be unlocked with `qm unlock`: "
                   + ", ".join(f"{names.get(r['vmid'], r['vmid'])} ({r['lock']})" for r in clear)
                   + ". Locks with a running task are never touched; each is re-checked right before it is cleared.")
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result,
                     context={"node": node.name, "planned_vmids": [r["vmid"] for r in clear]}, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def age_text_seconds(seconds) -> str:
    from datetime import datetime, timedelta, timezone
    if seconds is None:
        return "unknown"
    now = datetime.now(timezone.utc)
    return age_text(now - timedelta(seconds=int(seconds)), now)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


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
