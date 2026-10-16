"""Clone a VM or a template (workload.clone): a full copy, or a linked clone of a template, on any online host, with a new ID and name.

dry-run (the source, the new ID and name, the target host, room for the disks on the target storage, Proxmox's own clone feature check, privileges) -> approve ->
revalidate -> lock -> clone (one Proxmox task) -> set the tags -> optionally start -> read the clone back -> inventory -> audit. A linked clone shares disks with its
template, so it is only offered for templates, and the preview says the template then cannot be deleted while the clone exists.
"""

from .credentials import load_pve_credentials
from .delete_workflow import owned_disks
from .discovery import build_pve_client
from .hw_edit_workflow import parse_size_bytes
from .labels import vm_label
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Node, Operation, PveTarget, Workload
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_permissions import friendly_error, missing_privileges, permission_reason
from .node_ready import node_not_ready
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient, TaskTimeoutError
from .storage_move_workflow import check_target
from .tags_workflow import clean_tags, split_tags
from .vm_create import VM_NAME_RE
from . import guest_kind as gk
from . import rollback

OP_TYPE = "workload.clone"
GIB = 1 << 30


class CloneError(Exception):
    pass


def dry_run_clone(db, source: Workload, *, name: str, newid: int | None, target_node: str | None, full: bool, storage: str | None, pool: str | None, tags: list[str] | None,
                  start_after: bool, actor: str, correlation_id=None) -> Operation:
    src_node = db.query(Node).filter(Node.id == source.node_id).one()
    cluster = db.query(Cluster).filter(Cluster.id == source.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    kind = gk.kind(source)
    name = (name or "").strip()
    target_node = (target_node or src_node.name).strip()
    ctx = {"guest_kind": kind, "source_workload_id": str(source.id), "source_vmid": source.vmid, "source_node": src_node.name, "node": target_node, "name": name, "newid": newid, "full": bool(full),
           "storage": storage or None, "pool": pool or None, "tags": None, "start_after": bool(start_after)}
    op = create_operation(db, OP_TYPE, cluster_id=cluster.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    if not VM_NAME_RE.match(name) or len(name) > 63:
        reasons.append("the name must be a DNS-style name: letters, digits and hyphens (dots between labels), at most 63 characters")
        blocking.append("SAFE-CLONE-001")
    try:
        ctx["tags"] = clean_tags(tags) if tags is not None else None
    except Exception as exc:  # noqa: BLE001
        reasons.append(str(exc))
        blocking.append("SAFE-CLONE-001")
    tnode = db.query(Node).filter(Node.cluster_id == cluster.id, Node.name == target_node, Node.is_missing.is_(False)).first()
    if tnode is None or tnode.status != "online" or tnode.maintenance_mode:
        reasons.append(f"{target_node} is not an online host outside maintenance")
        blocking.append("SAFE-CLONE-001")
    is_template = False
    disks: list[dict] = []
    try:
        client, _cred = build_pve_client(db, target)
        with client:
            cfg = gk.config(client, source, src_node.name)
            is_template = str(cfg.get("template", "0")) in ("1", "true")
            disks = owned_disks(cfg, kind)
            if kind == "lxc" and (gk.status_current(client, source, src_node.name).get("status") or source.status) != "stopped":
                reasons.append("a container can only be cloned while it is stopped: shut it down first")
                blocking.append("SAFE-CLONE-001")
            res = client.cluster_resources("vm") or []
            used = {int(r["vmid"]) for r in res if r.get("vmid") is not None}
            if newid is None:
                newid = int(client.cluster_next_id())
            elif newid in used:
                reasons.append(f"VM ID {newid} is already used in this cluster")
                blocking.append("SAFE-CLONE-001")
            if any(r.get("name") == name for r in res):
                reasons.append(f"a guest named '{name}' already exists: allowed, but confusing")
            try:
                # Proxmox's feature names: "copy" = a full copy of the disks, "clone" = a linked clone (templates only)
                feature = "clone" if (is_template and not full) else "copy"
                if not (client.qemu_feature(src_node.name, source.vmid, feature) if kind == "qemu" else client.lxc_feature(src_node.name, source.vmid, feature)):
                    reasons.append(f"Proxmox says this VM cannot be {'linked-cloned' if feature == 'clone' else 'copied'} in its current state (for a running VM that depends on its storage: shut it down first)")
                    blocking.append("SAFE-CLONE-001")
            except Exception:  # noqa: BLE001
                reasons.append("could not ask Proxmox whether this VM can be cloned: unverified")
            if full or not is_template:
                if not full and not is_template:
                    pass
                rows = client.storage_list(target_node) or []
                need = sum(parse_size_bytes(d["size"]) or 0 for d in disks)
                dest = storage or None
                if dest:
                    for p in check_target(rows, dest, need):
                        reasons.append(p)
                        blocking.append("SAFE-STORAGE-002")
                else:
                    reasons.append("no target storage chosen: each disk is copied to the same storage as the original" + (" (which must be reachable from the target host)" if target_node != src_node.name else ""))
                if need:
                    reasons.append(f"{need / GIB:.1f} GiB of disks are copied, which takes a while for a large VM (progress is in the Tasks panel)")
            if tnode is not None:
                free = ((client.node_status(target_node).get("memory") or {}).get("total") or 0) - ((client.node_status(target_node).get("memory") or {}).get("used") or 0)
                mem = int(str(cfg.get("memory", 0)).split(",")[0] or 0) << 20
                if free and mem > free:
                    reasons.append(f"{target_node} has {free / GIB:.1f} GiB of memory free and the clone asks for {mem / GIB:.1f} GiB: it would not start there")
                    blocking.append("SAFE-CREATE-001")
            if pool and pool not in {p.get("poolid") for p in client.pools() or []}:
                reasons.append(f"the pool '{pool}' does not exist in Proxmox")
                blocking.append("SAFE-CLONE-001")
        if not full and not is_template:
            reasons.append("a linked clone is only possible from a template: this is a normal VM, so a full clone is made")
            ctx["full"] = full = True
        if not full and is_template:
            reasons.append("a linked clone shares its disks with the template and is quick to make; the template can then not be deleted while the clone exists")
        if cfg.get("tags") and ctx["tags"] is None:
            reasons.append(f"the clone keeps the tags of the original ({', '.join(split_tags(cfg.get('tags')))})")
        lacking = missing_privileges(db, target, source.vmid, ["VM.Clone"])
        if lacking:
            reasons.append(permission_reason(lacking, "clone VMs"))
            blocking.append("SAFE-PERMISSION-001")
        if newid is not None:
            la = missing_privileges(db, target, newid, ["VM.Allocate"], path=f"/vms/{newid}")
            if la:
                reasons.append(permission_reason(la, "create VMs"))
                blocking.append("SAFE-PERMISSION-001")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"could not check the cluster: {str(exc)[:160]}")
        blocking.append("SAFE-STORAGE-002")
    ctx["newid"] = newid
    ctx["is_template"] = is_template
    if not blocking and newid is not None:
        reasons.insert(0, f"clones {vm_label(source)} ({'template' if is_template else 'VM'}) as VM {newid} '{name}' on {target_node}: {'full copy' if ctx['full'] else 'linked clone'}" + ("; started afterwards" if start_after else "; left powered off"))
    op.context = ctx
    db.commit()
    result = {"cluster": cluster.name, "node": target_node, "vmid": newid, "name": name, "workload_name": name, "source": vm_label(source), "full": ctx["full"], "eligible": not blocking,
              "reasons": reasons, "blocking_safety_rules": sorted(set(blocking))}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_clone(db, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise CloneError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "cloning", "tagging", "starting_up", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    cluster = db.query(Cluster).filter(Cluster.id == op.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    ctx = dict(op.context or {})
    src_node, src_vmid, node, newid = ctx["source_node"], int(ctx["source_vmid"]), ctx["node"], int(ctx["newid"])
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            rc, _cred = build_pve_client(db, target)
            with rc:
                res = rc.cluster_resources("vm") or []
            if newid in {int(r["vmid"]) for r in res if r.get("vmid") is not None}:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"VM ID {newid} was taken since the preview; nothing was created. Preview again")
            if src_vmid not in {int(r["vmid"]) for r in res if r.get("vmid") is not None}:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"the source VM {src_vmid} is gone; nothing was created")
            why = node_not_ready(db, cluster.id, node)
            if why:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"{why}; nothing was created. Preview again to choose another host")
            try:
                acquire_lock(db, resource_type="cluster", resource_id=cluster.id, operation_id=op.id, reason=OP_TYPE)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="cloning", stage="cloning")
        creds = load_pve_credentials(db, target, "maintenance")
        if op.status == "cloning":
            if not op.pve_upid:
                with PveMaintenanceClient(creds).for_guest(ctx.get("guest_kind", "qemu")) as wc:
                    op.pve_upid = wc.clone_vm(src_node, src_vmid, newid, name=ctx["name"], target=node if node != src_node else None, full=ctx["full"], storage=ctx.get("storage"), pool=ctx.get("pool"))
                db.commit()
            with PveMaintenanceClient(creds) as wc:
                res = wc.wait_for_task(src_node, op.pve_upid, timeout=7200)
            op.pve_upid = None
            db.commit()
            if not res.finished_ok:
                release_locks_for_operation(db, op.id)
                rc, _cred = build_pve_client(db, target)
                with rc:
                    left = any(int(r.get("vmid", -1)) == newid for r in rc.cluster_resources("vm") or [])
                return fail_operation(db, op, error=f"Proxmox reported the clone did not succeed: {res.exit_status or res.raw}." + (f" A partly made VM {newid} may exist: check it in Proxmox." if left else " No VM was left behind."))
            nxt = "tagging" if ctx.get("tags") is not None else ("starting_up" if ctx["start_after"] else "verifying")
            op = enter_stage(db, op, status=nxt, stage=nxt)
        if op.status == "tagging":
            with PveMaintenanceClient(creds) as wc:
                wc.set_vm_tags(node, newid, ctx["tags"], guest_type="vm" if ctx.get("guest_kind", "qemu") == "qemu" else "lxc")
            op = enter_stage(db, op, status="starting_up" if ctx["start_after"] else "verifying", stage="starting_up" if ctx["start_after"] else "verifying")
        if op.status == "starting_up":
            if not op.pve_upid:
                with PveMaintenanceClient(creds).for_guest(ctx.get("guest_kind", "qemu")) as wc:
                    op.pve_upid = wc.start_vm(node, newid)
                db.commit()
            with PveMaintenanceClient(creds) as wc:
                res = wc.wait_for_task(node, op.pve_upid, timeout=300)
            op.pve_upid = None
            db.commit()
            if not res.finished_ok:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"VM {newid} was cloned but did not start: {res.exit_status or res.raw}")
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            rc, _cred = build_pve_client(db, target)
            with rc:
                ct = ctx.get("guest_kind", "qemu") == "lxc"
                cfg = rc.lxc_config(node, newid) if ct else rc.qemu_config(node, newid)
                src_cfg = rc.lxc_config(src_node, src_vmid) if ct else rc.qemu_config(src_node, src_vmid)
                live = next((v for v in (rc.lxc_list(node) if ct else rc.qemu_list(node)) if int(v.get("vmid", -1)) == newid), None)
            problems = []
            name_key = "hostname" if ctx.get("guest_kind") == "lxc" else "name"
            if cfg.get(name_key) != ctx["name"]:
                problems.append(f"{name_key} is {cfg.get(name_key)}")
            for k in (("cores", "memory", "swap", "unprivileged") if ctx.get("guest_kind") == "lxc" else ("cores", "memory", "sockets", "cpu", "bios", "machine")):
                if str(cfg.get(k)) != str(src_cfg.get(k)):
                    problems.append(f"{k} differs from the source")
            if live is None:
                problems.append("the clone is not listed by Proxmox")
            if ctx.get("tags") is not None and sorted(t.lower() for t in split_tags(cfg.get("tags"))) != sorted(t.lower() for t in ctx["tags"]):
                problems.append("the tags did not match")
            op.verification_result = {"vmid": newid, "node": node, "status": (live or {}).get("status"), "problems": problems}
            db.commit()
            release_locks_for_operation(db, op.id)
            if problems:
                return fail_operation(db, op, error="read-back after the clone did not match: " + "; ".join(problems) + f". VM {newid} exists in Proxmox")
            try:
                from .discovery import run_discovery
                run_discovery(db, target, actor="operation")
            except Exception:  # noqa: BLE001
                pass
            wl = db.query(Workload).filter(Workload.cluster_id == cluster.id, Workload.vmid == newid).first()
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
