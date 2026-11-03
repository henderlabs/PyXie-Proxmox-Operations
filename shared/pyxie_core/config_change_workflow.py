"""Change a guest's configuration options (workload.config_change): the Config tab's single, safe write path.

Dry run: read the guest's live config from Proxmox, validate every requested value against its option spec (guest_config.py),
diff against the current config, check the lock/privilege situation, and say for each change whether it applies live or waits for
the next restart. Approval -> revalidate (config digest unchanged, no lock) -> one config PUT carrying Proxmox's `digest` so
nothing changed behind PyXie's back is overwritten -> re-read and verify every key. Also discards pending (restart-waiting)
changes via Proxmox's `revert`.
"""

from sqlalchemy.orm import Session

from . import rollback
from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .guest_config import SPECS, agent_enabled, comparable, plan_changes
from .labels import vm_label
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .maintenance import vm_lock_reason
from .models import Cluster, Node, Operation, PveTarget, Workload
from .operations_engine import approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient
from .vm_locks import lock_context


class ConfigChangeError(Exception):
    pass


def _kind(workload: Workload) -> str:
    return "vm" if workload.type == "vm" else "ct"


def _load_context(db: Session, workload: Workload):
    node = db.query(Node).filter(Node.id == workload.node_id).one()
    cluster = db.query(Cluster).filter(Cluster.id == workload.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    return node, cluster, target


def read_guest(db: Session, workload: Workload) -> dict:
    """Live read: {config, pending, privileges|None}. Raises ConfigChangeError if Proxmox cannot be read."""
    node, cluster, target = _load_context(db, workload)
    try:
        client, _cred = build_pve_client(db, target)
        with client:
            if workload.type == "vm":
                config, pending = client.qemu_config(node.name, workload.vmid), client.qemu_pending(node.name, workload.vmid)
            else:
                config, pending = client.lxc_config(node.name, workload.vmid), client.lxc_pending(node.name, workload.vmid)
    except Exception as exc:  # noqa: BLE001
        raise ConfigChangeError(f"could not read {vm_label(workload)} from Proxmox: {str(exc)[:160]}") from exc
    privs = None
    try:
        with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as w:
            perms = w._get("/access/permissions", {"path": f"/vms/{workload.vmid}"}) or {}
            privs = set()
            for p in perms.values():
                privs |= set((p or {}).keys())
    except Exception:  # noqa: BLE001 -- unknown privileges are reported as unknown, not as missing
        privs = None
    return {"config": config or {}, "pending": pending or [], "privileges": privs, "node": node.name}


def dry_run_config_change(db: Session, workload: Workload, *, changes: dict, revert: list[str] | None = None, actor: str, correlation_id=None) -> Operation:
    if workload.is_missing:
        raise ConfigChangeError("this workload is no longer in the inventory")
    kind = _kind(workload)
    node, cluster, target = _load_context(db, workload)
    live = read_guest(db, workload)
    cfg = live["config"]
    planned, errors, unchanged = plan_changes(kind, cfg, changes or {})
    if errors:
        raise ConfigChangeError("; ".join(f"{k}: {v}" for k, v in errors.items()))
    pending_now = {r["key"] for r in live["pending"] if "pending" in r or r.get("delete")}
    reverts = [k for k in (revert or []) if k in pending_now]
    if not planned and not reverts:
        raise ConfigChangeError("nothing to change: " + ("those values are already set" if unchanged else "no change was requested"))

    running = workload.status == "running"
    reasons: list[str] = []
    blocking: list[str] = []
    lock_reason = vm_lock_reason(workload.vmid, cfg, **lock_context(db, workload))
    if lock_reason:
        reasons.append(lock_reason)
        blocking.append("SAFE-LOCK-001")
    if workload.status not in ("running", "stopped"):
        reasons.append(f"workload status is '{workload.status}': not safe to change its configuration right now")
        blocking.append("SAFE-MIGRATE-001")
    privs = live["privileges"]
    view = []
    for c in planned:
        ok = None if privs is None else (c["priv"] in privs)
        when = "live" if c["apply"] == "live" else ("restart" if running else "now")
        view.append({**{k: c[k] for k in ("key", "label", "old", "new", "remove", "priv")}, "applies": when, "priv_ok": ok})
        if ok is False:
            reasons.append(f"{c['label']}: the Proxmox account PyXie uses lacks {c['priv']}, so this change would be refused. Grant it (your call) or change it on the node.")
            blocking.append("SAFE-PERM-001")
    restart_needed = [c["label"] for c in view if c["applies"] == "restart"]
    if restart_needed:
        reasons.append("Takes effect after the guest is restarted (Proxmox keeps it as a pending change until then): " + ", ".join(restart_needed) + ".")
    if reverts:
        reasons.append("Will discard the pending (restart-waiting) change for: " + ", ".join(reverts) + ".")

    context = {
        "workload_id": str(workload.id), "vmid": workload.vmid, "node": node.name, "kind": kind, "digest": cfg.get("digest"),
        "set": {c["key"]: c["value"] for c in planned if not c["remove"]}, "delete": [c["key"] for c in planned if c["remove"]],
        "revert": reverts, "changes": view,
    }
    op = create_operation(db, "workload.config_change", cluster_id=cluster.id, node_id=node.id, workload_id=workload.id,
                          correlation_id=correlation_id, context=context, created_by=actor)
    result = {"workload": vm_label(workload), "node": node.name, "changes": view, "reverts": reverts, "unchanged": unchanged,
              "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": sorted(set(blocking))}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_config_change(db: Session, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one()
    ctx = dict(op.context or {})
    workload = db.query(Workload).filter(Workload.id == ctx["workload_id"]).one()
    node, cluster, target = _load_context(db, workload)
    kind = ctx["kind"]
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            live = read_guest(db, workload)
            cfg = live["config"]
            if ctx.get("digest") and cfg.get("digest") != ctx["digest"]:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"{vm_label(workload)}'s configuration changed in Proxmox after the preview (someone or something edited it). Nothing was written: preview again.")
            if cfg.get("lock"):
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            try:
                acquire_lock(db, resource_type="workload", resource_id=workload.id, operation_id=op.id, reason="workload.config_change")
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")

        if op.status == "executing":
            with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as w:
                w.set_guest_config(node.name, workload.vmid, kind=kind, set_keys=ctx.get("set") or {}, delete=ctx.get("delete") or [],
                                   revert=ctx.get("revert") or [], digest=ctx.get("digest"))
            op = enter_stage(db, op, status="verifying", stage="verifying")

        if op.status == "verifying":
            after = read_guest(db, workload)["config"]
            specs = SPECS[kind]
            problems = []
            for key, want in (ctx.get("set") or {}).items():
                got = after.get(key)
                if key == "agent":
                    ok = agent_enabled(got) == agent_enabled(want)
                else:
                    ok = comparable(specs[key], got) == comparable(specs[key], want)
                if not ok:
                    problems.append(f"{key} reads '{got}', expected '{want}'")
            for key in ctx.get("delete") or []:
                if after.get(key) not in (None, ""):
                    problems.append(f"{key} is still set")
            op.verification_result = {"checked": list((ctx.get("set") or {}).keys()) + (ctx.get("delete") or []), "problems": problems}
            db.commit()
            release_locks_for_operation(db, op.id)
            if problems:
                return fail_operation(db, op, error="read-back after the change did not match: " + "; ".join(problems))
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE)
        raise ConfigChangeError(f"operation {op.id} reached an unexpected state ({op.status})")
    except MutationsDisabledError as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error: {exc}")
