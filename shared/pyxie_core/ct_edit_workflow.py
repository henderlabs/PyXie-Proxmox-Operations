"""Container edits: change cores / memory / swap (workload.ct_resize) and add, edit or remove a network device (workload.ct_nic_change).

A container's settings change live (no power cycle), so these are short config writes through the same Safety Contract: dry-run against the real cluster -> approve ->
revalidate -> lock -> write -> read back (pending-aware) -> audit. Container network lines differ from a VM's: `name=eth0,bridge=vmbr0,hwaddr=...,ip=dhcp|CIDR,gw=...,tag=...,
firewall=1,type=veth`. Settings this operation does not manage (rate, MTU, IPv6, trunks) are kept as they are. Disks use the disk-grow operation, clone the clone operation.
"""

import ipaddress
import re

from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .hw_edit_workflow import pending_value
from .labels import vm_label
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Node, Operation, PveTarget, Workload, WorkloadNic
from .nic_workflow import BRIDGE_RE, bridges_by_node, check_bridge, sdn_path, valid_mac
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_permissions import friendly_error, missing_privileges, permission_reason
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient
from . import guest_kind as gk
from . import rollback
from .labels import mem_gb

OP_RESIZE, OP_NIC = "workload.ct_resize", "workload.ct_nic_change"
NET_RE = re.compile(r"^net(\d+)$")
MANAGED = ("name", "bridge", "hwaddr", "ip", "gw", "tag", "firewall")
GIB = 1 << 30


class CtEditError(Exception):
    pass


def parse_ct_net(value) -> dict:
    """'name=eth0,bridge=vmbr0,ip=dhcp,type=veth' -> {"name": "eth0", "bridge": "vmbr0", ...} in order. Pure."""
    out: dict[str, str] = {}
    for p in str(value or "").split(","):
        k, _, v = p.partition("=")
        if k:
            out[k] = v
    return out


def ct_net_fields(value) -> dict:
    n = parse_ct_net(value)
    return {"name": n.get("name"), "bridge": n.get("bridge"), "tag": int(n["tag"]) if str(n.get("tag", "")).isdigit() else None, "firewall": str(n.get("firewall", "0")) in ("1", "true"),
            "ip": n.get("ip"), "gw": n.get("gw"), "mac": n.get("hwaddr")}


def build_ct_net(old: dict | None, *, name: str, bridge: str, tag: int | None, firewall: bool, ip: str | None, gw: str | None, mac: str | None) -> str:
    """The container NIC line for these values, keeping any setting the old line has that is not managed here. Pure."""
    keep = {k: v for k, v in (old or {}).items() if k not in MANAGED}
    out = [f"name={name}", f"bridge={bridge}"]
    if mac:
        out.append(f"hwaddr={mac}")
    if ip:
        out.append(f"ip={ip}")
    if gw:
        out.append(f"gw={gw}")
    if tag is not None:
        out.append(f"tag={tag}")
    if firewall:
        out.append("firewall=1")
    out += [f"{k}={v}" for k, v in keep.items()]
    return ",".join(out)


def next_free_net(config: dict) -> tuple[str, str] | None:
    used = {int(m.group(1)) for k in config if (m := NET_RE.match(k))}
    i = next((i for i in range(32) if i not in used), None)
    return (f"net{i}", f"eth{i}") if i is not None else None


def valid_ip(ip: str | None) -> str | None:
    """Why the address setting cannot be used, or None. 'dhcp', 'manual' or an IPv4 address with its prefix. Pure."""
    if not ip or ip in ("dhcp", "manual"):
        return None
    try:
        i = ipaddress.ip_interface(ip)
        return None if i.version == 4 and "/" in ip else "use dhcp, manual, or an IPv4 address with its prefix like 192.168.1.50/24"
    except ValueError:
        return "use dhcp, manual, or an IPv4 address with its prefix like 192.168.1.50/24"


def _ctx(db, workload: Workload):
    node = db.query(Node).filter(Node.id == workload.node_id).one()
    cluster = db.query(Cluster).filter(Cluster.id == workload.cluster_id).one()
    return node, cluster, db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()


def _finish(db, op, actor, ctx, result, blocking):
    op.context = ctx
    db.commit()
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def dry_run_ct_resize(db, workload: Workload, *, cores: int | None, memory_mb: int | None, swap_mb: int | None, actor: str, correlation_id=None) -> Operation:
    if workload.type != "lxc":
        raise CtEditError("this is for containers; VMs use Rightsizing / the hardware editor")
    node, cluster, target = _ctx(db, workload)
    ctx = {"node_id": str(node.id), "node": node.name, "vmid": workload.vmid, "cores": cores, "memory_mb": memory_mb, "swap_mb": swap_mb}
    op = create_operation(db, OP_RESIZE, cluster_id=cluster.id, node_id=node.id, workload_id=workload.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    for label, v, lo, hi in (("cores", cores, 1, 256), ("memory", memory_mb, 16, 4194304), ("swap", swap_mb, 0, 4194304)):
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi):
            reasons.append(f"{label} must be a whole number from {lo} to {hi}")
            blocking.append("SAFE-CT-001")
    if cores is None and memory_mb is None and swap_mb is None:
        reasons.append("nothing to change")
        blocking.append("SAFE-CT-001")
    cur: dict = {}
    if not blocking:
        try:
            client, _cred = build_pve_client(db, target)
            with client:
                cfg = gk.config(client, workload, node.name)
                st = client.node_status(node.name) or {}
            cur = {"cores": int(cfg.get("cores") or 0) or None, "memory_mb": int(cfg.get("memory") or 512), "swap_mb": int(cfg.get("swap") if cfg.get("swap") is not None else 512)}
            if cfg.get("lock"):
                reasons.append(f"Proxmox has the container locked ('{cfg.get('lock')}'): another task is working on it")
                blocking.append("SAFE-LOCK-001")
            changes = []
            for label, key, new in (("cores", "cores", cores), ("memory", "memory_mb", memory_mb), ("swap", "swap_mb", swap_mb)):
                if new is not None and new != cur[key]:
                    was = 'all' if cur[key] is None else (cur[key] if label == 'cores' else mem_gb(cur[key]))
                    changes.append(f"{label}: {was} -> {new if label == 'cores' else mem_gb(new)}")
            if not changes:
                reasons.append("the container already has exactly these values")
                blocking.append("SAFE-CT-001")
            else:
                reasons += changes
                reasons.append("a container takes these changes at once, without a restart" + (" (a lower memory limit that the container is already using more than makes it reclaim or be killed)" if memory_mb is not None and memory_mb < cur["memory_mb"] else ""))
            if memory_mb is not None and workload.status == "running":
                free = ((st.get("memory") or {}).get("total") or 0) - ((st.get("memory") or {}).get("used") or 0)
                extra = max(0, (memory_mb - cur["memory_mb"])) << 20
                if free and extra > free:
                    reasons.append(f"{node.name} has {free / GIB:.1f} GiB of memory free and this adds {extra / GIB:.1f} GiB")
                    blocking.append("SAFE-CT-001")
            need = (["VM.Config.CPU"] if cores is not None else []) + (["VM.Config.Memory"] if memory_mb is not None or swap_mb is not None else [])
            lacking = missing_privileges(db, target, workload.vmid, need) if need else None
            if lacking:
                reasons.append(permission_reason(lacking, "change container resources"))
                blocking.append("SAFE-PERMISSION-001")
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"could not read {vm_label(workload)} from Proxmox: {str(exc)[:140]}")
            blocking.append("SAFE-STORAGE-002")
    ctx["current"] = cur
    result = {"vmid": workload.vmid, "workload_name": workload.name, "node": node.name, "current": cur, "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": sorted(set(blocking))}
    return _finish(db, op, actor, ctx, result, blocking)


def dry_run_ct_nic(db, workload: Workload, *, action: str, net_id: str | None, bridge: str | None, tag: int | None, firewall: bool, ip: str | None, gateway: str | None, mac: str | None,
                   actor: str, correlation_id=None) -> Operation:
    if workload.type != "lxc":
        raise CtEditError("this is for containers; VMs use the network device editor")
    if action not in ("add", "edit", "remove"):
        raise CtEditError("action must be 'add', 'edit' or 'remove'")
    node, cluster, target = _ctx(db, workload)
    ctx = {"node_id": str(node.id), "node": node.name, "vmid": workload.vmid, "action": action, "net_id": net_id, "was_running": workload.status == "running"}
    op = create_operation(db, OP_NIC, cluster_id=cluster.id, node_id=node.id, workload_id=workload.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    mac = (mac or "").strip() or None
    ip = (ip or "").strip() or None
    gateway = (gateway or "").strip() or None
    old_line = new_line = None
    old: dict = {}
    try:
        client, _cred = build_pve_client(db, target)
        with client:
            cfg = gk.config(client, workload, node.name)
        if cfg.get("lock"):
            reasons.append(f"Proxmox has the container locked ('{cfg.get('lock')}'): another task is working on it")
            blocking.append("SAFE-LOCK-001")
        name = None
        if action == "add":
            slot = next_free_net(cfg)
            if slot is None:
                reasons.append("no free network device slot")
                blocking.append("SAFE-CT-001")
            else:
                net_id, name = net_id or slot[0], f"eth{(net_id or slot[0])[3:]}"
                if not NET_RE.match(net_id):
                    reasons.append(f"'{net_id}' is not a network device name (net0, net1, ...)")
                    blocking.append("SAFE-CT-001")
                elif net_id in cfg:
                    reasons.append(f"'{net_id}' is already in use")
                    blocking.append("SAFE-CT-001")
        else:
            old_line = str(cfg.get(net_id) or "")
            if not NET_RE.match(net_id or "") or not old_line:
                reasons.append(f"'{net_id}' is not a network device of this container")
                blocking.append("SAFE-CT-001")
            else:
                old = parse_ct_net(old_line)
                name = old.get("name") or f"eth{net_id[3:]}"
        ctx["net_id"] = net_id
        if action == "remove":
            if old_line:
                reasons.append(f"{net_id} ({name} on {old.get('bridge')}) is removed from the container" + (" at once, while it runs" if workload.status == "running" else ""))
                if sum(1 for k in cfg if NET_RE.match(k)) == 1:
                    reasons.append("this is the container's only network device: it will have no network afterwards")
        elif not blocking:
            bridge = (bridge or old.get("bridge") or "").strip()
            if not BRIDGE_RE.match(bridge or ""):
                reasons.append("choose a bridge")
                blocking.append("SAFE-NIC-001")
            if tag is not None and not 1 <= int(tag) <= 4094:
                reasons.append("a VLAN tag is a number from 1 to 4094 (or empty)")
                blocking.append("SAFE-NIC-001")
            bad = valid_ip(ip)
            if bad:
                reasons.append(bad)
                blocking.append("SAFE-NIC-001")
            if gateway:
                try:
                    ipaddress.IPv4Address(gateway)
                except ValueError:
                    reasons.append("the gateway must be an IPv4 address")
                    blocking.append("SAFE-NIC-001")
            if action == "edit" and mac is None:
                mac = old.get("hwaddr")
            if mac:
                problem = valid_mac(mac)
                if problem:
                    reasons.append(problem)
                    blocking.append("SAFE-NIC-001")
                else:
                    dup = (db.query(WorkloadNic, Workload).join(Workload, Workload.id == WorkloadNic.workload_id).filter(WorkloadNic.mac.ilike(mac), Workload.is_missing.is_(False)).all())
                    dup = [w for n, w in dup if not (w.id == workload.id and n.slot == net_id)]
                    if dup:
                        reasons.append(f"MAC {mac.upper()} is already used by {', '.join(vm_label(w) for w in dup)}")
                        blocking.append("SAFE-NIC-001")
            if "SAFE-NIC-001" not in blocking:
                names = [n.name for n in db.query(Node).filter(Node.cluster_id == cluster.id, Node.is_missing.is_(False)).order_by(Node.name).all()]
                blocks, warns = check_bridge(bridge, tag, node.name, bridges_by_node(db, cluster), names)
                for b in blocks:
                    reasons.append(b)
                    blocking.append("SAFE-NIC-001")
                reasons += warns
                new_ip = ip if ip is not None else old.get("ip")
                new_gw = gateway if gateway is not None else old.get("gw")
                new_line = build_ct_net(old if old_line else None, name=name, bridge=bridge, tag=tag if tag is not None else None, firewall=bool(firewall), ip=new_ip, gw=new_gw, mac=mac)
                ctx["expected"] = {"bridge": bridge, "tag": tag, "firewall": bool(firewall), "ip": new_ip, "gw": new_gw, "mac": mac}
                if action == "add":
                    reasons.append(f"a new network device {net_id} ({name}) on {bridge}" + (f", VLAN {tag}" if tag is not None else ", untagged") + f", address {new_ip or 'not set'}" + (": hot-plugged into the running container" if workload.status == "running" else ""))
                else:
                    f_old = ct_net_fields(old_line)
                    diffs = [f"{k}: {f_old[k] if f_old[k] not in (None, False) else '-'} -> {v if v not in (None, False) else '-'}" for k, v in
                             (("bridge", bridge), ("tag", tag), ("firewall", bool(firewall)), ("ip", new_ip), ("gw", new_gw), ("mac", mac)) if (f_old[k] or None) != (v or None) and not (k == "firewall" and f_old[k] == v)]
                    if not diffs:
                        reasons.append("nothing to change: this device already has exactly these settings")
                        blocking.append("SAFE-NIC-001")
                    else:
                        reasons.append(f"{net_id}: " + "; ".join(diffs))
        need = ["VM.Config.Network"]
        lacking = missing_privileges(db, target, workload.vmid, need)
        if lacking:
            reasons.append(permission_reason(lacking, "change network devices"))
            blocking.append("SAFE-PERMISSION-001")
        if action != "remove" and bridge:
            ls = missing_privileges(db, target, workload.vmid, ["SDN.Use"], path=sdn_path(bridges_by_node(db, cluster), node.name, bridge))
            if ls:
                reasons.append(permission_reason(ls, f"attach network devices to bridge {bridge}"))
                blocking.append("SAFE-PERMISSION-001")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"could not read {vm_label(workload)} from Proxmox: {str(exc)[:140]}")
        blocking.append("SAFE-STORAGE-002")
    ctx.update({"old_line": old_line, "new_line": new_line})
    result = {"vmid": workload.vmid, "workload_name": workload.name, "node": node.name, "action": action, "net_id": net_id, "old_line": old_line, "new_line": new_line, "eligible": not blocking,
              "reasons": reasons, "blocking_safety_rules": sorted(set(blocking))}
    return _finish(db, op, actor, ctx, result, blocking)


def approve(db, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_ct_edit(db, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise CtEditError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    workload = db.query(Workload).filter(Workload.id == op.workload_id).one()
    node, cluster, target = _ctx(db, workload)
    ctx = dict(op.context or {})
    kind = op.operation_type_id
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            rc, _cred = build_pve_client(db, target)
            with rc:
                cfg = gk.config(rc, workload, node.name)
            drift = bool(cfg.get("lock"))
            if kind == OP_NIC:
                live = str(cfg.get(ctx["net_id"]) or "")
                drift = drift or (ctx["action"] == "add" and bool(live)) or (ctx["action"] != "add" and live != (ctx.get("old_line") or ""))
            if drift:
                release_locks_for_operation(db, op.id)
                return block_operation(db, op, blocking_safety_rules=["SAFE-CT-001"], actor="system")  # changed since the preview
            try:
                acquire_lock(db, resource_type="workload", resource_id=workload.id, operation_id=op.id, reason=kind)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")
        if op.status == "executing":
            with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")).for_guest("lxc") as wc:
                if kind == OP_RESIZE:
                    wc.set_vm_config_keys(node.name, workload.vmid, {k: v for k, v in (("cores", ctx.get("cores")), ("memory", ctx.get("memory_mb")), ("swap", ctx.get("swap_mb"))) if v is not None})
                elif ctx["action"] == "remove":
                    wc.set_vm_config_keys(node.name, workload.vmid, {"delete": ctx["net_id"]})
                else:
                    wc.set_vm_config_keys(node.name, workload.vmid, {ctx["net_id"]: ctx["new_line"]})
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            rc, _cred = build_pve_client(db, target)
            with rc:
                cfg = gk.config(rc, workload, node.name)
                pending = rc.lxc_pending(node.name, workload.vmid)
            problems = []
            if kind == OP_RESIZE:
                for key, want in (("cores", ctx.get("cores")), ("memory", ctx.get("memory_mb")), ("swap", ctx.get("swap_mb"))):
                    if want is not None and str(pending_value(pending, cfg, key)) != str(want):
                        problems.append(f"{key} is {pending_value(pending, cfg, key)}, expected {want}")
                op.verification_result = {"cores": cfg.get("cores"), "memory": cfg.get("memory"), "swap": cfg.get("swap")}
            else:
                got = pending_value(pending, cfg, ctx["net_id"])
                if ctx["action"] == "remove":
                    if ctx["net_id"] in cfg and not any(r.get("key") == ctx["net_id"] and r.get("delete") for r in pending or []):
                        problems.append(f"{ctx['net_id']} is still present")
                else:
                    f, want = ct_net_fields(got), ctx.get("expected") or {}
                    for k in ("bridge", "tag", "firewall"):
                        if (f.get(k) or None) != (want.get(k) or None) and not (k == "firewall" and f.get(k) == want.get(k)):
                            problems.append(f"{k} is {f.get(k)}, expected {want.get(k)}")
                    if want.get("ip") and f.get("ip") != want["ip"]:
                        problems.append(f"ip is {f.get('ip')}, expected {want['ip']}")
                op.verification_result = {"net_id": ctx["net_id"], "line": got}
            db.commit()
            release_locks_for_operation(db, op.id)
            if problems:
                return fail_operation(db, op, error="read-back after the change did not match: " + "; ".join(problems))
            try:
                from .discovery import run_discovery
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
