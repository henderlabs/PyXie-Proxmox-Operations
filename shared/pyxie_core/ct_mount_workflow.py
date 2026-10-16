"""Container mount points (workload.disk_change on a container): add a mount point, detach one (the data is kept as an "unused" volume), attach an unused volume at a path, delete an
unused volume for good. The root disk (rootfs) is never detached; grow it from Hardware > Disks > Grow.

Same Safety Contract as the VM disk changes. A container must be stopped for every one of these (mount points cannot be hot-plugged), deleting needs the volume's name typed.
"""

import re

from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .labels import vm_label
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Operation, Workload
from .operations_engine import OperationError, block_operation, create_operation, enter_stage, fail_operation
from .pve_permissions import friendly_error, missing_privileges, permission_reason
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient, TaskTimeoutError
from . import rollback

MP_RE = re.compile(r"^mp(\d+)$")
UNUSED_RE = re.compile(r"^unused(\d+)$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._\-]+(/[A-Za-z0-9._\-]+)*$")
STORAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,39}$")
ACTIONS = ("add", "detach", "attach", "delete_unused")


def parse_mount(value) -> dict:
    """'local-lvm:vm-200-disk-1,mp=/data,backup=1,size=8G' -> {"volume", "options"}. Pure."""
    parts = str(value or "").split(",")
    opts = {}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        if k:
            opts[k] = v
    return {"volume": parts[0], "options": opts}


def free_mp(config: dict) -> str | None:
    used = {int(m.group(1)) for k in config if (m := MP_RE.match(k))}
    return next((f"mp{i}" for i in range(256) if i not in used), None)


def mount_paths(config: dict) -> set[str]:
    return {parse_mount(v)["options"].get("mp") for k, v in config.items() if MP_RE.match(k) or k == "rootfs"} - {None}


def build_mount(storage: str, size_gb: int, path: str, backup: bool) -> str:
    return f"{storage}:{int(size_gb)},mp={path}" + (",backup=1" if backup else "")


def _ctx(db, workload):
    from .disk_workflow import _load_context
    return _load_context(db, workload)


def dry_run_ct_mount(db, workload: Workload, *, action: str, key: str | None, storage: str | None, size_gb: int | None, path: str | None, backup: bool, confirm: str, actor: str, correlation_id=None) -> Operation:
    from .disk_workflow import OP_TYPE, DiskError
    if action not in ACTIONS:
        raise DiskError(f"a container's disk action must be one of {', '.join(ACTIONS)}")
    node, cluster, target = _ctx(db, workload)
    ctx = {"node_id": str(node.id), "node": node.name, "vmid": workload.vmid, "action": action, "key": key, "guest": "lxc", "was_running": workload.status == "running"}
    op = create_operation(db, OP_TYPE, cluster_id=cluster.id, node_id=node.id, workload_id=workload.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    plan: dict = {}
    config: dict = {}
    try:
        client, _cred = build_pve_client(db, target)
        with client:
            config = client.lxc_config(node.name, workload.vmid)
            live = next((v for v in client.lxc_list(node.name) if int(v.get("vmid", -1)) == workload.vmid), None)
            running = bool(live and live.get("status") == "running")
            if action == "add" and storage:
                st = {s.get("storage"): s for s in client.storage_list(node.name) or []}.get(storage)
                if st is None or "rootdir" not in str(st.get("content") or "") or not st.get("active", 1):
                    reasons.append(f"'{storage}' cannot hold container disks on {node.name}: pick an active datastore that holds container disks")
                    blocking.append("SAFE-DISK-001")
                elif st.get("avail") is not None and size_gb and int(size_gb) * 1073741824 > int(st["avail"]):
                    reasons.append(f"{storage} has only {int(st['avail']) // 1073741824} GiB free: a {size_gb} GiB mount point would not fit")
                    blocking.append("SAFE-DISK-001")
        if config.get("lock"):
            reasons.append(f"Proxmox has the container locked ('{config.get('lock')}'): another task is working on it")
            blocking.append("SAFE-LOCK-001")
        if running:
            reasons.append("the container is running: stop it first (a mount point cannot be added or removed while it runs)")
            blocking.append("SAFE-DISK-002")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"could not read {vm_label(workload)} from Proxmox: {str(exc)[:140]}")
        blocking.append("SAFE-STORAGE-002")
    if not blocking:
        if action == "add":
            slot = free_mp(config)
            if not storage or not STORAGE_RE.match(storage):
                reasons.append("choose a datastore for the mount point")
                blocking.append("SAFE-DISK-001")
            if not size_gb or not (1 <= int(size_gb) <= 65536):
                reasons.append("a mount point is 1 to 65536 GiB")
                blocking.append("SAFE-DISK-001")
            if not path or not PATH_RE.match(path):
                reasons.append("the mount path is the folder inside the container, for example /data")
                blocking.append("SAFE-DISK-001")
            elif path in mount_paths(config) or path == "/":
                reasons.append(f"'{path}' is already a mount point of this container")
                blocking.append("SAFE-DISK-001")
            if slot is None:
                reasons.append("no free mount point slot")
                blocking.append("SAFE-DISK-001")
            if not blocking:
                plan = {"key": slot, "line": build_mount(storage, int(size_gb), path, backup)}
                reasons.append(f"adds a new {size_gb} GiB mount point {slot} on {storage}, mounted at {path}")
        elif action == "detach":
            val = str(config.get(key or "") or "")
            if not MP_RE.match(key or "") or not val:
                reasons.append("only a mount point (mp0, mp1 ...) can be detached; the root disk stays")
                blocking.append("SAFE-DISK-001")
            elif parse_mount(val)["volume"].startswith("/"):
                reasons.append(f"{key} is a folder of the host shared into the container, not a volume PyXie manages: change it in Proxmox")
                blocking.append("SAFE-DISK-001")
            else:
                m = parse_mount(val)
                plan = {"key": key, "volume": m["volume"], "old_line": val}
                reasons.append(f"detaches {key} ({m['volume']}, was mounted at {m['options'].get('mp', '?')}): the container no longer sees it, the data stays on the datastore as an unused volume you can attach again")
        elif action == "attach":
            val = str(config.get(key or "") or "")
            if not UNUSED_RE.match(key or "") or not val:
                reasons.append(f"'{key}' is not an unused volume of this container")
                blocking.append("SAFE-DISK-001")
            elif not path or not PATH_RE.match(path) or path in mount_paths(config):
                reasons.append("choose a mount path that is not already used, for example /data")
                blocking.append("SAFE-DISK-001")
            elif free_mp(config) is None:
                reasons.append("no free mount point slot")
                blocking.append("SAFE-DISK-001")
            else:
                vol = val.split(",")[0]
                plan = {"key": key, "volume": vol, "new_key": free_mp(config), "line": f"{vol},mp={path}" + (",backup=1" if backup else "")}
                reasons.append(f"attaches the unused volume {vol} as {plan['new_key']}, mounted at {path}")
        else:
            val = str(config.get(key or "") or "")
            if not UNUSED_RE.match(key or "") or not val:
                reasons.append(f"'{key}' is not an unused volume of this container")
                blocking.append("SAFE-DISK-001")
            else:
                vol = val.split(",")[0]
                plan = {"key": key, "volume": vol}
                if (confirm or "").strip() != vol.split(":", 1)[-1]:
                    reasons.append(f"type the volume's name ({vol.split(':', 1)[-1]}) to confirm: this destroys its data and cannot be undone")
                    blocking.append("SAFE-DISK-001")
                else:
                    reasons.append(f"destroys the unused volume {vol} and everything on it. This cannot be undone")
        lacking = list(missing_privileges(db, target, workload.vmid, ["VM.Config.Disk"]) or [])
        if action == "add" and storage and STORAGE_RE.match(storage):
            lacking += missing_privileges(db, target, 0, ["Datastore.AllocateSpace"], path=f"/storage/{storage}") or []
        if lacking:
            reasons.append(permission_reason(lacking, "change container disks"))
            blocking.append("SAFE-PERMISSION-001")
    ctx.update({"key": plan.get("key", key), "plan": plan})
    op.context = ctx
    db.commit()
    result = {"vmid": workload.vmid, "workload_name": workload.name, "node": node.name, "action": action, "key": plan.get("key", key), "plan": plan, "eligible": not blocking,
              "reasons": reasons, "blocking_safety_rules": sorted(set(blocking))}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def execute_ct_mount(db, op: Operation, workload: Workload) -> Operation:
    from .disk_workflow import OP_TYPE
    node, cluster, target = _ctx(db, workload)
    ctx = dict(op.context or {})
    action, plan = ctx["action"], ctx.get("plan") or {}
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            rc, _cred = build_pve_client(db, target)
            with rc:
                config = rc.lxc_config(node.name, workload.vmid)
                live = next((v for v in rc.lxc_list(node.name) if int(v.get("vmid", -1)) == workload.vmid), None)
            running = bool(live and live.get("status") == "running")
            bad = (config.get("lock") or running
                   or (action == "add" and (plan["key"] in config or plan["line"].split("mp=")[1].split(",")[0] in mount_paths(config)))
                   or (action == "detach" and str(config.get(plan["key"]) or "") != plan["old_line"])
                   or (action == "attach" and (str(config.get(plan["key"]) or "").split(",")[0] != plan["volume"] or plan["new_key"] in config))
                   or (action == "delete_unused" and str(config.get(plan["key"]) or "").split(",")[0] != plan["volume"]))
            if bad:
                release_locks_for_operation(db, op.id)
                return block_operation(db, op, blocking_safety_rules=["SAFE-DISK-001"], actor="system")
            try:
                acquire_lock(db, resource_type="workload", resource_id=workload.id, operation_id=op.id, reason=OP_TYPE)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")
        if op.status == "executing":
            with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")).for_guest("lxc") as wc:
                if action == "add":
                    wc.set_vm_config_keys(node.name, workload.vmid, {plan["key"]: plan["line"]})
                elif action == "attach":
                    wc.set_vm_config_keys(node.name, workload.vmid, {plan["new_key"]: plan["line"]})
                    wc.set_vm_config_keys(node.name, workload.vmid, {"delete": plan["key"]})
                else:
                    wc.set_vm_config_keys(node.name, workload.vmid, {"delete": plan["key"]})
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            rc, _cred = build_pve_client(db, target)
            with rc:
                config = rc.lxc_config(node.name, workload.vmid)
            problems = []
            vol = plan.get("volume")
            if action == "add" and plan["key"] not in config:
                problems.append(f"{plan['key']} is not in the container's configuration")
            elif action == "detach":
                if plan["key"] in config:
                    problems.append(f"{plan['key']} is still attached")
                if not any(str(v).split(",")[0] == vol for k, v in config.items() if UNUSED_RE.match(k)):
                    problems.append("the detached volume does not show up as unused")
            elif action == "attach" and not str(config.get(plan["new_key"]) or "").startswith(vol or "?"):
                problems.append(f"{plan['new_key']} does not hold the volume")
            elif action == "delete_unused" and plan["key"] in config:
                problems.append(f"{plan['key']} is still listed")
            op.verification_result = {"action": action, "key": plan.get("key"), "problems": problems}
            db.commit()
            release_locks_for_operation(db, op.id)
            if problems:
                return fail_operation(db, op, error="read-back after the change did not match: " + "; ".join(problems))
            try:
                from .discovery import run_discovery
                run_discovery(db, target, actor="operation")
            except Exception:  # noqa: BLE001
                pass
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.IRREVERSIBLE if action == "delete_unused" else rollback.MANUAL_REVERSIBLE)
        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")
    except (MutationsDisabledError, TaskTimeoutError) as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=friendly_error(exc) if "Permission check failed" in str(exc) else f"unexpected error: {exc}")
