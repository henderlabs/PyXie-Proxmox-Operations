"""workload.restore: restore a VM or container from a Proxmox Backup Server backup, as a new guest or over the existing one.

dry-run (the backup still exists and did not fail verification, its configuration, the new ID and name or the typed name for an
overwrite, the target host and storage with room, privileges, nothing PyXie itself runs on) -> approve -> revalidate (backup,
ID or guest state, host) -> lock -> restore (one Proxmox task, new hardware addresses) -> configure (name, network devices
connected or not, tag, note) -> optionally start -> read back -> inventory -> audit.

A new guest is undone with the guarded Delete. An overwrite replaces the guest's disks and snapshots with the backup and cannot be undone.
"""

from datetime import datetime, timezone

from . import guest_kind as gk
from . import rollback
from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .labels import vm_label
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Node, Operation, PveTarget, Workload
from .node_ready import node_not_ready
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_permissions import friendly_error, missing_privileges, permission_reason
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient, TaskTimeoutError, self_vmid_from_env
from .restore_points import VOLID_RE, config_disks, config_nets, net_is_down, net_with_link, parse_config_text
from .tags_workflow import split_tags
from .vm_create import VM_NAME_RE

OP_TYPE = "workload.restore"
GIB = 1 << 30
MARGIN = 1.1


class RestoreError(Exception):
    pass


def _timeout_for(size_bytes: int | None) -> int:
    """Seconds to wait for the Proxmox restore task: a floor of 30 minutes plus time for the data at a pessimistic 15 MB/s, at most 10 hours."""
    return int(min(36000, max(1800, 1800 + (size_bytes or 0) / 15e6)))


def _storage_problems(rows: list[dict], name: str, need: int, kind: str) -> list[str]:
    row = next((s for s in rows if s.get("storage") == name), None)
    if row is None:
        return [f"storage '{name}' is not available on that host"]
    out = []
    if not row.get("active", 1):
        out.append(f"storage '{name}' is not active on that host")
    want = "rootdir" if kind == "ct" else "images"
    if want not in str(row.get("content") or ""):
        out.append(f"storage '{name}' is not set up to hold {'container' if kind == 'ct' else 'VM'} disks")
    avail = row.get("avail")
    if avail is not None and need * MARGIN > avail:
        out.append(f"storage '{name}' has {avail / GIB:.0f} GB free, the restore needs about {need / GIB:.0f} GB plus a 10% margin")
    return out


def dry_run_restore(db, source: Workload, *, volid: str, mode: str, newid: int | None, name: str | None, target_node: str | None, storage: str | None,
                    pool: str | None, start_after: bool, nics_connected: bool, confirm_name: str | None, actor: str, correlation_id=None) -> Operation:
    cluster = db.query(Cluster).filter(Cluster.id == source.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    src_node = db.query(Node).filter(Node.id == source.node_id).one()
    kind = "ct" if source.type == "lxc" else "vm"
    overwrite = mode == "overwrite"
    node_name = src_node.name if overwrite else (target_node or src_node.name).strip()
    name = (name or "").strip() or (source.name if overwrite else f"{source.name or 'guest'}-restored")
    ctx = {"guest_kind": "lxc" if kind == "ct" else "qemu", "source_workload_id": str(source.id), "source_vmid": source.vmid, "source_name": source.name,
           "mode": mode, "volid": volid, "node": node_name, "name": name, "storage": storage or None, "pool": pool or None, "start_after": bool(start_after),
           "nics_connected": bool(nics_connected), "newid": None, "size": None, "disk_count": None, "nic_count": None}
    op = create_operation(db, OP_TYPE, cluster_id=cluster.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    info: dict = {}

    m = VOLID_RE.match(volid or "")
    if not m or m.group(1) != kind or int(m.group(2)) != source.vmid:
        reasons.append("that is not a backup of this guest")
        blocking.append("SAFE-RESTORE-001")
    if mode not in ("new", "overwrite"):
        reasons.append("choose to restore as a new guest or over the existing one")
        blocking.append("SAFE-RESTORE-001")
    if not VM_NAME_RE.match(name) or len(name) > 63:
        reasons.append("the name must be a DNS-style name: letters, digits and hyphens (dots between labels), at most 63 characters")
        blocking.append("SAFE-RESTORE-001")
    point = None
    disk_bytes = 0
    try:
        client, _cred = build_pve_client(db, target)
        with client, PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as adm:
            res = client.cluster_resources("vm") or []
            used = {int(r["vmid"]): r for r in res if r.get("vmid") is not None}
            tnode = db.query(Node).filter(Node.cluster_id == cluster.id, Node.name == node_name, Node.is_missing.is_(False)).first()
            if tnode is None or tnode.status != "online":
                reasons.append(f"{node_name} is not an online host")
                blocking.append("SAFE-RESTORE-001")
            elif tnode.maintenance_mode and not overwrite:
                reasons.append(f"{node_name} is in maintenance mode: choose another host")
                blocking.append("SAFE-RESTORE-001")
            # the backup: still there, and not known to be damaged
            store_name = volid.split(":", 1)[0] if volid else ""
            try:
                items = adm.backup_content(node_name if tnode else src_node.name, store_name, source.vmid)
                point = next((i for i in items if i.get("volid") == volid), None)
            except Exception as exc:  # noqa: BLE001
                reasons.append(f"could not list the backups on '{store_name}': {str(exc)[:120]}")
                blocking.append("SAFE-RESTORE-003")
            if point is None and "SAFE-RESTORE-003" not in blocking:
                reasons.append("that backup no longer exists")
                blocking.append("SAFE-RESTORE-003")
            if point is not None:
                vstate = (point.get("verification") or {}).get("state")
                info = {"backup_time": point.get("ctime"), "backup_size": point.get("size"), "verified": vstate}
                ctx["size"] = point.get("size")
                if vstate == "failed":
                    reasons.append("this backup FAILED verification on the backup server: restoring it would give a damaged guest. Pick another")
                    blocking.append("SAFE-RESTORE-003")
                elif vstate != "ok":
                    reasons.append("this backup has not been verified by the backup server (not an error; a restore test is the real proof)")
            # what is inside it
            cfg: dict = {}
            try:
                cfg = parse_config_text(adm.extract_config(node_name if tnode else src_node.name, volid))
            except Exception:  # noqa: BLE001
                reasons.append("could not read the guest configuration inside the backup: its disks and network devices are not shown")
            disks = config_disks(cfg, kind)
            nets = config_nets(cfg)
            disk_bytes = sum(d["size"] for d in disks)
            need = disk_bytes or int((point or {}).get("size") or 0)
            ctx["disk_count"], ctx["nic_count"] = len(disks), len(nets)
            rows = client.storage_list(node_name) or []
            if storage:
                for p in _storage_problems(rows, storage, need, kind):
                    reasons.append(p)
                    blocking.append("SAFE-STORAGE-002")
            elif disks:
                for st in sorted({d["storage"] for d in disks if d["storage"]}):
                    for p in _storage_problems(rows, st, sum(d["size"] for d in disks if d["storage"] == st), kind):
                        reasons.append(f"the backup puts a disk on {st}: {p}. Choose a target storage")
                        blocking.append("SAFE-STORAGE-002")
            if pool and pool not in {p.get("poolid") for p in client.pools() or []}:
                reasons.append(f"the pool '{pool}' does not exist in Proxmox")
                blocking.append("SAFE-RESTORE-001")
            if overwrite:
                vmid = source.vmid
                live = used.get(vmid)
                if live is None:
                    reasons.append("the guest no longer exists in Proxmox: restore it as a new guest")
                    blocking.append("SAFE-RESTORE-002")
                else:
                    if live.get("status") != "stopped":
                        reasons.append("the guest is running: shut it down first (an overwrite needs it stopped)")
                        blocking.append("SAFE-RESTORE-002")
                    if live.get("hastate") not in (None, "", "unmanaged"):
                        reasons.append("the guest is managed by Proxmox HA: remove it from HA first")
                        blocking.append("SAFE-RESTORE-002")
                    gcfg = gk.config(client, source, live.get("node") or src_node.name)
                    if gcfg.get("lock"):
                        reasons.append(f"the guest is locked ({gcfg.get('lock')}): wait for that to finish")
                        blocking.append("SAFE-RESTORE-002")
                if str(vmid) == (self_vmid_from_env() or ""):
                    reasons.append("this is the VM PyXie itself runs on: PyXie will not overwrite itself")
                    blocking.append("SAFE-RESTORE-002")
                if (confirm_name or "").strip() != (source.name or ""):
                    reasons.append(f"type the exact name of the guest ({source.name}) to confirm that its current disks and snapshots are replaced")
                    blocking.append("SAFE-RESTORE-002")
                newid = vmid
            else:
                if newid is None:
                    newid = int(client.cluster_next_id())
                elif newid in used:
                    reasons.append(f"VM ID {newid} is already used in this cluster")
                    blocking.append("SAFE-RESTORE-001")
                if any(r.get("name") == name for r in res):
                    reasons.append(f"a guest named '{name}' already exists: allowed, but confusing")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"could not check the cluster: {str(exc)[:160]}")
        blocking.append("SAFE-STORAGE-002")
        newid = source.vmid if overwrite else newid
    ctx["newid"] = newid
    try:
        la = missing_privileges(db, target, ctx["newid"] or source.vmid, ["VM.Allocate"], path=f"/vms/{ctx['newid'] or source.vmid}")
        if la:
            reasons.append(permission_reason(la, "restore guests"))
            blocking.append("SAFE-PERMISSION-001")
        if storage:
            ls = missing_privileges(db, target, 0, ["Datastore.AllocateSpace"], path=f"/storage/{storage}")
            if ls:
                reasons.append(permission_reason(ls, "use that storage"))
                blocking.append("SAFE-PERMISSION-001")
    except Exception:  # noqa: BLE001
        pass
    blocking = sorted(set(blocking))
    if not blocking:
        when = datetime.fromtimestamp(int(info.get("backup_time") or 0), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if info.get("backup_time") else "unknown time"
        v = {"ok": "verified", "failed": "FAILED verification"}.get(info.get("verified"), "not verified")
        head = (f"OVERWRITES {vm_label(source)}: its current disks and snapshots are destroyed and replaced by the backup of {when} ({v}). This cannot be undone."
                if overwrite else f"Restores the backup of {vm_label(source)} from {when} ({v}) as a NEW {'container' if kind == 'ct' else 'VM'} {ctx['newid']} '{name}' on {node_name}.")
        reasons.insert(0, head)
        if ctx["nic_count"]:
            reasons.append(f"{ctx['nic_count']} network device(s) get new hardware addresses and will be " + ("CONNECTED: it can reach the network as soon as it starts" if nics_connected else "DISCONNECTED (link down) so it cannot clash with the original; connect them when you are ready"))
        if ctx["disk_count"]:
            reasons.append(f"{ctx['disk_count']} disk(s), about {disk_bytes / GIB:.1f} GiB, restored" + (f" to {storage}" if storage else " to the storages in the backup") + "; this can take a long time (progress is in the Tasks panel)")
        if overwrite:
            reasons.append("Proxmox may keep the replaced disks as 'unused' disks of the guest, still using space: the result lists them so you can delete them from the guest's hardware settings")
        reasons.append("the guest gets the tag 'restored' and a note naming the backup" + ("; it is started afterwards" if start_after else "; it stays stopped"))
    op.context = ctx
    db.commit()
    result = {"cluster": cluster.name, "node": node_name, "vmid": ctx["newid"], "name": name, "workload_name": name, "source": vm_label(source), "mode": mode,
              "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": blocking, **info}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=blocking, actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_restore(db, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise RestoreError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "restoring", "configuring", "starting_up", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    cluster = db.query(Cluster).filter(Cluster.id == op.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    ctx = dict(op.context or {})
    node, newid, volid, mode = ctx["node"], int(ctx["newid"]), ctx["volid"], ctx["mode"]
    kind = ctx.get("guest_kind", "qemu")
    ct = kind == "lxc"
    overwrite = mode == "overwrite"
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            rc, _cred = build_pve_client(db, target)
            with rc, PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as adm:
                res = {int(r["vmid"]): r for r in rc.cluster_resources("vm") or [] if r.get("vmid") is not None}
                items = adm.backup_content(node, volid.split(":", 1)[0], int(ctx["source_vmid"]))
            point = next((i for i in items if i.get("volid") == volid), None)
            problem = None
            if point is None:
                problem = "the backup is gone"
            elif (point.get("verification") or {}).get("state") == "failed":
                problem = "the backup failed verification since the preview"
            elif overwrite and (newid not in res or res[newid].get("status") != "stopped"):
                problem = f"the guest {newid} is no longer stopped (or no longer exists)"
            elif not overwrite and newid in res:
                problem = f"VM ID {newid} was taken since the preview"
            if problem is None:
                why = node_not_ready(db, cluster.id, node) if not overwrite else None
                problem = why
            if problem:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"{problem}; nothing was restored. Preview again")
            try:
                acquire_lock(db, resource_type="cluster", resource_id=cluster.id, operation_id=op.id, reason=OP_TYPE, ttl_seconds=40000)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="restoring", stage="restoring")
        creds = load_pve_credentials(db, target, "maintenance")
        if op.status == "restoring":
            if not op.pve_upid:
                with PveMaintenanceClient(creds).for_guest(kind) as wc:
                    op.pve_upid = wc.restore_guest(node, newid, volid, storage=ctx.get("storage"), force=overwrite, pool=ctx.get("pool"))
                db.commit()
            with PveMaintenanceClient(creds) as wc:
                res = wc.wait_for_task(node, op.pve_upid, timeout=_timeout_for(ctx.get("size")))
            op.pve_upid = None
            db.commit()
            if not res.finished_ok:
                release_locks_for_operation(db, op.id)
                rc, _cred = build_pve_client(db, target)
                with rc:
                    left = any(int(r.get("vmid", -1)) == newid for r in rc.cluster_resources("vm") or [])
                return fail_operation(db, op, error=f"Proxmox reported the restore did not succeed: {res.exit_status or res.raw}." + (f" A partly restored guest {newid} may exist: check it in Proxmox (a new one can be removed with Delete)." if left else " Nothing was left behind."))
            op = enter_stage(db, op, status="configuring", stage="configuring")
        if op.status == "configuring":
            rc, _cred = build_pve_client(db, target)
            with rc:
                cfg = (rc.lxc_config(node, newid) if ct else rc.qemu_config(node, newid)) or {}
            keys: dict = {}
            name_key = "hostname" if ct else "name"
            if not overwrite and cfg.get(name_key) != ctx["name"]:
                keys[name_key] = ctx["name"]
            if not overwrite and str(cfg.get("onboot", "0")) in ("1", "true"):
                keys["onboot"] = 0  # a restored copy must not start by itself when its host boots
            for k, v in config_nets(cfg).items():
                want = net_with_link(v, bool(ctx.get("nics_connected")))
                if net_is_down(v) != net_is_down(want):
                    keys[k] = want
            tags = split_tags(cfg.get("tags"))
            if "restored" not in [t.lower() for t in tags]:
                keys["tags"] = ";".join(tags + ["restored"])
            note = f"Restored by PyXie from {volid} on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}."
            if note.split(" on ")[0] not in str(cfg.get("description") or ""):
                keys["description"] = ((str(cfg.get("description")) + "\n\n") if cfg.get("description") else "") + note
            if keys:
                with PveMaintenanceClient(creds).for_guest(kind) as wc:
                    wc.set_vm_config_keys(node, newid, keys)
            op = enter_stage(db, op, status="starting_up" if ctx["start_after"] else "verifying", stage="starting_up" if ctx["start_after"] else "verifying")
        if op.status == "starting_up":
            if not op.pve_upid:
                with PveMaintenanceClient(creds).for_guest(kind) as wc:
                    op.pve_upid = wc.start_vm(node, newid)
                db.commit()
            with PveMaintenanceClient(creds) as wc:
                res = wc.wait_for_task(node, op.pve_upid, timeout=300)
            op.pve_upid = None
            db.commit()
            if not res.finished_ok:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"Guest {newid} was restored but did not start: {res.exit_status or res.raw}")
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            rc, _cred = build_pve_client(db, target)
            with rc:
                cfg = (rc.lxc_config(node, newid) if ct else rc.qemu_config(node, newid)) or {}
                live = next((v for v in (rc.lxc_list(node) if ct else rc.qemu_list(node)) if int(v.get("vmid", -1)) == newid), None)
            problems = []
            if live is None:
                problems.append("the guest is not listed by Proxmox")
            name_key = "hostname" if ct else "name"
            if not overwrite and cfg.get(name_key) != ctx["name"]:
                problems.append(f"{name_key} is {cfg.get(name_key)}")
            nets = config_nets(cfg)
            if ctx.get("nic_count") is not None and len(nets) != ctx["nic_count"]:
                problems.append(f"{len(nets)} network device(s) found, the backup had {ctx['nic_count']}")
            for k, v in nets.items():
                if not ctx.get("nics_connected") and not net_is_down(v):
                    problems.append(f"{k} is not disconnected")
            if "restored" not in [t.lower() for t in split_tags(cfg.get("tags"))]:
                problems.append("the 'restored' tag is missing")
            ndisk = len(config_disks(cfg, "ct" if ct else "vm"))
            if ctx.get("disk_count") is not None and ndisk != ctx["disk_count"]:
                problems.append(f"{ndisk} disk(s) found, the backup had {ctx['disk_count']}")
            status = (live or {}).get("status")
            if ctx["start_after"] and status != "running":
                problems.append(f"it is {status}, not running")
            unused = sorted(k for k in cfg if k.startswith("unused"))
            op.verification_result = {"vmid": newid, "node": node, "status": status, "disks": ndisk, "nics": len(nets), "problems": problems,
                                      "unused_disks_left": {k: cfg[k] for k in unused}}
            db.commit()
            release_locks_for_operation(db, op.id)
            if problems:
                return fail_operation(db, op, error="read-back after the restore did not match: " + "; ".join(problems) + f". Guest {newid} exists in Proxmox")
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
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.IRREVERSIBLE if overwrite else rollback.MANUAL_REVERSIBLE)
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
