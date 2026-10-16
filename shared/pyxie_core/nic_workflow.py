"""Network devices on a VM (workload.nic_change): add, edit or remove one NIC, with every field Proxmox offers for it that matters day to day:
model, bridge, VLAN tag, firewall, MAC address and link down. It generalises workload.network_vlan_change (VLAN tag only).

Same Safety Contract as every other operation: dry-run -> approve -> revalidate -> lock -> execute -> read the config back -> verify -> audit.
The preview reads every host's real bridges (Proxmox /nodes/{n}/network): a bridge the VM's own host lacks, or a VLAN it cannot carry, blocks the
change; a bridge another host lacks is a warning (the VM could not move there). A duplicate MAC is blocked. Settings other than the ones
edited here (rate limit, MTU, queues, trunks) are kept exactly as they are.
"""

import re

from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .labels import vm_label
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Node, Operation, PveTarget, Workload, WorkloadNic
from .network_workflow import _vlan_allowed, list_network_topology
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_permissions import friendly_error, missing_privileges, permission_reason
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient, self_vmid_from_env
from .hw_edit_workflow import pending_value
from . import rollback

OP_TYPE = "workload.nic_change"
MODELS = ["virtio", "e1000", "e1000e", "rtl8139", "vmxnet3"]
NET_RE = re.compile(r"^net(\d+)$")
MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
BRIDGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{0,14}$")
MANAGED = ("bridge", "tag", "firewall", "link_down")  # the keys this operation writes; everything else on the NIC is kept


class NicError(Exception):
    pass


def parse_net(value) -> dict:
    """'virtio=BC:24:11:4A:20:1C,bridge=vmbr0,tag=20,firewall=1,rate=50' -> {"model","mac","params": {key: value, ...} in order}. Pure."""
    parts = str(value or "").split(",")
    first = parts[0]
    model, _, mac = first.partition("=")
    params: dict[str, str] = {}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        if k:
            params[k] = v
    return {"model": model or None, "mac": mac or None, "params": params}


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def net_fields(value) -> dict:
    """The fields the editor shows for a NIC line. Pure."""
    n = parse_net(value)
    p = n["params"]
    return {"model": n["model"], "mac": n["mac"], "bridge": p.get("bridge"), "tag": int(p["tag"]) if str(p.get("tag", "")).isdigit() else None,
            "firewall": _truthy(p.get("firewall")), "link_down": _truthy(p.get("link_down"))}


def build_net(old: dict | None, *, model: str, bridge: str, tag: int | None, firewall: bool, mac: str | None, link_down: bool) -> str:
    """The NIC line for these values: the managed keys rewritten, any other setting of the old line (rate, mtu, queues, trunks) kept. Pure."""
    keep = {k: v for k, v in ((old or {}).get("params") or {}).items() if k not in MANAGED}
    out = [f"{model}={mac}" if mac else model, f"bridge={bridge}"]
    if tag is not None:
        out.append(f"tag={tag}")
    if firewall:
        out.append("firewall=1")
    if link_down:
        out.append("link_down=1")
    out += [f"{k}={v}" for k, v in keep.items()]
    return ",".join(out)


def next_free_slot(config: dict) -> str | None:
    used = {int(m.group(1)) for k in config if (m := NET_RE.match(k))}
    return next((f"net{i}" for i in range(32) if i not in used), None)


def valid_mac(mac: str) -> str | None:
    """Why the MAC cannot be used, or None. A unicast address has the lowest bit of its first byte clear. Pure."""
    if not MAC_RE.match(mac or ""):
        return "a MAC address is six pairs of hex digits like BC:24:11:4A:20:1C"
    if int(mac[:2], 16) & 1:
        return "that is a multicast MAC address (the first byte must be even)"
    return None


def _load_context(db, workload: Workload):
    node = db.query(Node).filter(Node.id == workload.node_id).one()
    cluster = db.query(Cluster).filter(Cluster.id == workload.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    return node, cluster, target


def bridges_by_node(db, cluster: Cluster) -> dict[str, dict[str, dict]]:
    """{node name: {bridge name: info}} from each host's own network config (read-only). A host that could not be read is absent."""
    out: dict[str, dict[str, dict]] = {}
    for t in list_network_topology(db, cluster):
        if t.get("error"):
            continue
        out[t["node"]] = {i["iface"]: i for i in t["interfaces"] if i.get("type") in ("bridge", "OVSBridge") and i.get("active", True)}
    # SDN virtual networks (vnets) are attached like bridges but are defined cluster-wide, not in a host's network config
    try:
        target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
        with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as wc:
            vnets = wc._get("/cluster/sdn/vnets") or []
    except Exception:  # noqa: BLE001 -- no SDN, or the token may not list it: plain bridges only
        vnets = []
    for v in vnets:
        name = v.get("vnet")
        if not name:
            continue
        for node_bridges in out.values():
            node_bridges.setdefault(name, {"iface": name, "type": "sdn", "vlan_aware": True, "allowed_vlans": None, "sdn_zone": v.get("zone")})
    return out


def sdn_path(topo: dict[str, dict[str, dict]], node: str, bridge: str) -> str:
    """The Proxmox permission path to attach a NIC to this bridge or SDN virtual network. Pure."""
    zone = ((topo.get(node) or {}).get(bridge) or {}).get("sdn_zone")
    return f"/sdn/zones/{zone}/{bridge}" if zone else f"/sdn/zones/localnetwork/{bridge}"


def check_bridge(bridge: str, tag: int | None, current_node: str, topo: dict[str, dict[str, dict]], all_nodes: list[str]) -> tuple[list[str], list[str]]:
    """(blocking reasons for the VM's own host, warnings for the other hosts). Pure."""
    blocks: list[str] = []
    warns: dict[str, list[str]] = {}
    for n in all_nodes:
        info = (topo.get(n) or {}).get(bridge)
        if n not in topo:
            warns.setdefault("its network config could not be read: unverified", []).append(n)
            continue
        problem = None
        if info is None:
            problem = f"it has no bridge '{bridge}'"
        elif tag is not None and not info.get("vlan_aware"):
            problem = f"bridge '{bridge}' is not VLAN-aware, so it cannot carry VLAN {tag}"
        elif tag is not None and not _vlan_allowed(tag, info.get("allowed_vlans")):
            problem = f"VLAN {tag} is not in bridge '{bridge}' allowed list ({info.get('allowed_vlans') or 'none'})"
        if problem is None:
            continue
        if n == current_node:
            blocks.append(f"{n} (where the VM lives): {problem}")
        else:
            warns.setdefault(f"the VM could not move there: {problem}", []).append(n)
    lines = [f"{', '.join(ns)}: {why}" for why, ns in warns.items()]
    return blocks, lines


def dry_run_nic_change(db, workload: Workload, *, action: str, net_id: str | None, model: str | None, bridge: str | None, tag: int | None,
                       firewall: bool, mac: str | None, link_down: bool, actor: str, correlation_id=None) -> Operation:
    if workload.type != "vm":
        raise NicError("network device edits are only implemented for VMs (qemu), not containers")
    if action not in ("add", "edit", "remove"):
        raise NicError("action must be 'add', 'edit' or 'remove'")
    node, cluster, target = _load_context(db, workload)
    was_running = workload.status == "running"
    ctx = {"node_id": str(node.id), "node": node.name, "vmid": workload.vmid, "action": action, "net_id": net_id, "was_running": was_running}
    op = create_operation(db, OP_TYPE, cluster_id=cluster.id, node_id=node.id, workload_id=workload.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    old_line = new_line = None
    old_fields: dict = {}
    mac = (mac or "").strip() or None
    topo: dict = {}
    if workload.status not in ("running", "stopped"):
        reasons.append(f"workload status is '{workload.status}': not safe to change its network devices right now")
        blocking.append("SAFE-MIGRATE-001")
    self_vm = str(workload.vmid) == (self_vmid_from_env() or "")
    try:
        client, _cred = build_pve_client(db, target)
        with client:
            config = client.qemu_config(node.name, workload.vmid)
        if config.get("lock"):
            reasons.append(f"Proxmox has the VM locked ('{config.get('lock')}'): another task is working on it")
            blocking.append("SAFE-LOCK-001")
        if action == "add":
            net_id = net_id or next_free_slot(config)
            if not net_id or not NET_RE.match(net_id) or net_id in config:
                reasons.append("no free network device slot" if not net_id else f"'{net_id}' is already in use")
                blocking.append("SAFE-NIC-001")
        else:
            old_line = str(config.get(net_id) or "")
            if not net_id or not NET_RE.match(net_id) or not old_line:
                reasons.append(f"'{net_id}' is not a network device of this VM")
                blocking.append("SAFE-NIC-001")
            else:
                old_fields = net_fields(old_line)
        ctx["net_id"] = net_id
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"could not read {vm_label(workload)} from Proxmox: {str(exc)[:140]}")
        blocking.append("SAFE-STORAGE-002")
        config = {}

    if action == "remove":
        if old_line:
            reasons.append(f"{net_id} ({old_fields.get('model')} on {old_fields.get('bridge')}) is removed from the VM: it loses that network connection" + (" at once if the guest supports hot-unplug, otherwise at its next shutdown and start" if was_running else ""))
            if sum(1 for k in config if NET_RE.match(k)) == 1:
                reasons.append("this is the VM's only network device: it will have no network at all afterwards (the console still works)")
            if self_vm:
                reasons.append("this is the VM PyXie itself runs on: PyXie will not remove its own network device")
                blocking.append("SAFE-NIC-002")
    elif not blocking or "SAFE-NIC-001" not in blocking:
        model = (model or old_fields.get("model") or "virtio").strip()
        if model not in MODELS:
            reasons.append(f"'{model}' is not a supported model: choose one of {', '.join(MODELS)}")
            blocking.append("SAFE-NIC-001")
        bridge = (bridge or old_fields.get("bridge") or "").strip()
        if not bridge or not BRIDGE_RE.match(bridge):
            reasons.append("choose a bridge")
            blocking.append("SAFE-NIC-001")
        if tag is not None and not (1 <= int(tag) <= 4094):
            reasons.append("a VLAN tag is a number from 1 to 4094 (leave it empty for untagged)")
            blocking.append("SAFE-NIC-001")
        if action == "edit" and mac is None:
            mac = old_fields.get("mac")
        if mac:
            problem = valid_mac(mac)
            if problem:
                reasons.append(problem)
                blocking.append("SAFE-NIC-001")
            else:
                dup = (db.query(WorkloadNic, Workload).join(Workload, Workload.id == WorkloadNic.workload_id)
                       .filter(WorkloadNic.mac.ilike(mac), Workload.is_missing.is_(False)).all())
                dup = [w for n, w in dup if not (w.id == workload.id and n.slot == net_id)]
                if dup:
                    reasons.append(f"MAC {mac.upper()} is already used by {', '.join(vm_label(w) for w in dup)}: two devices with one MAC break both networks")
                    blocking.append("SAFE-NIC-001")
        if "SAFE-NIC-001" not in blocking:
            topo = bridges_by_node(db, cluster)
            names = [n.name for n in db.query(Node).filter(Node.cluster_id == cluster.id, Node.is_missing.is_(False)).order_by(Node.name).all()]
            blocks, warns = check_bridge(bridge, tag, node.name, topo, names)
            for b in blocks:
                reasons.append(b)
                blocking.append("SAFE-NIC-001")
            reasons.extend(warns)
            new_fields = {"model": model, "bridge": bridge, "tag": int(tag) if tag is not None else None, "firewall": bool(firewall), "link_down": bool(link_down), "mac": mac}
            new_line = build_net(parse_net(old_line) if old_line else None, model=model, bridge=bridge, tag=new_fields["tag"], firewall=new_fields["firewall"], mac=mac, link_down=new_fields["link_down"])
            ctx["expected"] = new_fields
            if action == "add":
                reasons.append(f"a new {model} network device {net_id} on {bridge}" + (f", VLAN {tag}" if tag is not None else ", untagged") + (f", MAC {mac.upper()}" if mac else ", MAC chosen by Proxmox") + (": hot-plugged into the running VM" if was_running else ""))
            else:
                changes = [f"{k}: {old_fields.get(k) if old_fields.get(k) not in (None, False) else '-'} -> {new_fields[k] if new_fields[k] not in (None, False) else '-'}" for k in ("model", "bridge", "tag", "firewall", "link_down", "mac") if (old_fields.get(k) or None) != (new_fields[k] or None) and not (k in ("firewall", "link_down") and bool(old_fields.get(k)) == bool(new_fields[k]))]
                if not changes:
                    reasons.append("nothing to change: this device already has exactly these settings")
                    blocking.append("SAFE-NIC-001")
                else:
                    reasons.append(f"{net_id}: " + "; ".join(changes))
                if self_vm and any(not c.startswith("firewall") for c in changes):
                    reasons.append("this is the VM PyXie itself runs on: PyXie will not change a network setting that could cut its own connection (only the firewall flag, or adding a device, is allowed)")
                    blocking.append("SAFE-NIC-002")
                if old_fields.get("mac") and mac and old_fields["mac"].lower() != mac.lower():
                    reasons.append("a new MAC address makes the guest see a different network card: DHCP leases, licences and Linux interface names tied to the old one stop matching")
                if was_running and old_fields.get("model") and model != old_fields["model"]:
                    reasons.append("a model change on a running VM waits for its next shutdown and start; bridge, VLAN, firewall and link changes apply at once")
            if firewall:
                reasons.append("the NIC's firewall flag only has an effect where the Proxmox firewall is enabled for this VM or the datacenter; with a default-drop policy and no rules, it cuts the VM off")
            lacking_sdn = missing_privileges(db, target, workload.vmid, ["SDN.Use"], path=sdn_path(topo, node.name, bridge)) if bridge else None
            if lacking_sdn:
                reasons.append(permission_reason(lacking_sdn, f"attach network devices to bridge {bridge}"))
                blocking.append("SAFE-PERMISSION-001")
    lacking = missing_privileges(db, target, workload.vmid, ["VM.Config.Network"])
    if lacking:
        reasons.append(permission_reason(lacking, "change network devices"))
        blocking.append("SAFE-PERMISSION-001")
    ctx.update({"old_line": old_line, "new_line": new_line})
    op.context = ctx
    db.commit()
    result = {"vmid": workload.vmid, "workload_name": workload.name, "node": node.name, "action": action, "net_id": net_id, "old_line": old_line, "new_line": new_line,
              "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": sorted(set(blocking))}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_nic_change(db, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise NicError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    workload = db.query(Workload).filter(Workload.id == op.workload_id).one()
    node, cluster, target = _load_context(db, workload)
    ctx = dict(op.context or {})
    action, net_id, new_line, old_line = ctx["action"], ctx["net_id"], ctx.get("new_line"), ctx.get("old_line")
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            rc, _cred = build_pve_client(db, target)
            with rc:
                config = rc.qemu_config(node.name, workload.vmid)
            live = str(config.get(net_id) or "")
            if config.get("lock"):
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error="revalidation failed: the VM is locked by Proxmox; nothing was changed")
            if (action == "add" and live) or (action != "add" and live != (old_line or "")):
                release_locks_for_operation(db, op.id)
                return block_operation(db, op, blocking_safety_rules=["SAFE-NIC-001"], actor="system")  # changed since the preview: a human should look
            try:
                acquire_lock(db, resource_type="workload", resource_id=workload.id, operation_id=op.id, reason=OP_TYPE)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")
        if op.status == "executing":
            with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as wc:
                if action == "remove":
                    wc.delete_vm_net(node.name, workload.vmid, net_id)
                else:
                    wc.set_vm_net(node.name, workload.vmid, net_id, new_line)
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            rc, _cred = build_pve_client(db, target)
            with rc:
                config = rc.qemu_config(node.name, workload.vmid)
                pending = rc.qemu_pending(node.name, workload.vmid)
            problems = []
            got = pending_value(pending, config, net_id)
            if action == "remove":
                marked = any(r.get("key") == net_id and r.get("delete") for r in pending or [])
                if net_id in config and not marked:
                    problems.append(f"{net_id} is still present")
            else:
                f, want = net_fields(got), ctx.get("expected") or {}
                if not got:
                    problems.append(f"{net_id} is not present after the change")
                else:
                    for k in ("model", "bridge", "tag", "firewall", "link_down"):
                        if f.get(k) != want.get(k) and (f.get(k) or None) != (want.get(k) or None):
                            problems.append(f"{k} is {f.get(k)}, expected {want.get(k)}")
                    if want.get("mac") and (f.get("mac") or "").lower() != want["mac"].lower():
                        problems.append(f"MAC is {f.get('mac')}, expected {want['mac']}")
            op.verification_result = {"net_id": net_id, "line": got, "removed": action == "remove"}
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
