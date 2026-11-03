"""Host network interfaces from a host's Config tab: stage an edit, then reload (apply) or discard.

Proxmox keeps a two-step model and PyXie keeps it: STAGE writes /etc/network/interfaces.new only (the running network is untouched), RELOAD
applies the staged file with ifupdown2, DISCARD deletes it. Every step is previewed, approved and audited like any other write. An edit that would
move the address PyXie reaches the host on is refused outright (Proxmox cannot roll a reload back; that is a console job); other edits to a link
that carries PyXie or the cluster need the host's name typed to confirm the reload.

Operation types: host.network_stage, host.network_apply, host.network_discard.
"""

import re
import time

from sqlalchemy.orm import Session

from . import rollback
from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .host_network import EDITABLE_TYPES, PRIV, SPECS, assess, current_value, keys_for, payload, plan_changes, staged_problems
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Node, Operation, PveTarget
from .network_view import proofs_for_node
from .operations_engine import approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient


class HostNetworkError(Exception):
    pass


def same_setting(got, want) -> bool:
    """Read-back comparison for one interface setting. Proxmox returns some text (a comment) with a trailing newline, so whitespace at
    the ends does not count; an empty value and a missing one are the same. Pure."""
    norm = lambda v: (str(v).strip() if v is not None else "") or None
    return norm(got) == norm(want)


def _context(db: Session, node: Node):
    cluster = db.query(Cluster).filter(Cluster.id == node.cluster_id).one()
    return cluster, db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()


def _carries(db: Session, cluster: Cluster, node: Node, rows: list[dict]) -> dict[str, list[str]]:
    """What each interface is PROVEN to carry (management address, migration network, corosync link, guests). Missing facts are simply absent."""
    from .network_workflow import list_workload_nics, read_cluster_net_facts, read_sdn
    facts = read_cluster_net_facts(db, cluster)
    try:
        zones = {z["bridge"]: z["zone"] for z in read_sdn(db, cluster).get("zones", []) if z.get("bridge")}
    except Exception:  # noqa: BLE001
        zones = {}
    per_bridge: dict[str, int] = {}
    try:
        for w in list_workload_nics(db, cluster):
            if w.get("node") == node.name:
                for n in w.get("nics", []):
                    if n.get("bridge"):
                        per_bridge[n["bridge"]] = per_bridge.get(n["bridge"], 0) + 1
    except Exception:  # noqa: BLE001
        pass
    ifaces = [{"iface": r["iface"], "address": r.get("address"), "ports": r.get("bridge_ports") or r.get("slaves")} for r in rows if r.get("iface") != "lo"]
    return proofs_for_node(ifaces, mgmt_ip=node.management_ip, migration_cidr=facts.get("migration_cidr"), corosync_addrs=(facts.get("corosync") or {}).get(node.name, []),
                           zone_bridges=zones, guests_per_bridge=per_bridge)


def read_network(db: Session, node: Node, *, with_carries: bool = True) -> dict:
    """Live read: {rows: [raw interface dicts, staged values included], staged: diff text|None, carries: {iface: [..]}, privileges|None}."""
    cluster, target = _context(db, node)
    try:
        client, _cred = build_pve_client(db, target)
        with client:
            rows, staged = client.node_network_state(node.name)
    except Exception as exc:  # noqa: BLE001
        raise HostNetworkError(f"could not read {node.name}'s network from Proxmox: {str(exc)[:160]}") from exc
    rows = [r for r in rows if r.get("iface") and r.get("iface") != "lo"]
    privs = None
    try:
        with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as w:
            perms = w._get("/access/permissions", {"path": f"/nodes/{node.name}"}) or {}
            privs = set()
            for p in perms.values():
                privs |= set((p or {}).keys())
    except Exception:  # noqa: BLE001
        privs = None
    return {"rows": rows, "staged": (staged or "").strip() or None, "privileges": privs,
            "carries": _carries(db, cluster, node, rows) if with_carries else {}}


def _row(rows: list[dict], name: str) -> dict | None:
    return next((r for r in rows if r.get("iface") == name), None)


def _perm_reason(live: dict) -> tuple[list[str], list[str]]:
    if live["privileges"] is not None and PRIV not in live["privileges"]:
        return [f"the Proxmox account PyXie uses lacks {PRIV} on this host, so this would be refused"], ["SAFE-PERM-001"]
    return [], []


def touched_in_diff(diff: str, names: list[str]) -> list[str]:
    """Interfaces a staged diff mentions. Conservative: an interface that only appears as diff context counts too (over-warn, never under-warn)."""
    return [n for n in names if re.search(rf"(?<![A-Za-z0-9._-]){re.escape(n)}(?![A-Za-z0-9._-])", diff or "")]


# --------------------------------------------------------------------------- stage


def dry_run_network_stage(db: Session, node: Node, *, iface: str, changes: dict, actor: str, correlation_id=None) -> Operation:
    cluster, _target = _context(db, node)
    live = read_network(db, node)
    row = _row(live["rows"], iface)
    if row is None:
        raise HostNetworkError(f"{node.name} has no interface called {iface}")
    planned, errors, unchanged = plan_changes(row, changes or {}, live["rows"])
    if errors:
        raise HostNetworkError("; ".join(f"{k}: {v}" for k, v in errors.items()))
    if not planned:
        raise HostNetworkError("nothing to change: " + ("those values are already set" if unchanged else "no change was requested"))
    carries = live["carries"].get(iface, [])
    risk = assess(row, planned, carries)
    reasons, blocking = _perm_reason(live)
    for rule, text in risk["blocked"]:
        reasons.append(text)
        blocking.append(rule)
    reasons += risk["warnings"]
    if live["staged"]:
        reasons.append("Proxmox already has staged network changes on this host; this edit is added to them. They all apply together at Reload.")
    reasons.append("This only STAGES the change in Proxmox's pending file. The running network is untouched until you press Reload on the host's Config tab.")
    view = [{k: c[k] for k in ("key", "label", "old", "new", "remove")} for c in planned]
    ctx = {"node": node.name, "node_id": str(node.id), "iface": iface, "iface_type": row.get("type"), "payload": payload(row, planned), "changes": view,
           "critical": risk["critical"], "carries": carries}
    op = create_operation(db, "host.network_stage", cluster_id=cluster.id, node_id=node.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    result = {"node": node.name, "iface": iface, "changes": view, "unchanged": unchanged, "eligible": not blocking, "reasons": reasons,
              "blocking_safety_rules": blocking, "critical": risk["critical"], "staged_only": True}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def _same_as_preview(row: dict, view: list[dict]) -> list[str]:
    """Keys whose live value no longer matches what the preview showed as the old value."""
    return [c["key"] for c in view if (current_value(row, c["key"]) or None) != (c["old"] or None)]


def execute_network_stage(db: Session, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one()
    ctx = dict(op.context or {})
    node = db.query(Node).filter(Node.id == ctx["node_id"]).one()
    cluster, target = _context(db, node)
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            live = read_network(db, node, with_carries=False)
            row = _row(live["rows"], ctx["iface"])
            if row is None or _same_as_preview(row, ctx["changes"]):
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"{ctx['iface']} changed in Proxmox after the preview. Nothing was written: preview again.")
            try:
                acquire_lock(db, resource_type="node", resource_id=node.id, operation_id=op.id, reason="host.network_stage", ttl_seconds=600)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")
        if op.status == "executing":
            p = ctx["payload"]
            with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as w:
                w.stage_node_network(node.name, ctx["iface"], p["type"], p["set"], p["delete"])
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            after = read_network(db, node, with_carries=False)
            row = _row(after["rows"], ctx["iface"]) or {}
            problems = []
            for c in ctx["changes"]:
                got = current_value(row, c["key"])
                if not same_setting(got, c["new"]):
                    problems.append(f"{c['key']} reads '{got}', expected '{c['new']}'")
            if not after["staged"]:
                problems.append("Proxmox does not report a staged change")
            op.verification_result = {"problems": problems, "staged": after["staged"]}
            db.commit()
            release_locks_for_operation(db, op.id)
            if problems:
                return fail_operation(db, op, error="read-back after staging did not match: " + "; ".join(problems))
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE)
        raise HostNetworkError(f"operation {op.id} reached an unexpected state ({op.status})")
    except MutationsDisabledError as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error: {exc}")


# --------------------------------------------------------------------------- apply (reload)


def dry_run_network_apply(db: Session, node: Node, *, actor: str, correlation_id=None) -> Operation:
    cluster, _target = _context(db, node)
    live = read_network(db, node)
    if not live["staged"]:
        raise HostNetworkError(f"{node.name} has no staged network changes to apply")
    names = [r["iface"] for r in live["rows"]]
    touched = touched_in_diff(live["staged"], names)
    critical_names = [n for n in touched if any(c.startswith(("host management", "cluster")) for c in live["carries"].get(n, []))]
    reasons, blocking = _perm_reason(live)
    for rule, text in staged_problems(live["rows"], node.management_ip):
        reasons.append(text)
        blocking.append(rule)
    if node.maintenance_mode is False and node.status == "online":
        reasons.append("Guests on this host may lose their network for a moment while the reload runs.")
    if critical_names:
        reasons.append("The staged file touches " + ", ".join(critical_names) + ", which carries " + "; ".join(sorted({c for n in critical_names for c in live["carries"].get(n, [])})) + ". "
                       "If the reload leaves the host unreachable, Proxmox cannot undo it: it has to be fixed at the host's console. Type the host name to confirm.")
    reasons.append("After the reload PyXie re-reads the host and reports whether it can still reach it, either way.")
    ctx = {"node": node.name, "node_id": str(node.id), "staged": live["staged"], "touched": touched, "critical": bool(critical_names), "confirm_name": node.name if critical_names else None}
    op = create_operation(db, "host.network_apply", cluster_id=cluster.id, node_id=node.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    result = {"node": node.name, "staged": live["staged"], "touched": touched, "critical": bool(critical_names), "confirm_name": ctx["confirm_name"], "eligible": not blocking,
              "reasons": reasons, "blocking_safety_rules": blocking}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def execute_network_apply(db: Session, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one()
    ctx = dict(op.context or {})
    node = db.query(Node).filter(Node.id == ctx["node_id"]).one()
    cluster, target = _context(db, node)
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            live = read_network(db, node, with_carries=False)
            if (live["staged"] or "") != (ctx["staged"] or ""):
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"{node.name}'s staged network changes are different from the preview. Nothing was applied: preview again.")
            try:
                acquire_lock(db, resource_type="node", resource_id=node.id, operation_id=op.id, reason="host.network_apply", ttl_seconds=900)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")
        if op.status == "executing":
            with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as w:
                upid = w.apply_node_network(node.name)
                task_ok, task_note = True, None
                if upid:
                    try:
                        res = w.wait_for_task(node.name, upid, timeout=180.0)
                        task_ok = res.status == "stopped" and (res.exit_status in (None, "OK"))
                        task_note = None if task_ok else f"the reload task ended with {res.exit_status or res.status}"
                    except Exception as exc:  # noqa: BLE001 -- the link can blip during a reload; the read-back below decides
                        task_note = f"could not follow the reload task ({str(exc)[:80]}); checking the host directly"
            op.pve_upid = upid
            op.pve_task_result = {"task_ok": task_ok, "note": task_note}
            db.commit()
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            problems, reached = [], False
            deadline = time.time() + 90
            while time.time() < deadline:
                try:
                    after = read_network(db, node, with_carries=False)
                    reached = True
                    break
                except HostNetworkError:
                    time.sleep(4)
            if not reached:
                problems.append("PyXie can no longer reach the host's Proxmox API after the reload. It has to be fixed at the host's console (or its management port/cable checked)")
            else:
                if after["staged"]:
                    problems.append("Proxmox still reports staged changes: the reload did not apply them")
                down = [r["iface"] for r in after["rows"] if r.get("type") in EDITABLE_TYPES and int(r.get("active") or 0) == 0 and int(r.get("autostart") or 0) == 1]
                if down:
                    problems.append("these interfaces are set to start at boot but are not up: " + ", ".join(down))
            res = dict(op.pve_task_result or {})
            if res.get("task_ok") is False:
                problems.append(res.get("note") or "the reload task failed")
            op.verification_result = {"problems": problems, "reached": reached}
            db.commit()
            release_locks_for_operation(db, op.id)
            if problems:
                return fail_operation(db, op, error="; ".join(problems))
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE)
        raise HostNetworkError(f"operation {op.id} reached an unexpected state ({op.status})")
    except MutationsDisabledError as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error: {exc}")


# --------------------------------------------------------------------------- discard


def dry_run_network_discard(db: Session, node: Node, *, actor: str, correlation_id=None) -> Operation:
    cluster, _target = _context(db, node)
    live = read_network(db, node, with_carries=False)
    if not live["staged"]:
        raise HostNetworkError(f"{node.name} has no staged network changes to discard")
    reasons, blocking = _perm_reason(live)
    reasons.append("Deletes the staged file only. The running network is not touched and nothing is reloaded. Anyone else's staged edits on this host are discarded with yours.")
    ctx = {"node": node.name, "node_id": str(node.id), "staged": live["staged"]}
    op = create_operation(db, "host.network_discard", cluster_id=cluster.id, node_id=node.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    result = {"node": node.name, "staged": live["staged"], "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": blocking}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def execute_network_discard(db: Session, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one()
    ctx = dict(op.context or {})
    node = db.query(Node).filter(Node.id == ctx["node_id"]).one()
    cluster, target = _context(db, node)
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            try:
                acquire_lock(db, resource_type="node", resource_id=node.id, operation_id=op.id, reason="host.network_discard", ttl_seconds=300)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")
        if op.status == "executing":
            with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as w:
                w.discard_node_network(node.name)
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            after = read_network(db, node, with_carries=False)
            problems = ["Proxmox still reports staged changes"] if after["staged"] else []
            op.verification_result = {"problems": problems}
            db.commit()
            release_locks_for_operation(db, op.id)
            if problems:
                return fail_operation(db, op, error="; ".join(problems))
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE)
        raise HostNetworkError(f"operation {op.id} reached an unexpected state ({op.status})")
    except MutationsDisabledError as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error: {exc}")


# --------------------------------------------------------------------------- read model for the UI


def describe(db: Session, node: Node) -> dict:
    """What the host Config tab's Network interfaces section shows."""
    live = read_network(db, node)
    out = []
    for r in sorted(live["rows"], key=lambda x: x["iface"]):
        typ = r.get("type")
        editable = typ in EDITABLE_TYPES
        out.append({
            "iface": r["iface"], "type": typ, "editable": editable, "active": bool(int(r.get("active") or 0)), "comments": r.get("comments"),
            "ports": r.get("bridge_ports") or r.get("slaves") or None, "cidr": r.get("cidr"), "gateway": r.get("gateway"), "mtu": r.get("mtu"),
            "carries": live["carries"].get(r["iface"], []),
            "fields": [{"key": k, "label": SPECS[k]["label"], "kind": SPECS[k]["kind"], "value": current_value(r, k), "hint": SPECS[k].get("hint"),
                        "options": SPECS[k].get("options")} for k in keys_for(typ)] if editable else [],
        })
    privs = live["privileges"]
    return {"node": node.name, "node_id": str(node.id), "interfaces": out, "staged": live["staged"], "priv": PRIV, "priv_ok": None if privs is None else PRIV in privs}
