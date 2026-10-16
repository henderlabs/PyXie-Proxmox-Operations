"""Hardware edits on an existing VM: grow a disk (workload.disk_grow) and change its machine type / CPU type (workload.hw_change).

Same Safety Contract as every other operation: dry-run -> approve -> revalidate -> lock -> execute -> read the config back from Proxmox ->
verify -> audit. Disks only ever grow (Proxmox cannot shrink one), online for VirtIO/SCSI. A machine or CPU type change is checked against
every host in the cluster first (hw_compat: real CPU flags and QEMU capabilities read from each host), can take a snapshot first so the
change can be undone, and applies the next time the VM is stopped and started: the preview offers to power-cycle it now.
"""

import re
from datetime import datetime, timezone

from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .hw_compat import check_cpu_type, check_machine, split_cpu, valid_cpu_type, valid_machine
from .labels import vm_label
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Node, Operation, PveTarget, Workload, WorkloadDisk
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_permissions import friendly_error, missing_privileges, permission_reason
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient, SelfProtectionError, TaskTimeoutError, self_vmid_from_env
from .resize_workflow import check_guest_agent
from .snapshot_workflow import real_snapshots, refresh_stored_snapshots
from . import guest_kind as gk
from . import rollback

GIB = 1 << 30
UNITS = {"": 1, "K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}
GROWABLE = re.compile(r"^(scsi|virtio|sata|ide)\d+$")
OP_GROW, OP_HW = "workload.disk_grow", "workload.hw_change"


class HwEditError(Exception):
    pass


def parse_size_bytes(text) -> int | None:
    m = re.match(r"^(\d+(?:\.\d+)?)([KMGT]?)$", str(text or "").strip())
    return int(float(m.group(1)) * UNITS[m.group(2)]) if m else None


def disk_size_bytes(value: str) -> int | None:
    """The size=… part of a disk line ('local-lvm:vm-1-disk-0,size=100G,discard=on'). Pure."""
    for part in str(value or "").split(",")[1:]:
        if part.startswith("size="):
            return parse_size_bytes(part[5:])
    return None


CT_GROWABLE = re.compile(r"^(rootfs|mp\d+)$")


def is_growable_disk(slot: str, value: str, lxc: bool = False) -> bool:
    v = str(value or "")
    first = v.split(",")[0]
    if lxc:
        return bool(CT_GROWABLE.match(slot)) and ":" in first and not first.startswith("/")
    return bool(GROWABLE.match(slot)) and "media=cdrom" not in v and not v.startswith("none") and ":" in first


def _load_context(db, workload: Workload):
    node = db.query(Node).filter(Node.id == workload.node_id).one()
    cluster = db.query(Cluster).filter(Cluster.id == workload.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    return node, cluster, target


def read_hosts(db, cluster: Cluster, client, current_node: str) -> list[dict]:
    """What each host in the cluster can run, read from Proxmox. A value that could not be read is None (reported as unverified)."""
    hosts = []
    for n in db.query(Node).filter(Node.cluster_id == cluster.id, Node.is_missing.is_(False)).order_by(Node.name).all():
        h = {"name": n.name, "is_current": n.name == current_node, "cpu_model": None, "flags": None, "models": None, "machines": None}
        try:
            info = (client.node_status(n.name) or {}).get("cpuinfo") or {}
            h["cpu_model"] = info.get("model")
            h["flags"] = set(str(info.get("flags") or "").split()) or None
        except Exception:  # noqa: BLE001
            pass
        try:
            h["models"] = {m.get("name"): m.get("vendor") for m in client.qemu_cpu_models(n.name) if m.get("name")}
        except Exception:  # noqa: BLE001
            pass
        try:
            h["machines"] = {m.get("id") for m in client.qemu_machine_types(n.name) if m.get("id")}
        except Exception:  # noqa: BLE001
            pass
        hosts.append(h)
    return hosts


def pending_value(rows: list[dict], config: dict, key: str):
    """The value a VM config key has or WILL have: a pending change wins over the running value. Pure."""
    for r in rows or []:
        if r.get("key") == key and "pending" in r:
            return r.get("pending")
    return config.get(key)


# ---------------------------------------------------------------- disk grow

def dry_run_disk_grow(db, workload: Workload, *, disk: str, new_size_gb: int, actor: str, correlation_id=None) -> Operation:
    if not isinstance(new_size_gb, int) or new_size_gb < 1:
        raise HwEditError("the new size must be a whole number of GiB")
    node, cluster, target = _load_context(db, workload)
    was_running = workload.status == "running"
    context = {"node_id": str(node.id), "node": node.name, "vmid": workload.vmid, "disk": disk, "new_size_gb": new_size_gb, "was_running": was_running}
    op = create_operation(db, OP_GROW, cluster_id=cluster.id, node_id=node.id, workload_id=workload.id, correlation_id=correlation_id, context=context, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    current = storage = None
    snaps: list[dict] = []
    free = None
    if workload.status not in ("running", "stopped"):
        reasons.append(f"workload status is '{workload.status}': not safe to change its disks right now")
        blocking.append("SAFE-MIGRATE-001")
    try:
        client, _cred = build_pve_client(db, target)
        with client:
            config = gk.config(client, workload, node.name)
            snaps = real_snapshots(gk.snapshots(client, workload, node.name))
            rows = client.storage_list(node.name)
        value = str(config.get(disk) or "")
        if not is_growable_disk(disk, value, lxc=workload.type == "lxc"):
            reasons.append(f"'{disk}' is not a disk that can be grown (only VirtIO, SCSI, SATA and IDE disks; not CD-ROMs, EFI or TPM disks)")
            blocking.append("SAFE-HW-001")
        else:
            current = disk_size_bytes(value)
            storage = value.split(":", 1)[0]
        if config.get("lock"):
            reasons.append(f"Proxmox has the VM locked ('{config.get('lock')}'): another task is working on it")
            blocking.append("SAFE-LOCK-001")
        srow = next((s for s in rows if s.get("storage") == storage), None) if storage else None
        free = srow.get("avail") if srow else None
        stype = (srow or {}).get("type")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"could not read {vm_label(workload)} from Proxmox: {str(exc)[:140]}")
        blocking.append("SAFE-STORAGE-002")
        srow, stype = None, None
    new_bytes = new_size_gb * GIB
    if current is not None:
        if new_bytes <= current:
            reasons.append(f"the new size ({new_size_gb} GiB) must be larger than the current {current / GIB:.0f} GiB: Proxmox can only grow a disk, never shrink one")
            blocking.append("SAFE-HW-001")
        else:
            delta = new_bytes - current
            reasons.append(f"{disk} grows from {current / GIB:.0f} GiB to {new_size_gb} GiB (+{delta / GIB:.0f} GiB). This cannot be undone: a disk can never be made smaller.")
            if free is not None and delta > free:
                msg = f"storage '{storage}' has {free / GIB:.0f} GiB free, less than the {delta / GIB:.0f} GiB added"
                if stype == "lvm":
                    reasons.append(msg + ": a thick LVM volume needs the space now")
                    blocking.append("SAFE-STORAGE-002")
                else:
                    reasons.append(msg + ": thin storage may accept it, but writes fail once the storage fills up")
            reasons.append("the guest then has to use the new space: extend the partition and filesystem inside it (Windows: Disk Management > Extend Volume; Linux: growpart + resize2fs/xfs_growfs)")
            if was_running:
                reasons.append("the VM keeps running: Proxmox grows a VirtIO or SCSI disk online")
    if snaps:
        reasons.append(f"the VM has {len(snaps)} snapshot{'s' if len(snaps) != 1 else ''}: some storage types refuse to grow a disk that has snapshots; Proxmox will say so and nothing changes")
    lacking = missing_privileges(db, target, workload.vmid, ["VM.Config.Disk"])
    if lacking:
        reasons.append(permission_reason(lacking, "resize disks"))
        blocking.append("SAFE-PERMISSION-001")
    result = {"vmid": workload.vmid, "workload_name": workload.name, "node": node.name, "disk": disk, "storage": storage, "current_bytes": current,
              "new_bytes": new_bytes, "was_running": was_running, "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": sorted(set(blocking))}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def execute_disk_grow(db, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise HwEditError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    workload = db.query(Workload).filter(Workload.id == op.workload_id).one()
    node, cluster, target = _load_context(db, workload)
    ctx = dict(op.context or {})
    disk, new_gb = ctx["disk"], int(ctx["new_size_gb"])
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            rc, _cred = build_pve_client(db, target)
            with rc:
                config = gk.config(rc, workload, node.name)
            cur = disk_size_bytes(str(config.get(disk) or ""))
            if config.get("lock") or cur is None or cur >= new_gb * GIB:
                release_locks_for_operation(db, op.id)
                why = "the VM is locked by Proxmox" if config.get("lock") else f"{disk} is now {cur / GIB:.0f} GiB or not a disk any more" if cur else f"{disk} is no longer a disk"
                return fail_operation(db, op, error=f"revalidation failed: {why}; nothing was changed")
            try:
                acquire_lock(db, resource_type="workload", resource_id=workload.id, operation_id=op.id, reason=OP_GROW)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")
        if op.status == "executing":
            with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")).for_guest(gk.kind(workload)) as wc:
                out = wc.resize_disk(node.name, workload.vmid, disk, f"{new_gb}G")
                if isinstance(out, str) and out.startswith("UPID:"):
                    res = wc.wait_for_task(node.name, out, timeout=900)
                    if not res.succeeded:
                        release_locks_for_operation(db, op.id)
                        return fail_operation(db, op, error=f"Proxmox reported the resize did not succeed: {res.exit_status or res.raw}")
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            rc, _cred = build_pve_client(db, target)
            with rc:
                config = gk.config(rc, workload, node.name)
            got = disk_size_bytes(str(config.get(disk) or ""))
            op.verification_result = {"disk": disk, "size_bytes": got, "expected_bytes": new_gb * GIB}
            db.commit()
            release_locks_for_operation(db, op.id)
            if got is None or got < new_gb * GIB:
                return fail_operation(db, op, error=f"read-back from Proxmox shows {disk} at {got / GIB:.0f} GiB, expected {new_gb} GiB" if got else f"read-back could not find {disk}")
            db.query(WorkloadDisk).filter(WorkloadDisk.workload_id == workload.id, WorkloadDisk.slot == disk).update({"size_bytes": got})
            db.commit()
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.IRREVERSIBLE)
        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")
    except (MutationsDisabledError, TaskTimeoutError, SelfProtectionError) as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=friendly_error(exc) if "Permission check failed" in str(exc) else f"unexpected error: {exc}")


# ---------------------------------------------------------------- machine / CPU type

def dry_run_hw_change(db, workload: Workload, *, machine: str | None, cpu_type: str | None, snapshot_first: bool, power_cycle: bool,
                      actor: str, correlation_id=None) -> Operation:
    if workload.type != "vm":
        raise HwEditError("hardware changes are only implemented for VMs (qemu), not containers")
    machine = (machine or "").strip() or None
    cpu_type = (cpu_type or "").strip() or None
    if machine and not valid_machine(machine):
        raise HwEditError(f"'{machine}' is not a machine type name")
    if cpu_type and not valid_cpu_type(cpu_type):
        raise HwEditError(f"'{cpu_type}' is not a CPU type name")
    node, cluster, target = _load_context(db, workload)
    was_running = workload.status == "running"
    ctx = {"node_id": str(node.id), "node": node.name, "vmid": workload.vmid, "was_running": was_running, "snapshot_first": bool(snapshot_first),
           "power_cycle": bool(power_cycle and was_running)}
    op = create_operation(db, OP_HW, cluster_id=cluster.id, node_id=node.id, workload_id=workload.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    verdicts: dict = {}
    cur_machine = cur_cpu = None
    new_machine = new_cpu = None
    if workload.status not in ("running", "stopped"):
        reasons.append(f"workload status is '{workload.status}': not safe to change its hardware right now")
        blocking.append("SAFE-MIGRATE-001")
    try:
        client, _cred = build_pve_client(db, target)
        with client:
            config = client.qemu_config(node.name, workload.vmid)
            hosts = read_hosts(db, cluster, client, node.name)
            can_snapshot = client.qemu_feature(node.name, workload.vmid, "snapshot") if snapshot_first else True
        cur_machine = config.get("machine")
        cur_base, cur_rest = split_cpu(config.get("cpu"))
        cur_cpu = cur_base or None
        if config.get("lock"):
            reasons.append(f"Proxmox has the VM locked ('{config.get('lock')}'): another task is working on it")
            blocking.append("SAFE-LOCK-001")
        cur_m_base, _, cur_m_rest = (cur_machine or "").partition(",")
        if machine and machine != cur_m_base:
            new_machine = machine + (("," + cur_m_rest) if cur_m_rest else "")  # keep extras such as viommu=intel
        if cpu_type and cpu_type != (cur_cpu or ""):
            new_cpu = cpu_type + cur_rest
        if not new_machine and not new_cpu:
            reasons.append("nothing to change: the VM already has this machine type and CPU type")
            blocking.append("SAFE-HW-001")
        if new_machine:
            rows = check_machine(new_machine, hosts)
            verdicts["machine"] = rows
            reasons.append(f"machine type: {cur_machine or 'default (i440fx)'} -> {new_machine}")
            if ("q35" in new_machine) != ("q35" in (cur_machine or "")) or (not cur_machine and "q35" in new_machine):
                reasons.append("moving between the i440fx and q35 machine types changes the virtual PCI layout: Windows may ask to re-activate or reinstall device drivers, and Linux can rename network interfaces (ens18 -> enp6s18) so its network config stops matching")
        if new_cpu:
            rows = check_cpu_type(cpu_type, hosts)
            verdicts["cpu"] = rows
            reasons.append(f"CPU type: {cur_cpu or 'default (kvm64)'} -> {new_cpu}")
        for kind, rows in verdicts.items():
            grouped: dict[str, list[str]] = {}
            for r in rows:
                if r["level"] == "block":
                    reasons.append(f"{kind} type cannot run on {r['node']}, where the VM lives: {r['reason']}")
                    blocking.append("SAFE-HW-001")
                elif r["level"] == "warn":
                    grouped.setdefault(r["reason"], []).append(r["node"])
            for why, names in grouped.items():
                reasons.append(f"{'all hosts' if len(names) == len(rows) else ', '.join(names)}: {why}")
        if snapshot_first and not can_snapshot:
            reasons.append("Proxmox says this VM cannot be snapshotted, so the safety snapshot cannot be taken: untick 'snapshot first' to go ahead without one")
            blocking.append("SAFE-SNAPSHOT-001")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"could not read {vm_label(workload)} from Proxmox: {str(exc)[:140]}")
        blocking.append("SAFE-STORAGE-002")
    if snapshot_first and "SAFE-SNAPSHOT-001" not in blocking:
        reasons.append("a snapshot is taken first (disks only), so this can be undone by rolling back; it stays until you delete it on the Snapshots tab")
    if was_running and power_cycle:
        if str(workload.vmid) == (self_vmid_from_env() or ""):
            reasons.append("this is the VM PyXie itself runs on: PyXie will not power it off")
            blocking.append("SAFE-HW-001")
        if workload.downtime_tolerance == "low":
            reasons.append("the workload is marked downtime tolerance 'low': power-cycling it needs explicit review")
            blocking.append("SAFE-DOWNTIME-001")
        if workload.ha_state:
            reasons.append(f"HA state: {workload.ha_state}: Proxmox's HA manager may restart this guest during the power-off window")
            blocking.append("SAFE-HA-001")
        ga = check_guest_agent(db, target, node, workload)
        if ga["state"] == "not_responding":
            reasons.append("the guest agent is switched on in Proxmox but is not answering inside the guest, so the graceful shutdown would time out; fix the agent, or apply the change by stopping and starting the VM yourself")
            blocking.append("SAFE-GUEST-AGENT-001")
        reasons.append("the VM is shut down, reconfigured and started again: it is off for about a minute")
    elif was_running:
        reasons.append("the VM keeps running on its current hardware; the change takes effect the next time it is shut down and started (a restart from inside the guest is not enough)")
    need = (["VM.Config.HWType"] if new_machine else []) + (["VM.Config.CPU"] if new_cpu else [])
    lacking = missing_privileges(db, target, workload.vmid, need) if need else None
    if lacking:
        reasons.append(permission_reason(lacking, "change machine and CPU types"))
        blocking.append("SAFE-PERMISSION-001")
    ctx.update({"new_machine": new_machine, "new_cpu": new_cpu, "old_machine": cur_machine, "old_cpu": cur_cpu})
    op.context = ctx
    db.commit()
    result = {"vmid": workload.vmid, "workload_name": workload.name, "node": node.name, "old_machine": cur_machine, "new_machine": new_machine,
              "old_cpu": cur_cpu, "new_cpu": new_cpu, "host_checks": verdicts, "snapshot_first": bool(snapshot_first), "power_cycle": ctx["power_cycle"],
              "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": sorted(set(blocking))}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_hw_change(db, operation_id) -> Operation:
    """Resumable. Stages: revalidating -> snapshotting (if asked) -> shutting_down (if power-cycling) -> reconfiguring -> starting_up -> verifying."""
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise HwEditError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "snapshotting", "shutting_down", "reconfiguring", "starting_up", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    workload = db.query(Workload).filter(Workload.id == op.workload_id).one()
    node, cluster, target = _load_context(db, workload)
    ctx = dict(op.context or {})
    was_running, power_cycle, snapshot_first = bool(ctx.get("was_running")), bool(ctx.get("power_cycle")), bool(ctx.get("snapshot_first"))
    new_machine, new_cpu = ctx.get("new_machine"), ctx.get("new_cpu")
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            rc, _cred = build_pve_client(db, target)
            with rc:
                live = next((v for v in rc.qemu_list(node.name) if int(v.get("vmid", -1)) == workload.vmid), None)
                config = rc.qemu_config(node.name, workload.vmid)
            if live is None:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"{vm_label(workload)} is no longer listed on {node.name}")
            if config.get("lock") or (live.get("status") == "running") != was_running:
                release_locks_for_operation(db, op.id)
                return block_operation(db, op, blocking_safety_rules=["SAFE-MIGRATE-001"], actor="system")
            # the extras on the CPU and machine settings (flags, viommu...) are taken from the VM as it is NOW, so edits made since the preview are not overwritten
            if new_cpu:
                new_cpu = new_cpu.partition(",")[0] + split_cpu(config.get("cpu"))[1]
            if new_machine:
                _, _, live_m_rest = str(config.get("machine") or "").partition(",")
                new_machine = new_machine.partition(",")[0] + (("," + live_m_rest) if live_m_rest else "")
            ctx = {**(op.context or {}), "new_cpu": new_cpu, "new_machine": new_machine}
            op.context = ctx
            db.commit()
            try:
                acquire_lock(db, resource_type="workload", resource_id=workload.id, operation_id=op.id, reason=OP_HW)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            nxt = "snapshotting" if snapshot_first else "shutting_down" if power_cycle else "reconfiguring"
            op = enter_stage(db, op, status=nxt, stage=nxt)
        creds = load_pve_credentials(db, target, "maintenance")
        if op.status == "snapshotting":
            name = ctx.get("safety_snapshot") or "pyxie-before-hw-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
            if not op.pve_upid:
                with PveMaintenanceClient(creds) as wc:
                    op.pve_upid = wc.create_snapshot(node.name, workload.vmid, name, description="taken by PyXie before a machine/CPU type change")
                op.context = {**(op.context or {}), "safety_snapshot": name}
                db.commit()
            with PveMaintenanceClient(creds) as wc:
                res = wc.wait_for_task(node.name, op.pve_upid, timeout=1800)
            if not res.succeeded:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"the safety snapshot did not succeed ({res.exit_status or res.raw}); the VM was not changed")
            op.pve_upid = None
            db.commit()
            nxt = "shutting_down" if power_cycle else "reconfiguring"
            op = enter_stage(db, op, status=nxt, stage=nxt)
        if op.status == "shutting_down":
            if not op.pve_upid:
                rc, _cred = build_pve_client(db, target)
                with rc:
                    now = next((v for v in rc.qemu_list(node.name) if int(v.get("vmid", -1)) == workload.vmid), None)
                if now is not None and now.get("status") == "stopped":
                    op = enter_stage(db, op, status="reconfiguring", stage="reconfiguring")  # already off (a resumed run): nothing to shut down
                else:
                    with PveMaintenanceClient(creds) as wc:
                        op.pve_upid = wc.shutdown_vm(node.name, workload.vmid, timeout=120)
                    db.commit()
        if op.status == "shutting_down":
            with PveMaintenanceClient(creds) as wc:
                res = wc.wait_for_task(node.name, op.pve_upid, timeout=180)
            if not res.succeeded:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"shutdown did not succeed: {res.raw}; the VM was not changed")
            op.pve_upid = None
            db.commit()
            op = enter_stage(db, op, status="reconfiguring", stage="reconfiguring")
        if op.status == "reconfiguring":
            try:
                with PveMaintenanceClient(creds) as wc:
                    wc.set_vm_hardware(node.name, workload.vmid, machine=new_machine, cpu=new_cpu)
            except Exception as exc:  # noqa: BLE001
                if not power_cycle:
                    raise
                restarted = False  # the VM was shut down for this change: never leave it off because Proxmox refused the edit
                try:
                    with PveMaintenanceClient(creds) as wc:
                        restarted = wc.wait_for_task(node.name, wc.start_vm(node.name, workload.vmid), timeout=300).succeeded
                except Exception:  # noqa: BLE001
                    pass
                release_locks_for_operation(db, op.id)
                why = friendly_error(exc) if "Permission check failed" in str(exc) else str(exc)
                return fail_operation(db, op, error=f"Proxmox refused the change: {why}. The VM was shut down for it; " + ("PyXie started it again." if restarted else "PyXie could not start it again: start it in Proxmox."))
            nxt = "starting_up" if power_cycle else "verifying"
            op = enter_stage(db, op, status=nxt, stage=nxt)
        if op.status == "starting_up":
            if not op.pve_upid:
                with PveMaintenanceClient(creds) as wc:
                    op.pve_upid = wc.start_vm(node.name, workload.vmid)
                db.commit()
            with PveMaintenanceClient(creds) as wc:
                res = wc.wait_for_task(node.name, op.pve_upid, timeout=300)
            if not res.succeeded:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"the VM did not start again: {res.raw}. The change itself was applied" + (f"; roll back to snapshot '{ctx.get('safety_snapshot')}' to undo it" if (op.context or {}).get("safety_snapshot") else ""))
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            rc, _cred = build_pve_client(db, target)
            with rc:
                config = rc.qemu_config(node.name, workload.vmid)
                pending = rc.qemu_pending(node.name, workload.vmid)
                live = next((v for v in rc.qemu_list(node.name) if int(v.get("vmid", -1)) == workload.vmid), None)
                snaps = rc.qemu_snapshots(node.name, workload.vmid)
            got_machine, got_cpu = pending_value(pending, config, "machine"), pending_value(pending, config, "cpu")
            problems = []
            if new_machine and str(got_machine or "") != new_machine:
                problems.append(f"machine is '{got_machine}', expected '{new_machine}'")
            if new_cpu and str(got_cpu or "") != new_cpu:
                problems.append(f"cpu is '{got_cpu}', expected '{new_cpu}'")
            expect = "running" if was_running else "stopped"
            if live is None or live.get("status") != expect:
                problems.append(f"status is {live.get('status') if live else 'missing'}, expected '{expect}'")
            op.verification_result = {"machine": got_machine, "cpu": got_cpu, "pending_keys": [r.get("key") for r in pending or [] if "pending" in r], "snapshot": (op.context or {}).get("safety_snapshot")}
            db.commit()
            release_locks_for_operation(db, op.id)
            if (op.context or {}).get("safety_snapshot"):
                refresh_stored_snapshots(db, workload, snaps)
            if problems:
                return fail_operation(db, op, error="read-back after the change did not match: " + "; ".join(problems))
            try:
                from .discovery import run_discovery
                run_discovery(db, target, actor="operation")
            except Exception:  # noqa: BLE001
                pass
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE if (op.context or {}).get("safety_snapshot") else rollback.UNKNOWN)
        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")
    except (MutationsDisabledError, TaskTimeoutError, SelfProtectionError) as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=friendly_error(exc) if "Permission check failed" in str(exc) else f"unexpected error: {exc}")
