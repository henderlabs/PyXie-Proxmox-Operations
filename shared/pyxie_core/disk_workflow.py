"""Disks on a VM (workload.disk_change): add a new disk, detach one (its data is kept as an "unused" disk), attach an unused disk, delete an unused disk for good,
and move a disk to another VM on the same host (Proxmox "reassign owner").

Same Safety Contract as every other operation: dry-run -> approve -> revalidate -> lock -> execute -> read the config back -> verify -> audit.
Rules of thumb the preview enforces: detaching, and moving an attached disk, need the VM stopped (a running guest would lose a disk under its feet); an unused disk can be moved or deleted while the VM runs; adding works on a
running VM for SCSI/VirtIO/SATA (hot-plug); deleting needs the volume's name typed; PyXie never detaches, moves or deletes a disk of the VM it runs on.
Detach and move keep the data: nothing is destroyed until an unused disk is deleted, which cannot be undone.
"""

import re

from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .labels import vm_label
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Node, Operation, PveTarget, Workload
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_permissions import friendly_error, missing_privileges, permission_reason
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient, TaskTimeoutError, self_vmid_from_env
from . import rollback

OP_TYPE = "workload.disk_change"
ACTIONS = ("add", "detach", "attach", "delete_unused", "move_to_vm", "edit_options", "remove_cdrom")
BUSES = {"scsi": 31, "virtio": 16, "sata": 6, "ide": 4}
HOT_PLUG = ("scsi", "virtio", "sata")
DISK_RE = re.compile(r"^(scsi|virtio|sata|ide)(\d+)$")
UNUSED_RE = re.compile(r"^unused(\d+)$")
CACHES = ("none", "directsync", "writethrough", "writeback", "unsafe")
STORAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,39}$")


class DiskError(Exception):
    pass


def parse_disk(value) -> dict:
    """'local-lvm:vm-101-disk-0,cache=none,size=32G' -> {"volume", "options": {...}}. Pure."""
    parts = str(value or "").split(",")
    opts: dict[str, str] = {}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        if k:
            opts[k] = v
    return {"volume": parts[0], "options": opts}


def is_cdrom(value) -> bool:
    return "media=cdrom" in str(value or "")


def disk_keys(config: dict) -> list[str]:
    """The data disks (not CD/DVD drives) in a VM config, in slot order. Pure."""
    keys = [k for k, v in config.items() if DISK_RE.match(k) and not is_cdrom(v) and str(v) != "none"]
    return sorted(keys, key=lambda k: (DISK_RE.match(k).group(1), int(DISK_RE.match(k).group(2))))


def unused_keys(config: dict) -> list[str]:
    return sorted((k for k in config if UNUSED_RE.match(k)), key=lambda k: int(UNUSED_RE.match(k).group(1)))


def free_slot(config: dict, bus: str) -> str | None:
    """The first unused slot name on a bus ('scsi3'), or None when the bus is full. Pure."""
    if bus not in BUSES:
        return None
    used = {int(m.group(2)) for k in config if (m := DISK_RE.match(k)) and m.group(1) == bus}
    return next((f"{bus}{i}" for i in range(BUSES[bus]) if i not in used), None)


def volume_name(volid: str) -> str:
    """'local-lvm:vm-101-disk-1' -> 'vm-101-disk-1'. Pure."""
    return str(volid).split(":", 1)[-1]


def build_new_disk(storage: str, size_gb: int, *, bus: str, cache: str, discard: bool, ssd: bool, iothread: bool) -> str:
    """The config value that makes Proxmox allocate a new disk of this size. Pure."""
    out = [f"{storage}:{int(size_gb)}"]
    if cache and cache != "none":
        out.append(f"cache={cache}")
    if discard:
        out.append("discard=on")
    if ssd and bus != "virtio":
        out.append("ssd=1")
    if iothread and bus in ("scsi", "virtio"):
        out.append("iothread=1")
    return ",".join(out)


def build_options_line(volume: str, options: dict, *, bus: str, cache: str, discard: bool, ssd: bool, iothread: bool, backup: bool, readonly: bool) -> str:
    """A disk line with the options this operation manages rewritten (cache, discard, ssd, iothread, backup, readonly) and every other option (size, aio, ...) kept. Pure."""
    keep = {k: v for k, v in options.items() if k not in ("cache", "discard", "ssd", "iothread", "backup", "ro")}
    out = [volume]
    if cache and cache != "none":
        out.append(f"cache={cache}")
    if discard:
        out.append(f"discard={options.get('discard') if options.get('discard') in ('on', 'ignore') else 'on'}")  # keep 'ignore' if that is what it was
    if ssd and bus != "virtio":
        out.append("ssd=1")
    if iothread and bus in ("scsi", "virtio"):
        out.append("iothread=1")
    if not backup:
        out.append("backup=0")
    if readonly:
        out.append("ro=1")
    out += [f"{k}={v}" for k, v in keep.items()]
    return ",".join(out)


def option_flags(options: dict) -> dict:
    """The managed options of a disk line as the editor shows them. Pure."""
    return {"cache": options.get("cache", "none"), "discard": options.get("discard") in ("on", "1", "ignore"), "ssd": options.get("ssd") in ("1", "on"),
            "iothread": options.get("iothread") in ("1", "on"), "backup": options.get("backup", "1") not in ("0", "off"), "readonly": options.get("ro") in ("1", "on")}


def boot_uses(config: dict, key: str) -> bool:
    """Is this disk in the VM's boot order? Pure."""
    boot = str(config.get("boot") or "")
    return f"{key}" in boot.replace("order=", "").split(";") or str(config.get("bootdisk") or "") == key


def _load_context(db, workload: Workload):
    node = db.query(Node).filter(Node.id == workload.node_id).one()
    cluster = db.query(Cluster).filter(Cluster.id == workload.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    return node, cluster, target


def dry_run_disk_change(db, workload: Workload, *, action: str, key: str | None = None, bus: str | None = None, size_gb: int | None = None, storage: str | None = None,
                        cache: str = "none", discard: bool = False, ssd: bool = False, iothread: bool = False, target_workload_id=None, confirm: str = "",
                        backup: bool = True, readonly: bool = False, path: str | None = None, actor: str, correlation_id=None) -> Operation:
    if action not in ACTIONS:
        raise DiskError(f"action must be one of {', '.join(ACTIONS)}")
    if workload.type != "vm":
        from .ct_mount_workflow import dry_run_ct_mount
        return dry_run_ct_mount(db, workload, action=action, key=key, storage=storage, size_gb=size_gb, path=path, backup=backup, confirm=confirm, actor=actor, correlation_id=correlation_id)
    node, cluster, target = _load_context(db, workload)
    ctx = {"node_id": str(node.id), "node": node.name, "vmid": workload.vmid, "action": action, "key": key, "was_running": workload.status == "running"}
    op = create_operation(db, OP_TYPE, cluster_id=cluster.id, node_id=node.id, workload_id=workload.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    self_vm = str(workload.vmid) == (self_vmid_from_env() or "")
    plan: dict = {}
    config: dict = {}
    running = ctx["was_running"]
    target_wl = None
    tconfig: dict = {}
    try:
        client, _cred = build_pve_client(db, target)
        with client:
            config = client.qemu_config(node.name, workload.vmid)
            live = next((v for v in client.qemu_list(node.name) if int(v.get("vmid", -1)) == workload.vmid), None)
            running = bool(live and live.get("status") == "running")
            ctx["was_running"] = running
            if action == "move_to_vm":
                target_wl = db.query(Workload).filter(Workload.id == target_workload_id, Workload.cluster_id == cluster.id, Workload.is_missing.is_(False)).one_or_none() if target_workload_id else None
                if target_wl is not None and target_wl.type == "vm":
                    tnode = db.query(Node).filter(Node.id == target_wl.node_id).one()
                    if tnode.id == node.id:
                        tconfig = client.qemu_config(node.name, target_wl.vmid)
            if action == "add" and storage:
                stores = {s.get("storage"): s for s in client.storage_list(node.name) or []}
                st = stores.get(storage)
                if st is None or "images" not in str(st.get("content") or "") or not st.get("active", 1):
                    reasons.append(f"'{storage}' cannot hold VM disks on {node.name}: pick an active datastore that holds VM disks")
                    blocking.append("SAFE-DISK-001")
                elif st.get("avail") is not None and size_gb and int(size_gb) * 1073741824 > int(st["avail"]):
                    reasons.append(f"{storage} has only {int(st['avail']) // 1073741824} GiB free: a {size_gb} GiB disk would not fit")
                    blocking.append("SAFE-DISK-001")
        if config.get("lock"):
            reasons.append(f"Proxmox has the VM locked ('{config.get('lock')}'): another task is working on it")
            blocking.append("SAFE-LOCK-001")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"could not read {vm_label(workload)} from Proxmox: {str(exc)[:140]}")
        blocking.append("SAFE-STORAGE-002")

    needs_off = action in ("detach", "move_to_vm")
    if not blocking:
        if action == "add":
            bus = bus or "scsi"
            slot = free_slot(config, bus)
            if bus not in BUSES or slot is None:
                reasons.append(f"no free {bus} slot on this VM" if bus in BUSES else f"the bus must be one of {', '.join(BUSES)}")
                blocking.append("SAFE-DISK-001")
            if not storage or not STORAGE_RE.match(storage):
                reasons.append("choose a datastore for the new disk")
                blocking.append("SAFE-DISK-001")
            if not size_gb or not (1 <= int(size_gb) <= 65536):
                reasons.append("a disk is 1 to 65536 GiB")
                blocking.append("SAFE-DISK-001")
            if cache not in CACHES:
                reasons.append(f"cache must be one of {', '.join(CACHES)}")
                blocking.append("SAFE-DISK-001")
            if running and bus and bus not in HOT_PLUG:
                reasons.append(f"{bus} disks cannot be added while the VM runs: shut it down first, or use SCSI, VirtIO or SATA")
                blocking.append("SAFE-DISK-002")
            if not blocking:
                key = slot
                line = build_new_disk(storage, int(size_gb), bus=bus, cache=cache, discard=discard, ssd=ssd, iothread=iothread)
                plan = {"key": key, "line": line}
                reasons.append(f"adds a new {size_gb} GiB disk {key} on {storage}" + (": hot-plugged into the running VM" if running else ""))
        elif action in ("detach", "move_to_vm"):
            val = str(config.get(key or "") or "")
            unused_move = action == "move_to_vm" and bool(UNUSED_RE.match(key or ""))  # an unused disk is attached to nothing, so the VM can be running
            if not (DISK_RE.match(key or "") or unused_move) or not val or is_cdrom(val):
                reasons.append(f"'{key}' is not a data disk of this VM")
                blocking.append("SAFE-DISK-001")
            else:
                volid = parse_disk(val)["volume"]
                plan = {"key": key, "volume": volid, "old_line": val}
                if running and not unused_move:
                    reasons.append("the VM is running: shut it down first, so the guest does not lose a disk under its feet")
                    blocking.append("SAFE-DISK-002")
                if self_vm:
                    reasons.append("this is the VM PyXie itself runs on: PyXie will not take its disks away")
                    blocking.append("SAFE-DISK-002")
                if not unused_move and boot_uses(config, key):
                    reasons.append(f"{key} is in the boot order: the VM will not boot without it until you choose another boot disk")
                if action == "detach":
                    reasons.append(f"detaches {key} ({volid}): the VM no longer sees it, the data stays on the datastore as an unused disk you can attach again")
                else:
                    if target_wl is None or target_wl.type != "vm":
                        reasons.append("choose a VM to move the disk to")
                        blocking.append("SAFE-DISK-001")
                    elif target_wl.id == workload.id:
                        reasons.append("choose a different VM")
                        blocking.append("SAFE-DISK-001")
                    elif target_wl.node_id != workload.node_id:
                        reasons.append(f"{vm_label(target_wl)} is on another host: Proxmox moves a disk between VMs on the same host only")
                        blocking.append("SAFE-DISK-001")
                    elif str(target_wl.vmid) == (self_vmid_from_env() or ""):
                        reasons.append("PyXie will not attach disks to the VM it runs on")
                        blocking.append("SAFE-DISK-002")
                    else:
                        tbus = bus or (DISK_RE.match(key).group(1) if DISK_RE.match(key) else "scsi")
                        tslot = free_slot(tconfig, tbus)
                        if tslot is None:
                            reasons.append(f"{vm_label(target_wl)} has no free {tbus} slot")
                            blocking.append("SAFE-DISK-001")
                        else:
                            if tconfig.get("lock"):
                                reasons.append(f"{vm_label(target_wl)} is locked by Proxmox ('{tconfig.get('lock')}')")
                                blocking.append("SAFE-LOCK-001")
                            plan.update({"target_vmid": target_wl.vmid, "target_workload_id": str(target_wl.id), "target_slot": tslot, "target_name": target_wl.name})
                            reasons.append(f"moves {key} ({volid}) from {vm_label(workload)} to {vm_label(target_wl)} as {tslot}: the data is not copied, the volume is re-labelled to the new owner")
                            if target_wl.status == "running" and tbus not in HOT_PLUG:
                                reasons.append(f"{vm_label(target_wl)} is running and {tbus} disks cannot be hot-plugged: the disk appears at its next restart")
        elif action == "attach":
            val = str(config.get(key or "") or "")
            if not UNUSED_RE.match(key or "") or not val:
                reasons.append(f"'{key}' is not an unused disk of this VM")
                blocking.append("SAFE-DISK-001")
            else:
                bus = bus or "scsi"
                slot = free_slot(config, bus)
                if slot is None:
                    reasons.append(f"no free {bus} slot on this VM" if bus in BUSES else f"the bus must be one of {', '.join(BUSES)}")
                    blocking.append("SAFE-DISK-001")
                elif running and bus not in HOT_PLUG:
                    reasons.append(f"{bus} disks cannot be attached while the VM runs: shut it down first, or use SCSI, VirtIO or SATA")
                    blocking.append("SAFE-DISK-002")
                else:
                    plan = {"key": key, "volume": val.split(",")[0], "new_key": slot, "line": val}
                    reasons.append(f"attaches the unused disk {key} ({val.split(',')[0]}) as {slot}")
        elif action == "delete_unused":
            val = str(config.get(key or "") or "")
            if not UNUSED_RE.match(key or "") or not val:
                reasons.append(f"'{key}' is not an unused disk of this VM")
                blocking.append("SAFE-DISK-001")
            else:
                volid = val.split(",")[0]
                plan = {"key": key, "volume": volid}
                if self_vm:
                    reasons.append("this is the VM PyXie itself runs on: PyXie will not destroy its disks")
                    blocking.append("SAFE-DISK-002")
                if (confirm or "").strip() != volume_name(volid):
                    reasons.append(f"type the disk's name ({volume_name(volid)}) to confirm: this destroys its data and cannot be undone")
                    blocking.append("SAFE-DISK-001")
                else:
                    reasons.append(f"destroys the unused disk {volid} and everything on it. This cannot be undone")
        elif action == "edit_options":
            val = str(config.get(key or "") or "")
            m = DISK_RE.match(key or "")
            if not m or not val or is_cdrom(val):
                reasons.append(f"'{key}' is not a data disk of this VM")
                blocking.append("SAFE-DISK-001")
            elif cache not in CACHES:
                reasons.append(f"cache must be one of {', '.join(CACHES)}")
                blocking.append("SAFE-DISK-001")
            else:
                d = parse_disk(val)
                before = option_flags(d["options"])
                new_line = build_options_line(d["volume"], d["options"], bus=m.group(1), cache=cache, discard=discard, ssd=ssd, iothread=iothread, backup=backup, readonly=readonly)
                after = option_flags(parse_disk(new_line)["options"])
                changes = [f"{k}: {'on' if before[k] is True else 'off' if before[k] is False else before[k]} -> {'on' if after[k] is True else 'off' if after[k] is False else after[k]}" for k in before if before[k] != after[k]]
                if not changes:
                    reasons.append("nothing to change: this disk already has exactly these options")
                    blocking.append("SAFE-DISK-001")
                else:
                    plan = {"key": key, "old_line": val, "new_line": new_line, "expected": after}
                    reasons.append(f"{key} ({d['volume']}): " + "; ".join(changes))
                    if running:
                        reasons.append("the VM is running: Proxmox applies these changes at its next stop and start")
                    if "readonly" in [c.split(":")[0] for c in changes] and after["readonly"]:
                        reasons.append("a read-only disk cannot be written by the guest: do not set this on a disk the guest needs to write to")
                    if "backup" in [c.split(":")[0] for c in changes] and not after["backup"]:
                        reasons.append("a disk with backup off is left out of every backup of this VM: its data would not be restorable")
                    if after["iothread"] and config.get("scsihw") != "virtio-scsi-single" and m.group(1) == "scsi":
                        reasons.append("IO thread on a SCSI disk only helps with the VirtIO SCSI single controller")
        elif action == "remove_cdrom":
            val = str(config.get(key or "") or "")
            if not DISK_RE.match(key or "") or not is_cdrom(val):
                reasons.append(f"'{key}' is not a CD/DVD drive of this VM")
                blocking.append("SAFE-DISK-001")
            else:
                plan = {"key": key, "old_line": val}
                reasons.append(f"removes the CD/DVD drive {key}" + (f" (holding {parse_disk(val)['volume']})" if parse_disk(val)["volume"] not in ("none", "") else " (empty)") + (": a running VM loses it at its next stop and start" if running else ""))
                if boot_uses(config, key):
                    reasons.append(f"{key} is in the boot order: the VM will not boot from it any more")
        lacking = list(missing_privileges(db, target, workload.vmid, ["VM.Config.Disk"] + (["VM.Config.CDROM"] if action == "remove_cdrom" else [])) or [])
        if action == "add" and storage and STORAGE_RE.match(storage):
            lacking += missing_privileges(db, target, 0, ["Datastore.AllocateSpace"], path=f"/storage/{storage}") or []
        if lacking:
            reasons.append(permission_reason(lacking, "change disks"))
            blocking.append("SAFE-PERMISSION-001")
    ctx.update({"key": plan.get("key", key), "plan": plan, "needs_off": needs_off})
    op.context = ctx
    db.commit()
    result = {"vmid": workload.vmid, "workload_name": workload.name, "node": node.name, "action": action, "key": plan.get("key", key), "plan": plan,
              "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": sorted(set(blocking))}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_disk_change(db, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise DiskError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    workload = db.query(Workload).filter(Workload.id == op.workload_id).one()
    if (op.context or {}).get("guest") == "lxc":
        from .ct_mount_workflow import execute_ct_mount
        return execute_ct_mount(db, op, workload)
    node, cluster, target = _load_context(db, workload)
    ctx = dict(op.context or {})
    action, plan = ctx["action"], ctx.get("plan") or {}
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            rc, _cred = build_pve_client(db, target)
            with rc:
                config = rc.qemu_config(node.name, workload.vmid)
                live = next((v for v in rc.qemu_list(node.name) if int(v.get("vmid", -1)) == workload.vmid), None)
                tconfig = rc.qemu_config(node.name, plan["target_vmid"]) if action == "move_to_vm" else {}
            running = bool(live and live.get("status") == "running")
            problem = None
            if config.get("lock") or (tconfig or {}).get("lock"):
                problem = "a VM is locked by Proxmox"
            elif action == "add" and (plan["key"] in config or free_slot(config, DISK_RE.match(plan["key"]).group(1)) is None):
                problem = "the disk slot is no longer free"
            elif action in ("detach", "move_to_vm") and (str(config.get(plan["key"]) or "") != plan["old_line"] or (running and not UNUSED_RE.match(plan["key"]))):
                problem = "the disk changed, or the VM was started, since the preview"
            elif action in ("detach", "move_to_vm", "delete_unused") and str(workload.vmid) == (self_vmid_from_env() or ""):
                problem = "this is the VM PyXie itself runs on"
            elif action in ("edit_options", "remove_cdrom") and str(config.get(plan["key"]) or "") != plan["old_line"]:
                problem = "the disk changed since the preview"
            elif action == "attach" and (str(config.get(plan["key"]) or "").split(",")[0] != plan["volume"] or plan["new_key"] in config):
                problem = "the unused disk or the target slot changed since the preview"
            elif action == "delete_unused" and str(config.get(plan["key"]) or "").split(",")[0] != plan["volume"]:
                problem = "the unused disk is gone, or is a different volume than the one previewed"
            elif action == "move_to_vm" and plan["target_slot"] in tconfig:
                problem = "the slot on the other VM is no longer free"
            if problem:
                release_locks_for_operation(db, op.id)
                return block_operation(db, op, blocking_safety_rules=["SAFE-DISK-001"], actor="system")
            try:
                acquire_lock(db, resource_type="workload", resource_id=workload.id, operation_id=op.id, reason=OP_TYPE)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")
        if op.status == "executing":
            with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as wc:
                if action == "add":
                    wc.set_vm_config_keys(node.name, workload.vmid, {plan["key"]: plan["line"]})
                elif action == "detach":
                    wc.set_vm_config_keys(node.name, workload.vmid, {"delete": plan["key"]})
                elif action == "attach":
                    wc.set_vm_config_keys(node.name, workload.vmid, {plan["new_key"]: plan["line"]})
                    wc.set_vm_config_keys(node.name, workload.vmid, {"delete": plan["key"]})  # the unused entry; Proxmox keeps the volume, it is now attached
                elif action == "delete_unused":
                    wc.set_vm_config_keys(node.name, workload.vmid, {"delete": plan["key"]})
                elif action == "edit_options":
                    wc.set_vm_config_keys(node.name, workload.vmid, {plan["key"]: plan["new_line"]})
                elif action == "remove_cdrom":
                    wc.set_vm_config_keys(node.name, workload.vmid, {"delete": plan["key"]})
                elif action == "move_to_vm":
                    if not op.pve_upid:
                        op.pve_upid = wc.move_disk_to_vm(node.name, workload.vmid, plan["key"], plan["target_vmid"], plan["target_slot"])
                        db.commit()
                    res = wc.wait_for_task(node.name, op.pve_upid, timeout=900)
                    if not res.finished_ok:
                        release_locks_for_operation(db, op.id)
                        return fail_operation(db, op, error=f"Proxmox did not move the disk: {res.exit_status or res.raw}")
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            rc, _cred = build_pve_client(db, target)
            problems = []
            with rc:
                config = rc.qemu_config(node.name, workload.vmid)
                tconfig = rc.qemu_config(node.name, plan["target_vmid"]) if action == "move_to_vm" else {}
                pending = rc.qemu_pending(node.name, workload.vmid) if action in ("edit_options", "remove_cdrom") else []
            vol = plan.get("volume")
            if action == "add":
                if plan["key"] not in config:
                    problems.append(f"{plan['key']} is not in the VM's configuration")
            elif action == "detach":
                if plan["key"] in config:
                    problems.append(f"{plan['key']} is still attached")
                if not any(str(config.get(u) or "").startswith((vol or "?") + ",") or str(config.get(u) or "") == vol for u in unused_keys(config)):
                    problems.append("the detached disk does not show up as an unused disk")
            elif action == "attach":
                if not str(config.get(plan["new_key"]) or "").startswith(vol or "?"):
                    problems.append(f"{plan['new_key']} does not hold the disk")
            elif action == "delete_unused":
                if plan["key"] in config:
                    problems.append(f"{plan['key']} is still listed")
            elif action == "edit_options":
                from .hw_edit_workflow import pending_value
                got = option_flags(parse_disk(pending_value(pending, config, plan["key"]))["options"])
                problems += [f"{k} is {got[k]}, expected {v}" for k, v in plan["expected"].items() if got.get(k) != v]
            elif action == "remove_cdrom":
                marked = any(r.get("key") == plan["key"] and r.get("delete") for r in pending or [])
                if plan["key"] in config and not marked:
                    problems.append(f"{plan['key']} is still present")
            elif action == "move_to_vm":
                if plan["key"] in config:
                    problems.append(f"{plan['key']} is still on the original VM")
                if plan["target_slot"] not in tconfig:
                    problems.append(f"{plan['target_slot']} is missing on the other VM")
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
    except TaskTimeoutError as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"{exc}. The task may still finish on the host: look at the Tasks panel and the guest list before trying again. PyXie did not verify the result.")
    except MutationsDisabledError as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=friendly_error(exc) if "Permission check failed" in str(exc) else f"unexpected error: {exc}")
