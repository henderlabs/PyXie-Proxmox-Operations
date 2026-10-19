"""Containers: create one (workload.ct_create), and the container-template library (storage.ct_template_download from Proxmox's official appliance list,
storage.ct_template_delete). Same Safety Contract as the VM operations: dry-run against the real cluster -> approve -> revalidate -> lock -> Proxmox task -> read back -> audit.
Containers are created unprivileged (see ct_create.py)."""

from .credentials import load_pve_credentials
from .ct_create import TEMPLATE_RE, build_ct_params, summarize, validate_ct_spec
from .discovery import build_pve_client
from .iso_workflow import check_space, _lock_id
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Node, Operation, PlacementPinRule, PveTarget, Workload
from .nic_workflow import bridges_by_node, check_bridge
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_permissions import friendly_error, missing_privileges, permission_reason
from .node_ready import node_not_ready
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient, TaskTimeoutError
from .storage_move_workflow import check_target
from . import rollback

OP_CREATE, OP_TPL_DOWNLOAD, OP_TPL_DELETE = "workload.ct_create", "storage.ct_template_download", "storage.ct_template_delete"
GIB = 1 << 30


class CtError(Exception):
    pass


def _target(db, cluster: Cluster) -> PveTarget:
    return db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()


def _online_nodes(db, cluster: Cluster) -> list[Node]:
    return db.query(Node).filter(Node.cluster_id == cluster.id, Node.is_missing.is_(False), Node.status == "online").order_by(Node.name).all()


def ct_options(db, cluster: Cluster) -> dict:
    """What the container wizard offers, read live from Proxmox: hosts (state, memory), their root-disk storages, installed templates, bridges, pools, tag pins, next ID."""
    target = _target(db, cluster)
    out: dict = {"nodes": [], "templates": [], "bridges": [], "pools": [], "pins": [], "next_vmid": None, "errors": []}
    nodes = db.query(Node).filter(Node.cluster_id == cluster.id, Node.is_missing.is_(False)).order_by(Node.name).all()
    seen: dict[str, dict] = {}
    client, _cred = build_pve_client(db, target)
    with client:
        for n in nodes:
            row = {"name": n.name, "online": n.status == "online", "maintenance": bool(n.maintenance_mode), "mem_total": None, "mem_used": None, "storages": [], "template_storages": []}
            try:
                st = client.node_status(n.name) or {}
                row["mem_total"], row["mem_used"] = (st.get("memory") or {}).get("total"), (st.get("memory") or {}).get("used")
                for r in client.storage_list(n.name) or []:
                    if not r.get("active", 1):
                        continue
                    content = str(r.get("content") or "")
                    if "rootdir" in content:
                        row["storages"].append({"name": r["storage"], "type": r.get("type"), "avail": r.get("avail"), "shared": bool(r.get("shared"))})
                    if "vztmpl" in content:
                        row["template_storages"].append({"name": r["storage"], "type": r.get("type"), "avail": r.get("avail")})
                        for c in client.storage_content(n.name, r["storage"], "vztmpl") or []:
                            e = seen.setdefault(c["volid"], {"volid": c["volid"], "size": c.get("size"), "nodes": []})
                            e["nodes"].append(n.name)
            except Exception as exc:  # noqa: BLE001
                out["errors"].append(f"{n.name}: {str(exc)[:120]}")
            out["nodes"].append(row)
        out["templates"] = sorted(seen.values(), key=lambda t: t["volid"].lower())
        try:
            out["pools"] = sorted(p.get("poolid") for p in client.pools() if p.get("poolid"))
            out["next_vmid"] = int(client.cluster_next_id())
        except Exception:  # noqa: BLE001
            pass
    topo = bridges_by_node(db, cluster)
    out["bridges"] = [{"name": b, "vlan_aware": all(bool(topo[n][b].get("vlan_aware")) for n in topo if b in topo[n]), "missing_on": [n for n in sorted(topo) if b not in topo[n]]}
                      for b in sorted({b for t in topo.values() for b in t})]
    by_id = {str(n.id): n.name for n in nodes}
    out["pins"] = [{"tag": r.tag, "strict": bool(r.strict), "nodes": [by_id.get(str(i), "?") for i in (r.node_ids or [])]}
                   for r in db.query(PlacementPinRule).filter(PlacementPinRule.scope_type == "tag_group").all() if r.tag]
    return out


def dry_run_ct_create(db, cluster: Cluster, *, spec_in: dict, actor: str, correlation_id=None) -> Operation:
    spec, errors, notes = validate_ct_spec(spec_in)
    target = _target(db, cluster)
    ctx = {"cluster": cluster.name, "node": spec["node"], "name": spec["hostname"], "spec": spec}
    op = create_operation(db, OP_CREATE, cluster_id=cluster.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = list(errors)
    blocking = ["SAFE-CREATE-001"] if errors else []
    params = None
    vmid = spec["vmid"]
    node_row = db.query(Node).filter(Node.cluster_id == cluster.id, Node.name == spec["node"], Node.is_missing.is_(False)).first()
    if spec["node"] and (node_row is None or node_row.status != "online" or node_row.maintenance_mode):
        reasons.append(f"{spec['node']} is not an online host outside maintenance")
        blocking.append("SAFE-CREATE-001")
    if not errors and node_row is not None and "SAFE-CREATE-001" not in blocking:
        try:
            client, _cred = build_pve_client(db, target)
            with client:
                res = client.cluster_resources("vm") or []
                used = {int(r["vmid"]) for r in res if r.get("vmid") is not None}
                if vmid is None:
                    vmid = int(client.cluster_next_id())
                elif vmid in used:
                    reasons.append(f"ID {vmid} is already used in this cluster")
                    blocking.append("SAFE-CREATE-001")
                rows = client.storage_list(spec["node"]) or []
                for p in check_target([dict(r, content=str(r.get("content") or "").replace("rootdir", "images")) for r in rows], spec["disk_storage"], spec["disk_size_gb"] * GIB):
                    reasons.append(f"root disk: {p.replace('hold VM disks', 'hold container disks')}")
                    blocking.append("SAFE-STORAGE-002")
                tstore = spec["template"].split(":", 1)[0]
                if spec["template"] not in {c.get("volid") for c in client.storage_content(spec["node"], tstore, "vztmpl") or []}:
                    reasons.append(f"the template {spec['template']} is not on {spec['node']}'s storage '{tstore}'")
                    blocking.append("SAFE-CREATE-001")
                st = client.node_status(spec["node"]) or {}
                free = ((st.get("memory") or {}).get("total") or 0) - ((st.get("memory") or {}).get("used") or 0)
                need = spec["memory_mb"] << 20
                if free and need > free:
                    reasons.append(f"{spec['node']} has {free / GIB:.1f} GiB of memory free and the container asks for {need / GIB:.1f} GiB")
                    blocking.append("SAFE-CREATE-001")
                if spec["pool"] and spec["pool"] not in {p.get("poolid") for p in client.pools() or []}:
                    reasons.append(f"the pool '{spec['pool']}' does not exist in Proxmox")
                    blocking.append("SAFE-CREATE-001")
            names = [n.name for n in db.query(Node).filter(Node.cluster_id == cluster.id, Node.is_missing.is_(False)).all()]
            blocks, warns = check_bridge(spec["nic_bridge"], spec["nic_vlan"], spec["node"], bridges_by_node(db, cluster), names)
            for b in blocks:
                reasons.append(b)
                blocking.append("SAFE-NIC-001")
            reasons += warns
            for rule in db.query(PlacementPinRule).filter(PlacementPinRule.scope_type == "tag_group").all():
                if rule.tag in spec["tags"] and str(node_row.id) not in {str(i) for i in (rule.node_ids or [])}:
                    names_ = [n.name for n in db.query(Node).filter(Node.id.in_(list(rule.node_ids or []))).all()]
                    if rule.strict:
                        reasons.append(f"tag '{rule.tag}' is hard-pinned to {', '.join(names_)}: not allowed on {node_row.name}")
                        blocking.append("SAFE-CREATE-001")
                    else:
                        reasons.append(f"tag '{rule.tag}' prefers {', '.join(names_)}; Balance Load will move the container there")
            lacking = missing_privileges(db, target, vmid, ["VM.Allocate"], path=f"/vms/{vmid}")
            if lacking:
                reasons.append(permission_reason(lacking, "create containers"))
                blocking.append("SAFE-PERMISSION-001")
            if vmid is not None:
                params = build_ct_params(spec, vmid)
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"could not check the cluster: {str(exc)[:160]}")
            blocking.append("SAFE-STORAGE-002")
    reasons += notes
    if not blocking and params:
        reasons.insert(0, f"creates container {vmid} on {spec['node']}: {summarize(spec)}" + ("; started afterwards" if spec["start_after"] else "; left powered off"))
    ctx.update({"vmid": vmid, "params": params})
    op.context = ctx
    db.commit()
    result = {"cluster": cluster.name, "node": spec["node"], "vmid": vmid, "workload_name": spec["hostname"], "name": spec["hostname"], "eligible": not blocking, "reasons": reasons,
              "blocking_safety_rules": sorted(set(blocking))}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def appliance_list(db, cluster: Cluster, node: str) -> list[dict]:
    """The official container templates Proxmox offers to download (aplinfo), newest name first."""
    target = _target(db, cluster)
    with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as wc:
        rows = wc._get(f"/nodes/{node}/aplinfo") or []
    keep = [{"template": r.get("template"), "package": r.get("package"), "os": r.get("os"), "version": r.get("version"), "headline": r.get("headline"), "section": r.get("section")}
            for r in rows if r.get("type") == "lxc" and r.get("template") and (r.get("architecture") in (None, "", "amd64"))]
    return sorted(keep, key=lambda r: (r["section"] or "", r["package"] or "", r["template"]), reverse=False)


def dry_run_template_download(db, cluster: Cluster, *, node: str, storage: str, template: str, actor: str, correlation_id=None) -> Operation:
    target = _target(db, cluster)
    ctx = {"cluster": cluster.name, "node": node, "storage": storage, "template": template}
    op = create_operation(db, OP_TPL_DOWNLOAD, cluster_id=cluster.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    try:
        client, _cred = build_pve_client(db, target)
        with client:
            row = next((r for r in client.storage_list(node) or [] if r.get("storage") == storage), None)
            if row is None or not row.get("active", 1):
                reasons.append(f"storage '{storage}' is not active on {node}")
                blocking.append("SAFE-ISO-001")
            elif "vztmpl" not in str(row.get("content") or ""):
                reasons.append(f"storage '{storage}' is not set up to hold container templates (content type 'vztmpl')")
                blocking.append("SAFE-ISO-001")
            else:
                if any(c.get("volid") == f"{storage}:vztmpl/{template}" for c in client.storage_content(node, storage, "vztmpl") or []):
                    reasons.append(f"{storage} already has {template}")
                    blocking.append("SAFE-ISO-001")
                for p in check_space(row, 600 << 20):
                    reasons.append(p)
                    blocking.append("SAFE-STORAGE-002")
        known = {a["template"] for a in appliance_list(db, cluster, node)}
        if template not in known:
            reasons.append(f"'{template}' is not in Proxmox's list of official templates")
            blocking.append("SAFE-ISO-001")
        lacking = missing_privileges(db, target, 0, ["Datastore.AllocateTemplate"], path=f"/storage/{storage}")
        if lacking:
            reasons.append(permission_reason(lacking, f"add container templates to {storage}"))
            blocking.append("SAFE-PERMISSION-001")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"could not check: {str(exc)[:160]}")
        blocking.append("SAFE-STORAGE-002")
    if not blocking:
        reasons.insert(0, f"{node} downloads the official template {template} into {storage}; Proxmox checks its published checksum")
    result = {"cluster": cluster.name, "node": node, "storage": storage, "template": template, "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": sorted(set(blocking)), "workload_name": None}
    op.context = ctx
    db.commit()
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def dry_run_template_delete(db, cluster: Cluster, *, node: str, volid: str, actor: str, correlation_id=None) -> Operation:
    target = _target(db, cluster)
    m = TEMPLATE_RE.match(volid or "")
    ctx = {"cluster": cluster.name, "node": node, "volid": volid}
    op = create_operation(db, OP_TPL_DELETE, cluster_id=cluster.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    if not m:
        reasons.append("that is not a container template volume")
        blocking.append("SAFE-ISO-001")
    else:
        try:
            client, _cred = build_pve_client(db, target)
            with client:
                have = {c.get("volid") for c in client.storage_content(node, m.group(1), "vztmpl") or []}
            if volid not in have:
                reasons.append(f"{volid} is not on {node}'s storage (anymore)")
                blocking.append("SAFE-ISO-001")
            else:
                reasons.append(f"{m.group(2)} is deleted from {m.group(1)}. Containers already made from it keep working; download it again to use it for new ones.")
            lacking = missing_privileges(db, target, 0, ["Datastore.AllocateTemplate"], path=f"/storage/{m.group(1)}")
            if lacking:
                reasons.append(permission_reason(lacking, f"delete container templates from {m.group(1)}"))
                blocking.append("SAFE-PERMISSION-001")
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"could not check: {str(exc)[:160]}")
            blocking.append("SAFE-STORAGE-002")
    result = {"cluster": cluster.name, "node": node, "volid": volid, "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": sorted(set(blocking)), "workload_name": None}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_ct_op(db, operation_id) -> Operation:
    """One executor for the three container operation types. Resumable (a running Proxmox task is picked up again by its UPID)."""
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise CtError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "creating", "executing", "starting_up", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    cluster = db.query(Cluster).filter(Cluster.id == op.cluster_id).one()
    target = _target(db, cluster)
    ctx = dict(op.context or {})
    kind = op.operation_type_id
    node = ctx.get("node")
    try:
        creds = load_pve_credentials(db, target, "maintenance")
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            if kind == OP_CREATE:
                rc, _cred = build_pve_client(db, target)
                with rc:
                    taken = {int(r["vmid"]) for r in rc.cluster_resources("vm") or [] if r.get("vmid") is not None}
                if int(ctx["vmid"]) in taken:
                    release_locks_for_operation(db, op.id)
                    return fail_operation(db, op, error=f"ID {ctx['vmid']} was taken since the preview; nothing was created. Preview again")
                why = node_not_ready(db, cluster.id, node)
                if why:
                    release_locks_for_operation(db, op.id)
                    return fail_operation(db, op, error=f"{why}; nothing was created. Preview again to choose another host")
                lock = (cluster.id, "cluster")
            else:
                store = ctx["storage"] if kind == OP_TPL_DOWNLOAD else ctx["volid"].split(":", 1)[0]
                lock = (_lock_id(cluster, store), "storage")
            try:
                acquire_lock(db, resource_type=lock[1], resource_id=lock[0], operation_id=op.id, reason=kind)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="creating" if kind == OP_CREATE else "executing", stage="creating" if kind == OP_CREATE else "executing")
        if op.status in ("creating", "executing"):
            with PveMaintenanceClient(creds) as wc:
                if not op.pve_upid:
                    if kind == OP_CREATE:
                        op.pve_upid = wc.create_ct(node, ctx["params"])
                    elif kind == OP_TPL_DOWNLOAD:
                        op.pve_upid = wc.download_appliance(node, ctx["storage"], ctx["template"])
                    else:
                        op.pve_upid = wc.delete_volume(node, ctx["volid"].split(":", 1)[0], ctx["volid"])
                    db.commit()
                res = wc.wait_for_task(node, op.pve_upid, timeout=3600 if kind != OP_TPL_DELETE else 600)
            op.pve_upid = None
            db.commit()
            if not res.finished_ok:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"Proxmox reported the task did not succeed: {res.exit_status or res.raw}")
            nxt = "starting_up" if kind == OP_CREATE and ctx["spec"]["start_after"] else "verifying"
            op = enter_stage(db, op, status=nxt, stage=nxt)
        if op.status == "starting_up":
            if not op.pve_upid:
                with PveMaintenanceClient(creds).for_guest("lxc") as wc:
                    op.pve_upid = wc.start_vm(node, int(ctx["vmid"]))
                db.commit()
            with PveMaintenanceClient(creds) as wc:
                res = wc.wait_for_task(node, op.pve_upid, timeout=300)
            op.pve_upid = None
            db.commit()
            if not res.finished_ok:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"the container {ctx['vmid']} was created but did not start: {res.exit_status or res.raw}")
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            rc, _cred = build_pve_client(db, target)
            problems: list[str] = []
            with rc:
                if kind == OP_CREATE:
                    cfg = rc.lxc_config(node, int(ctx["vmid"]))
                    sp = ctx["spec"]
                    for k, want in (("hostname", sp["hostname"]), ("cores", str(sp["cores"])), ("memory", str(sp["memory_mb"]))):
                        if str(cfg.get(k)) != want:
                            problems.append(f"{k} is {cfg.get(k)}, expected {want}")
                    if not str(cfg.get("rootfs", "")).startswith(sp["disk_storage"] + ":"):
                        problems.append("the root disk is not on the chosen storage")
                    if f"bridge={sp['nic_bridge']}" not in str(cfg.get("net0", "")):
                        problems.append("net0 is not on the chosen bridge")
                    if str(cfg.get("unprivileged")) != "1":
                        problems.append("the container is not unprivileged")
                    op.verification_result = {"vmid": ctx["vmid"], "node": node, "problems": problems}
                else:
                    store = ctx["storage"] if kind == OP_TPL_DOWNLOAD else ctx["volid"].split(":", 1)[0]
                    volid = f"{store}:vztmpl/{ctx['template']}" if kind == OP_TPL_DOWNLOAD else ctx["volid"]
                    have = {c.get("volid") for c in rc.storage_content(node, store, "vztmpl") or []}
                    if (volid in have) != (kind == OP_TPL_DOWNLOAD):
                        problems.append("Proxmox's storage listing does not match what was expected")
                    op.verification_result = {"volid": volid, "present": volid in have}
            db.commit()
            release_locks_for_operation(db, op.id)
            if problems:
                return fail_operation(db, op, error="read-back did not match: " + "; ".join(problems))
            if kind == OP_CREATE:
                try:
                    from .discovery import run_discovery
                    run_discovery(db, target, actor="operation")
                except Exception:  # noqa: BLE001
                    pass
                wl = db.query(Workload).filter(Workload.cluster_id == cluster.id, Workload.vmid == int(ctx["vmid"])).first()
                if wl is not None:
                    op.workload_id = wl.id
                    op.context = {**(op.context or {}), "workload_id": str(wl.id)}
                    db.commit()
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.IRREVERSIBLE if kind == OP_TPL_DELETE else rollback.MANUAL_REVERSIBLE)
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
