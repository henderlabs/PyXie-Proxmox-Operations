"""Proxmox resource pools: create (pool.create), delete an empty one (pool.delete) and change which guests belong to one (pool.members).

Same Safety Contract as every other operation. A pool only groups guests for permissions and defaults; creating, emptying and deleting one never touches a guest's disks or power state.
A guest can be in one pool only, so adding a guest that is already in another pool is refused (remove it there first, in the same preview).
"""

import re
import uuid

from .credentials import load_pve_credentials
from .discovery import run_discovery
from .iso_workflow import _lock_id
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Operation, PveTarget, Workload
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_permissions import friendly_error, missing_privileges, permission_reason
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient
from . import rollback

OP_CREATE, OP_DELETE, OP_MEMBERS = "pool.create", "pool.delete", "pool.members"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{1,63}$")


class PoolError(Exception):
    pass


def validate_pool_id(pool_id: str, existing: set[str]) -> list[str]:
    """Blocking errors for a new pool name. Pure."""
    if not ID_RE.match(pool_id or ""):
        return ["the pool name uses letters, digits and - _ . (2 to 64 characters, starting with a letter or digit)"]
    if pool_id in existing:
        return [f"a pool called '{pool_id}' already exists"]
    return []


def plan_members(current: dict[str, set[int]], pool_id: str, add: list[int], remove: list[int]) -> tuple[list[int], list[int], list[str]]:
    """(vmids to add, vmids to remove, blocking errors). `current` maps every pool to its member vmids. Pure."""
    errors: list[str] = []
    here = current.get(pool_id, set())
    gone = [v for v in dict.fromkeys(remove) if v in here]
    for v in dict.fromkeys(remove):
        if v not in here:
            errors.append(f"guest {v} is not in '{pool_id}'")
    new = []
    for v in dict.fromkeys(add):
        if v in here:
            continue
        other = next((p for p, m in current.items() if p != pool_id and v in m), None)
        if other:
            errors.append(f"guest {v} is already in pool '{other}': a guest can be in one pool only, remove it there first")
        else:
            new.append(v)
    if not new and not gone and not errors:
        errors.append("nothing to change")
    return new, gone, errors


def _target(db, cluster: Cluster) -> PveTarget:
    return db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()


def read_pools(wc) -> dict[str, dict]:
    """Every pool with its comment and member vmids, read live from Proxmox. Pure read."""
    out = {}
    for p in wc._get("/pools") or []:
        pid = p["poolid"]
        detail = wc._get(f"/pools/{pid}") or {}
        members = [m for m in detail.get("members", []) if m.get("type") in ("qemu", "lxc")]
        out[pid] = {"comment": p.get("comment") or detail.get("comment") or "", "members": {int(m["vmid"]): m for m in members},
                    "other_members": [m for m in detail.get("members", []) if m.get("type") not in ("qemu", "lxc")]}
    return out


def _finish(db, op, actor, ctx, result, blocking):
    op.context = ctx
    db.commit()
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def _result(cluster, pool, reasons, blocking):
    return {"cluster": cluster.name, "pool": pool, "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": sorted(set(blocking)), "workload_name": None}


def dry_run_pool_create(db, cluster: Cluster, *, pool: str, comment: str, actor: str, correlation_id=None) -> Operation:
    pool, comment = (pool or "").strip(), (comment or "").strip()[:200]
    target = _target(db, cluster)
    ctx = {"cluster": cluster.name, "pool": pool, "comment": comment}
    op = create_operation(db, OP_CREATE, cluster_id=cluster.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    try:
        with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as wc:
            existing = {p["poolid"] for p in wc._get("/pools") or []}
        errs = validate_pool_id(pool, existing)
        reasons += errs
        if errs:
            blocking.append("SAFE-POOL-001")
        lacking = missing_privileges(db, target, 0, ["Pool.Allocate"], path="/pool")
        if lacking:
            reasons.append(permission_reason(lacking, "create pools"))
            blocking.append("SAFE-PERMISSION-001")
        if not blocking:
            reasons.append(f"creates the empty pool '{pool}'. No guest is touched.")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"could not check the cluster: {str(exc)[:160]}")
        blocking.append("SAFE-POOL-002")
    return _finish(db, op, actor, ctx, _result(cluster, pool, reasons, blocking), blocking)


def dry_run_pool_delete(db, cluster: Cluster, *, pool: str, confirm: str, actor: str, correlation_id=None) -> Operation:
    target = _target(db, cluster)
    ctx = {"cluster": cluster.name, "pool": pool}
    op = create_operation(db, OP_DELETE, cluster_id=cluster.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    if (confirm or "").strip() != pool:
        reasons.append(f"type the pool's exact name ({pool}) to confirm")
        blocking.append("SAFE-POOL-001")
    try:
        with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as wc:
            pools = read_pools(wc)
        if pool not in pools:
            reasons.append(f"there is no pool '{pool}'")
            blocking.append("SAFE-POOL-001")
        else:
            m = pools[pool]
            held = [f"{'CT' if v['type'] == 'lxc' else 'VM'} {k}" for k, v in sorted(m["members"].items())] + [x.get("id", "?") for x in m["other_members"]]
            if held:
                reasons.append(f"still holds {', '.join(held)}: remove them from the pool first")
                blocking.append("SAFE-POOL-001")
        lacking = missing_privileges(db, target, 0, ["Pool.Allocate"], path=f"/pool/{pool}")
        if lacking:
            reasons.append(permission_reason(lacking, "delete pools"))
            blocking.append("SAFE-PERMISSION-001")
        if not blocking:
            reasons.insert(0, f"deletes the empty pool '{pool}'. Permissions or defaults that point at it stop applying.")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"could not check the cluster: {str(exc)[:160]}")
        blocking.append("SAFE-POOL-002")
    return _finish(db, op, actor, ctx, _result(cluster, pool, reasons, blocking), blocking)


def dry_run_pool_members(db, cluster: Cluster, *, pool: str, add: list[uuid.UUID], remove: list[uuid.UUID], actor: str, correlation_id=None) -> Operation:
    target = _target(db, cluster)
    wls = {w.id: w for w in db.query(Workload).filter(Workload.cluster_id == cluster.id, Workload.id.in_(list(add) + list(remove))).all()}
    ctx = {"cluster": cluster.name, "pool": pool, "add": [], "remove": []}
    op = create_operation(db, OP_MEMBERS, cluster_id=cluster.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    try:
        with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as wc:
            pools = read_pools(wc)
        if pool not in pools:
            reasons.append(f"there is no pool '{pool}'")
            blocking.append("SAFE-POOL-001")
        else:
            by_vmid = {w.vmid: w for w in wls.values()}
            current = {p: set(v["members"]) for p, v in pools.items()}
            new, gone, errs = plan_members(current, pool, [wls[i].vmid for i in add if i in wls], [wls[i].vmid for i in remove if i in wls])
            if len(wls) != len(set(add) | set(remove)):
                errs.append("a chosen guest is not part of this cluster")
            reasons += errs
            if errs:
                blocking.append("SAFE-POOL-001")
            ctx["add"], ctx["remove"] = new, gone
            lacking = missing_privileges(db, target, 0, ["Pool.Allocate"], path=f"/pool/{pool}")
            for v in new:
                lacking = (lacking or []) + [p for p in (missing_privileges(db, target, v, ["VM.Allocate"]) or []) if p not in (lacking or [])]
            if lacking:
                reasons.append(permission_reason(sorted(set(lacking)), "change pool members"))
                blocking.append("SAFE-PERMISSION-001")
            if not blocking:
                name = lambda v: (by_vmid[v].name or f"guest {v}") + f" ({v})"  # noqa: E731
                if new:
                    reasons.append(f"adds {', '.join(name(v) for v in new)} to '{pool}'")
                if gone:
                    reasons.append(f"removes {', '.join(name(v) for v in gone)} from '{pool}'")
                reasons.append("Only pool membership changes: no disk, power state or setting of any guest.")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"could not check the cluster: {str(exc)[:160]}")
        blocking.append("SAFE-POOL-002")
    return _finish(db, op, actor, ctx, _result(cluster, pool, reasons, blocking), blocking)


def approve(db, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_pool_op(db, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise PoolError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    cluster = db.query(Cluster).filter(Cluster.id == op.cluster_id).one()
    target = _target(db, cluster)
    ctx = dict(op.context or {})
    kind, pool = op.operation_type_id, ctx["pool"]
    try:
        creds = load_pve_credentials(db, target, "maintenance")
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            with PveMaintenanceClient(creds) as wc:
                pools = read_pools(wc)
            problem = None
            if kind == OP_CREATE and pool in pools:
                problem = f"the pool '{pool}' now exists"
            elif kind != OP_CREATE and pool not in pools:
                problem = f"the pool '{pool}' is gone"
            elif kind == OP_DELETE and (pools[pool]["members"] or pools[pool]["other_members"]):
                release_locks_for_operation(db, op.id)
                return block_operation(db, op, blocking_safety_rules=["SAFE-POOL-001"], actor="system")
            elif kind == OP_MEMBERS:
                _n, _g, errs = plan_members({p: set(v["members"]) for p, v in pools.items()}, pool, ctx["add"], ctx["remove"])
                if errs:
                    release_locks_for_operation(db, op.id)
                    return block_operation(db, op, blocking_safety_rules=["SAFE-POOL-001"], actor="system")
            if problem:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"{problem}; nothing was changed")
            try:
                acquire_lock(db, resource_type="pool", resource_id=_lock_id(cluster, pool), operation_id=op.id, reason=kind)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")
        if op.status == "executing":
            with PveMaintenanceClient(creds) as wc:
                present = pool in {p["poolid"] for p in wc._get("/pools") or []}  # a resumed run may find the change already made
                if kind == OP_CREATE:
                    if not present:
                        wc.create_pool(pool, ctx.get("comment") or "")
                elif kind == OP_DELETE:
                    if present:
                        wc.delete_pool(pool)
                else:
                    if ctx["add"]:
                        wc.set_pool_members(pool, ctx["add"], remove=False)
                    if ctx["remove"]:
                        wc.set_pool_members(pool, ctx["remove"], remove=True)
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            with PveMaintenanceClient(creds) as wc:
                pools = read_pools(wc)
            problems: list[str] = []
            if kind == OP_CREATE and pool not in pools:
                problems.append("Proxmox does not list the new pool")
            elif kind == OP_DELETE and pool in pools:
                problems.append("Proxmox still lists the pool")
            elif kind == OP_MEMBERS:
                have = set(pools.get(pool, {}).get("members", {}))
                problems += [f"guest {v} is not in the pool" for v in ctx["add"] if v not in have]
                problems += [f"guest {v} is still in the pool" for v in ctx["remove"] if v in have]
            op.verification_result = {"pool": pool, "problems": problems}
            db.commit()
            release_locks_for_operation(db, op.id)
            if problems:
                return fail_operation(db, op, error="; ".join(problems))
            try:
                run_discovery(db, target, actor="operation")
            except Exception:  # noqa: BLE001
                pass
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE)
        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")
    except MutationsDisabledError as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=friendly_error(exc) if "Permission check failed" in str(exc) else f"unexpected error: {exc}")
