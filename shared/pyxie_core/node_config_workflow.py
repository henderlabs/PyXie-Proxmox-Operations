"""Change a host's Proxmox settings (host.config_change): node options, DNS, time zone, hosts file.

Same shape as the guest Config tab: read live -> validate -> preview (old -> new, privilege check, what it affects) -> approve ->
digest-checked write -> re-read and verify. Never touches network interfaces. A host in the middle of a maintenance or other
node-locking operation is refused (the node lock).
"""

from sqlalchemy.orm import Session

from . import rollback
from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Node, Operation, PveTarget
from .node_config import PRIV, SPECS, plan_changes, section_payloads
from .operations_engine import approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient


class NodeConfigChangeError(Exception):
    pass


def same_value(got, want) -> bool:
    """Read-back comparison for a host setting. Proxmox hands back some text (the description, the hosts file) with a trailing
    newline or CRLF line ends, so whitespace at the ends and line-end style do not count as a difference. Pure."""
    if got is None:
        return False
    return str(got).replace("\r\n", "\n").strip() == str(want).replace("\r\n", "\n").strip()


def _context(db: Session, node: Node):
    cluster = db.query(Cluster).filter(Cluster.id == node.cluster_id).one()
    return cluster, db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()


def read_node(db: Session, node: Node) -> dict:
    """Live read: {values: {key: str|None}, digests: {config, hosts}, privileges|None}."""
    cluster, target = _context(db, node)
    try:
        client, _cred = build_pve_client(db, target)
        with client:
            cfg = client._get(f"/nodes/{node.name}/config") or {}
            dns = client._get(f"/nodes/{node.name}/dns") or {}
            tm = client._get(f"/nodes/{node.name}/time") or {}
            hosts = client._get(f"/nodes/{node.name}/hosts") or {}
    except Exception as exc:  # noqa: BLE001
        raise NodeConfigChangeError(f"could not read {node.name} from Proxmox: {str(exc)[:160]}") from exc
    values = {k: cfg.get(k) for k, s in SPECS.items() if s["section"] == "config"}
    values.update({k: dns.get(k) for k in ("dns1", "dns2", "dns3", "search")})
    values["timezone"] = tm.get("timezone")
    values["hosts"] = hosts.get("data")
    privs = None
    try:
        with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as w:
            perms = w._get("/access/permissions", {"path": f"/nodes/{node.name}"}) or {}
            privs = set()
            for p in perms.values():
                privs |= set((p or {}).keys())
    except Exception:  # noqa: BLE001
        privs = None
    return {"values": values, "digests": {"config": cfg.get("digest"), "hosts": hosts.get("digest")}, "privileges": privs}


def dry_run_node_config(db: Session, node: Node, *, changes: dict, actor: str, correlation_id=None) -> Operation:
    cluster, target = _context(db, node)
    live = read_node(db, node)
    planned, errors, unchanged = plan_changes(live["values"], changes or {}, node.name)
    if errors:
        raise NodeConfigChangeError("; ".join(f"{k}: {v}" for k, v in errors.items()))
    if not planned:
        raise NodeConfigChangeError("nothing to change: " + ("those values are already set" if unchanged else "no change was requested"))
    reasons: list[str] = []
    blocking: list[str] = []
    privs = live["privileges"]
    if privs is not None and PRIV not in privs:
        reasons.append(f"the Proxmox account PyXie uses lacks {PRIV} on this host, so these changes would be refused")
        blocking.append("SAFE-PERM-001")
    keys = {c["key"] for c in planned}
    if keys & {"dns1", "dns2", "dns3", "search"}:
        reasons.append("DNS changes take effect at once for everything on this host (package updates, backups, cluster name lookups).")
    if "hosts" in keys:
        reasons.append("The hosts file is replaced as a whole. It still resolves this host's own name, but check any entries you removed.")
    if "timezone" in keys:
        reasons.append("The time zone changes how this host's clock and logs display; the actual time is unchanged.")
    view = [{**{k: c[k] for k in ("key", "label", "section", "old", "new", "remove")}, "applies": "immediately"} for c in planned]
    context = {"node": node.name, "node_id": str(node.id), "digests": live["digests"], "payloads": section_payloads(live["values"], planned), "changes": view}
    op = create_operation(db, "host.config_change", cluster_id=cluster.id, node_id=node.id, correlation_id=correlation_id, context=context, created_by=actor)
    result = {"node": node.name, "changes": view, "unchanged": unchanged, "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": blocking}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=blocking, actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_node_config(db: Session, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one()
    ctx = dict(op.context or {})
    node = db.query(Node).filter(Node.id == ctx["node_id"]).one()
    cluster, target = _context(db, node)
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            live = read_node(db, node)
            for section, key in (("config", "config"), ("hosts", "hosts")):
                if section in ctx["payloads"] and ctx["digests"].get(key) and live["digests"].get(key) != ctx["digests"][key]:
                    release_locks_for_operation(db, op.id)
                    return fail_operation(db, op, error=f"{node.name}'s {section} changed in Proxmox after the preview. Nothing was written: preview again.")
            try:
                acquire_lock(db, resource_type="node", resource_id=node.id, operation_id=op.id, reason="host.config_change", ttl_seconds=600)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")
        if op.status == "executing":
            with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as w:
                for section, payload in ctx["payloads"].items():
                    w.set_node_config(node.name, section, payload["set"], payload["delete"], digest=ctx["digests"].get("hosts" if section == "hosts" else "config") if section in ("config", "hosts") else None)
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            after = read_node(db, node)["values"]
            problems = []
            for section, payload in ctx["payloads"].items():
                for k, want in payload["set"].items():
                    got = after.get(k)
                    ok = same_value(got, want)
                    if not ok:
                        problems.append(f"{k} reads '{got}', expected '{want}'")
                for k in payload["delete"]:
                    if after.get(k) not in (None, ""):
                        problems.append(f"{k} is still set")
            op.verification_result = {"problems": problems}
            db.commit()
            release_locks_for_operation(db, op.id)
            if problems:
                return fail_operation(db, op, error="read-back after the change did not match: " + "; ".join(problems))
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE)
        raise NodeConfigChangeError(f"operation {op.id} reached an unexpected state ({op.status})")
    except MutationsDisabledError as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error: {exc}")
