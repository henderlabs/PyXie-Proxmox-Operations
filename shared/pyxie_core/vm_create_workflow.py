"""Create a VM (workload.create): the wizard's choices -> a new Proxmox VM, through the same Safety Contract as every other operation.

dry-run (everything is checked against the real cluster: VM ID free, host online and not in maintenance, room for the memory and the disks, the ISO
exists, the bridge and VLAN exist on the host, CPU and machine type run on it, tag pins respected, the admin token holds the privileges) -> approve ->
revalidate -> lock -> create (one Proxmox task) -> optionally start -> read the new VM's config back and compare -> pick the VM up in the inventory -> audit.
PyXie never deletes anything it did not create in this operation, and does not delete on failure: a half-made VM is reported so a person decides.
"""

from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .hw_compat import check_cpu_type, check_machine
from .hw_edit_workflow import read_hosts
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Node, Operation, PlacementPinRule, PveTarget, Workload
from .nic_workflow import bridges_by_node, check_bridge
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_permissions import friendly_error, missing_privileges, permission_reason
from .node_ready import node_not_ready
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient, TaskTimeoutError
from .storage_move_workflow import check_target
from .vm_create import build_params, summarize, validate_spec
from . import rollback

OP_TYPE = "workload.create"
GIB = 1 << 30


class VmCreateError(Exception):
    pass


def _target(db, cluster: Cluster) -> PveTarget:
    return db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()


def _image_storages(rows: list[dict]) -> list[dict]:
    return [{"name": r["storage"], "type": r.get("type"), "avail": r.get("avail"), "total": r.get("total"), "shared": bool(r.get("shared"))}
            for r in rows if r.get("active", 1) and "images" in str(r.get("content") or "")]


def vm_create_options(db, cluster: Cluster) -> dict:
    """Everything the wizard offers for this cluster, read live from Proxmox: hosts (state, free memory), their storages, ISOs, bridges, CPU and machine
    types, pools, tag pin rules and the next free VM ID. A part that could not be read is empty and listed in `errors`."""
    target = _target(db, cluster)
    out: dict = {"nodes": [], "isos": [], "bridges": [], "cpus": [], "machines": [], "pools": [], "pins": [], "next_vmid": None, "errors": []}
    nodes = db.query(Node).filter(Node.cluster_id == cluster.id, Node.is_missing.is_(False)).order_by(Node.name).all()
    client, _cred = build_pve_client(db, target)
    with client:
        seen_iso: dict[str, dict] = {}
        for n in nodes:
            row = {"name": n.name, "online": n.status == "online", "maintenance": bool(n.maintenance_mode), "mem_total": None, "mem_used": None, "cpu_model": None, "storages": []}
            try:
                st = client.node_status(n.name) or {}
                row["mem_total"], row["mem_used"] = (st.get("memory") or {}).get("total"), (st.get("memory") or {}).get("used")
                row["cpu_model"] = (st.get("cpuinfo") or {}).get("model")
                rows = client.storage_list(n.name) or []
                row["storages"] = _image_storages(rows)
                for r in rows:
                    if r.get("active", 1) and "iso" in str(r.get("content") or ""):
                        for c in client.storage_content(n.name, r["storage"], "iso") or []:
                            e = seen_iso.setdefault(c["volid"], {"volid": c["volid"], "size": c.get("size"), "storage": r["storage"], "nodes": [], "shared": bool(r.get("shared"))})
                            e["nodes"].append(n.name)
            except Exception as exc:  # noqa: BLE001
                out["errors"].append(f"{n.name}: {str(exc)[:120]}")
            out["nodes"].append(row)
        out["isos"] = sorted(seen_iso.values(), key=lambda e: e["volid"].lower())
        try:
            hosts = read_hosts(db, cluster, client, "")
            out["machines"] = sorted({m for h in hosts for m in (h["machines"] or [])})
            vendor = {k: v for h in hosts for k, v in (h["models"] or {}).items()}
            out["cpus"] = [{"name": k, "vendor": vendor.get(k), "missing_on": [h["name"] for h in hosts if h["models"] is not None and k not in h["models"]]}
                           for k in sorted({k for h in hosts for k in (h["models"] or {})}, key=str.lower)]
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"CPU/machine types: {str(exc)[:120]}")
        try:
            out["pools"] = sorted(p.get("poolid") for p in client.pools() if p.get("poolid"))
        except Exception:  # noqa: BLE001
            pass
        try:
            out["next_vmid"] = int(client.cluster_next_id())
        except Exception:  # noqa: BLE001
            pass
    topo = bridges_by_node(db, cluster)
    names = sorted({b for t in topo.values() for b in t})
    out["bridges"] = [{"name": b, "vlan_aware": all(bool(topo[n][b].get("vlan_aware")) for n in topo if b in topo[n]), "missing_on": [n for n in sorted(topo) if b not in topo[n]]} for b in names]
    by_id = {str(n.id): n.name for n in nodes}
    out["pins"] = [{"tag": r.tag, "strict": bool(r.strict), "nodes": [by_id.get(str(i), "?") for i in (r.node_ids or [])]}
                   for r in db.query(PlacementPinRule).filter(PlacementPinRule.scope_type == "tag_group").all() if r.tag]
    return out


def dry_run_vm_create(db, cluster: Cluster, *, spec_in: dict, actor: str, correlation_id=None) -> Operation:
    spec, errors, notes = validate_spec(spec_in)
    target = _target(db, cluster)
    ctx = {"cluster": cluster.name, "node": spec["node"], "name": spec["name"], "spec": spec}
    op = create_operation(db, OP_TYPE, cluster_id=cluster.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = list(errors)
    blocking: list[str] = ["SAFE-CREATE-001"] if errors else []
    params: dict | None = None
    vmid = spec["vmid"]
    node_row = db.query(Node).filter(Node.cluster_id == cluster.id, Node.name == spec["node"], Node.is_missing.is_(False)).first()
    if spec["node"] and node_row is None:
        reasons.append(f"'{spec['node']}' is not a host of cluster {cluster.name}")
        blocking.append("SAFE-CREATE-001")
    elif node_row is not None and (node_row.status != "online" or node_row.maintenance_mode):
        reasons.append(f"{node_row.name} is {'in maintenance mode' if node_row.maintenance_mode else node_row.status}: pick a host that is online")
        blocking.append("SAFE-CREATE-001")
    if not errors and node_row is not None:
        try:
            client, _cred = build_pve_client(db, target)
            with client:
                res = client.cluster_resources("vm") or []
                used_ids = {int(r["vmid"]) for r in res if r.get("vmid") is not None}
                same_name = [r for r in res if r.get("name") == spec["name"]]
                if vmid is None:
                    vmid = int(client.cluster_next_id())
                elif vmid in used_ids:
                    reasons.append(f"VM ID {vmid} is already used in this cluster")
                    blocking.append("SAFE-CREATE-001")
                if same_name:
                    reasons.append(f"a VM named '{spec['name']}' already exists (VM {same_name[0].get('vmid')}): allowed, but confusing")
                rows = client.storage_list(spec["node"]) or []
                st = client.node_status(spec["node"]) or {}
                free_mem = ((st.get("memory") or {}).get("total") or 0) - ((st.get("memory") or {}).get("used") or 0)
                for what, name, need in (("disk", spec["disk_storage"], spec["disk_size_gb"] * GIB), ("EFI disk", spec["efi_storage"], 4 << 20), ("TPM state disk", spec["tpm_storage"], 4 << 20)):
                    if name:
                        for problem in check_target(rows, name, need):
                            reasons.append(f"{what}: {problem}")
                            blocking.append("SAFE-STORAGE-002")
                if spec["iso"]:
                    iso_storage = spec["iso"].split(":", 1)[0]
                    present = {c.get("volid") for c in client.storage_content(spec["node"], iso_storage, "iso") or []}
                    if spec["iso"] not in present:
                        reasons.append(f"the ISO {spec['iso']} is not on {spec['node']}'s storage '{iso_storage}'")
                        blocking.append("SAFE-CREATE-001")
                need_mem = spec["memory_mb"] << 20
                if free_mem and need_mem > free_mem:
                    reasons.append(f"{spec['node']} has {free_mem / GIB:.1f} GiB of memory free and the VM asks for {need_mem / GIB:.1f} GiB: it would not start there")
                    blocking.append("SAFE-CREATE-001")
                elif free_mem and need_mem > free_mem * 0.8:
                    reasons.append(f"the VM takes over 80% of the memory {spec['node']} has free ({free_mem / GIB:.1f} GiB): little room is left for anything else")
                if spec["pool"] and spec["pool"] not in {p.get("poolid") for p in client.pools() or []}:
                    reasons.append(f"the pool '{spec['pool']}' does not exist in Proxmox")
                    blocking.append("SAFE-CREATE-001")
                hosts = read_hosts(db, cluster, client, spec["node"])
            blocks, warns = check_bridge(spec["nic_bridge"], spec["nic_vlan"], spec["node"], bridges_by_node(db, cluster), [n.name for n in db.query(Node).filter(Node.cluster_id == cluster.id, Node.is_missing.is_(False)).all()])
            for b in blocks:
                reasons.append(b)
                blocking.append("SAFE-NIC-001")
            reasons += warns
            for kind, rows_ in (("CPU type", check_cpu_type(spec["cpu_type"], hosts)), ("machine type", check_machine(spec["machine"] or "", hosts) if spec["machine"] else [])):
                grouped: dict[str, list[str]] = {}
                for r in rows_:
                    if r["level"] == "block":
                        reasons.append(f"{kind} cannot run on {r['node']}, where the VM would live: {r['reason']}")
                        blocking.append("SAFE-HW-001")
                    elif r["level"] == "warn":
                        grouped.setdefault(r["reason"], []).append(r["node"])
                for why, names in grouped.items():
                    reasons.append(f"{'all hosts' if len(names) == len(rows_) else ', '.join(names)}: {why}")
            for rule in db.query(PlacementPinRule).filter(PlacementPinRule.scope_type == "tag_group").all():
                if rule.tag in spec["tags"]:
                    allowed = {str(i) for i in (rule.node_ids or [])}
                    if str(node_row.id) not in allowed:
                        names = [n.name for n in db.query(Node).filter(Node.id.in_(list(allowed))).all()] if allowed else []
                        if rule.strict:
                            reasons.append(f"tag '{rule.tag}' is hard-pinned to {', '.join(names)}: the VM cannot be created on {node_row.name}")
                            blocking.append("SAFE-CREATE-001")
                        else:
                            reasons.append(f"tag '{rule.tag}' prefers {', '.join(names)}; Balance Load will move the VM there")
            lacking = missing_privileges(db, target, vmid, ["VM.Allocate"], path=f"/vms/{vmid}")
            if lacking:
                reasons.append(permission_reason(lacking, "create VMs"))
                blocking.append("SAFE-PERMISSION-001")
            if spec["pool"]:
                lp = missing_privileges(db, target, vmid, ["Pool.Allocate"], path=f"/pool/{spec['pool']}")
                if lp:
                    reasons.append(permission_reason(lp, f"add VMs to the pool {spec['pool']}"))
                    blocking.append("SAFE-PERMISSION-001")
            if vmid is not None:
                params = build_params(spec, vmid)
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"could not check the cluster: {str(exc)[:160]}")
            blocking.append("SAFE-STORAGE-002")
    reasons += notes
    if not blocking and params:
        reasons.insert(0, f"creates VM {vmid} on {spec['node']}: {summarize(spec)}" + ("; started afterwards" if spec["start_after"] else "; left powered off"))
    ctx.update({"vmid": vmid, "params": params})
    op.context = ctx
    db.commit()
    result = {"cluster": cluster.name, "node": spec["node"], "vmid": vmid, "workload_name": spec["name"], "name": spec["name"], "summary": summarize(spec) if not errors else None,
              "proxmox_call": params, "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": sorted(set(blocking))}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_vm_create(db, operation_id) -> Operation:
    """Resumable: a running Proxmox task is picked up again by its UPID."""
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise VmCreateError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "creating", "starting_up", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    cluster = db.query(Cluster).filter(Cluster.id == op.cluster_id).one()
    target = _target(db, cluster)
    ctx = dict(op.context or {})
    spec, vmid, params, node = ctx["spec"], int(ctx["vmid"]), ctx["params"], ctx["node"]
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            rc, _cred = build_pve_client(db, target)
            with rc:
                taken = {int(r["vmid"]) for r in rc.cluster_resources("vm") or [] if r.get("vmid") is not None}
            if vmid in taken:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"VM ID {vmid} was taken since the preview; nothing was created. Preview again to get a new one")
            why = node_not_ready(db, cluster.id, node)
            if why:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"{why}; nothing was created. Preview again to choose another host")
            try:
                acquire_lock(db, resource_type="cluster", resource_id=cluster.id, operation_id=op.id, reason=OP_TYPE)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="creating", stage="creating")
        creds = load_pve_credentials(db, target, "maintenance")
        if op.status == "creating":
            if not op.pve_upid:
                with PveMaintenanceClient(creds) as wc:
                    op.pve_upid = wc.create_vm(node, params)
                db.commit()
            with PveMaintenanceClient(creds) as wc:
                res = wc.wait_for_task(node, op.pve_upid, timeout=900)
            op.pve_upid = None
            db.commit()
            if not res.finished_ok:
                release_locks_for_operation(db, op.id)
                rc, _cred = build_pve_client(db, target)
                with rc:
                    exists = any(int(r.get("vmid", -1)) == vmid for r in rc.cluster_resources("vm") or [])
                return fail_operation(db, op, error=f"Proxmox reported the creation did not succeed: {res.exit_status or res.raw}." + (f" A partly created VM {vmid} exists on {node}: check it in Proxmox and remove it there if it is not wanted." if exists else " No VM was left behind."))
            nxt = "starting_up" if spec["start_after"] else "verifying"
            op = enter_stage(db, op, status=nxt, stage=nxt)
        if op.status == "starting_up":
            if not op.pve_upid:
                with PveMaintenanceClient(creds) as wc:
                    op.pve_upid = wc.start_vm(node, vmid)
                db.commit()
            with PveMaintenanceClient(creds) as wc:
                res = wc.wait_for_task(node, op.pve_upid, timeout=300)
            op.pve_upid = None
            db.commit()
            if not res.finished_ok:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"VM {vmid} was created but did not start: {res.exit_status or res.raw}. It is left as it is in Proxmox")
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            rc, _cred = build_pve_client(db, target)
            with rc:
                cfg = rc.qemu_config(node, vmid)
                live = next((v for v in rc.qemu_list(node) if int(v.get("vmid", -1)) == vmid), None)
            problems = []
            for key, expect in (("name", spec["name"]), ("cores", str(spec["cores"])), ("memory", str(spec["memory_mb"])), ("sockets", str(spec["sockets"]))):
                if str(cfg.get(key)) != expect:
                    problems.append(f"{key} is {cfg.get(key)}, expected {expect}")
            if str(cfg.get(f"{spec['disk_bus']}0", "")).split(":", 1)[0] != spec["disk_storage"]:
                problems.append(f"the disk is not on {spec['disk_storage']}")
            if f"bridge={spec['nic_bridge']}" not in str(cfg.get("net0", "")):
                problems.append(f"net0 is not on {spec['nic_bridge']}")
            if live is None:
                problems.append("the VM is not listed by Proxmox")
            op.verification_result = {"vmid": vmid, "node": node, "status": (live or {}).get("status"), "net0": cfg.get("net0"), "problems": problems}
            db.commit()
            release_locks_for_operation(db, op.id)
            if problems:
                return fail_operation(db, op, error="read-back after creation did not match: " + "; ".join(problems) + f". VM {vmid} exists in Proxmox")
            try:
                from .discovery import run_discovery
                run_discovery(db, target, actor="operation")
            except Exception:  # noqa: BLE001
                pass
            wl = db.query(Workload).filter(Workload.cluster_id == cluster.id, Workload.vmid == vmid).first()
            if wl is not None:
                op.workload_id = wl.id
                op.context = {**(op.context or {}), "workload_id": str(wl.id)}
                db.commit()
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE)
        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")
    except TaskTimeoutError as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"{exc}. The task may still finish on the host: look at the Tasks panel and the guest list before trying again. PyXie did not verify the result.")
    except MutationsDisabledError as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=friendly_error(exc) if "Permission check failed" in str(exc) else f"unexpected error: {exc}")
