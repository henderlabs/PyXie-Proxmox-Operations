"""Add a datastore to the Proxmox datacenter (storage.create) and remove a storage definition (storage.remove).
Types: NFS, iSCSI (no CHAP login), directory, LVM (an existing volume group, which is also how a Fibre Channel or shared iSCSI LUN is used), LVM-thin and ZFS.
CIFS/SMB and Proxmox Backup Server datastores need a password, which PyXie does not keep, so those are added in Proxmox.

Same Safety Contract as every other operation. A storage is a datacenter setting: creating one makes every host in its `nodes` list mount the share at once, removing one only
deletes the definition (the files on the share are never touched), so removal is refused while any guest still has a disk, mount point or ISO on it. NFS only: a CIFS share needs a
password, and PyXie does not keep secrets in operation records. The preview scans the NFS server through a Proxmox host to confirm the export is really offered.
"""

import ipaddress
import re
import time

from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .iso_workflow import _lock_id
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Node, Operation, PveTarget, Workload
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_permissions import friendly_error, missing_privileges, permission_reason
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient
from . import rollback

OP_CREATE, OP_REMOVE = "storage.create", "storage.remove"
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9\-_.]{0,38}[A-Za-z0-9]$")
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*$")
CONTENT = ("images", "rootdir", "iso", "vztmpl", "backup", "snippets", "import")
NFS_VERSIONS = ("3", "4", "4.1", "4.2")
TYPES = ("nfs", "iscsi", "dir", "lvm", "lvmthin", "zfspool")
BLOCK_CONTENT = ("images", "rootdir")
IQN_RE = re.compile(r"^(iqn\.[0-9]{4}-[0-9]{2}\.[a-z0-9.\-]+(:[A-Za-z0-9._:\-]+)?|eui\.[0-9A-Fa-f]{16}|naa\.[0-9A-Fa-f]{16,32})$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._\-]+(/[A-Za-z0-9._\-]+)*$")
VG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_+.\-]{0,127}$")
ZPOOL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:\-]*(/[A-Za-z0-9_.:\-]+)*$")
SCAN_KINDS = ("iscsi", "lvm", "lvmthin", "zfs")


def validate_storage(spec: dict) -> tuple[dict, list[str], list[str]]:
    """(clean spec, blocking errors, notes) for any supported datastore type. Pure."""
    kind = str(spec.get("type") or "nfs").strip()
    if kind == "nfs":
        clean, errors, notes = validate_nfs(spec)
        return {**clean, "type": "nfs"}, errors, notes
    errors, notes = [], []
    if kind not in TYPES:
        return {"type": kind}, [f"the datastore type must be one of {', '.join(TYPES)}"], notes
    sid = str(spec.get("id") or "").strip()
    if not ID_RE.match(sid):
        errors.append("the storage ID starts with a letter and uses letters, digits and - _ . (at least 2 characters)")
    nodes = [str(n).strip() for n in (spec.get("nodes") or []) if str(n).strip()]
    content = [c for c in CONTENT if c in (spec.get("content") or [])]
    clean = {"type": kind, "id": sid, "nodes": nodes, "content": content}
    allowed = ("images",) if kind == "iscsi" else BLOCK_CONTENT if kind in ("lvm", "lvmthin", "zfspool") else CONTENT
    if not content or any(c not in allowed for c in (spec.get("content") or [])):
        errors.append("choose what the datastore holds: " + ", ".join(allowed))
    if kind == "iscsi":
        portal = str(spec.get("portal") or "").strip()
        host = portal.rsplit(":", 1)[0] if portal.count(":") == 1 else portal
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not HOST_RE.match(host):
                errors.append("the portal must be an IP address or host name, optionally with :port")
        target = str(spec.get("target") or "").strip()
        if not IQN_RE.match(target):
            errors.append("the target is the iSCSI name the portal offers, for example iqn.2005-10.org.example:disk1")
        clean.update(portal=portal, target=target)
        notes.append("each LUN of the target shows up as a disk a VM can use directly; CHAP logins are not supported here (the password would have to be stored)")
    elif kind == "dir":
        path = str(spec.get("path") or "").strip()
        if not PATH_RE.match(path) or ".." in path.split("/"):
            errors.append("the path is the absolute folder on every host, for example /mnt/data")
        clean["path"] = path
        notes.append("the folder is used on every host that mounts the datastore: it must already be there (a mounted disk or share), or Proxmox creates an empty one on the host's own disk")
    elif kind in ("lvm", "lvmthin"):
        vg = str(spec.get("vgname") or "").strip()
        if not VG_RE.match(vg):
            errors.append("the volume group name uses letters, digits and _ + . -")
        clean["vgname"] = vg
        if kind == "lvmthin":
            pool = str(spec.get("thinpool") or "").strip()
            if not VG_RE.match(pool):
                errors.append("the thin pool name uses letters, digits and _ + . -")
            clean["thinpool"] = pool
        else:
            clean["shared"] = bool(spec.get("shared"))
            notes.append("tick Shared only when the volume group sits on a LUN every listed host can see (Fibre Channel or iSCSI): then guests on it can be live-migrated. The volume group itself is created once on a host, outside PyXie")
    elif kind == "zfspool":
        pool = str(spec.get("pool") or "").strip()
        if not ZPOOL_RE.match(pool):
            errors.append("the ZFS pool (or dataset) name uses letters, digits and _ . : - /")
        clean["pool"] = pool
        notes.append("the pool is local to each host that lists it; a ZFS datastore is not shared between hosts")
    if not nodes:
        notes.append("no host restriction: every host in the cluster is expected to have it" if kind in ("iscsi", "nfs") else "no host restriction: Proxmox expects it on every host, so name the hosts that really have it")
    return clean, errors, notes


def describe_storage(spec: dict) -> str:
    kind = spec.get("type", "nfs")
    where = {"nfs": f"{spec.get('server')}:{spec.get('export')}", "iscsi": f"{spec.get('portal')} target {spec.get('target')}", "dir": spec.get("path"),
             "lvm": f"volume group {spec.get('vgname')}" + (", shared" if spec.get("shared") else ""), "lvmthin": f"volume group {spec.get('vgname')}, thin pool {spec.get('thinpool')}",
             "zfspool": f"ZFS pool {spec.get('pool')}"}.get(kind)
    label = {"nfs": "NFS", "iscsi": "iSCSI", "dir": "directory", "lvm": "LVM", "lvmthin": "LVM-thin", "zfspool": "ZFS"}.get(kind, kind)
    return f"{label} datastore '{spec['id']}' ({where}) holding {', '.join(spec['content'])}"


def scan_source(wc, node: str, spec: dict) -> str | None:
    """Ask a host whether the thing the datastore points at really exists; an error sentence, or None. NFS and directory handled elsewhere/not checkable."""
    kind = spec["type"]
    try:
        if kind == "iscsi":
            found = wc._get(f"/nodes/{node}/scan/iscsi", params={"portal": spec["portal"]}) or []
            if spec["target"] not in {t.get("target") for t in found}:
                return f"{spec['portal']} does not offer the target '{spec['target']}' to {node} (it offers: {', '.join(t.get('target', '?') for t in found) or 'nothing'})"
        elif kind == "lvm":
            found = wc._get(f"/nodes/{node}/scan/lvm") or []
            if spec["vgname"] not in {v.get("vg") for v in found}:
                return f"{node} has no volume group '{spec['vgname']}' (it has: {', '.join(v.get('vg', '?') for v in found) or 'none'}). Create it on a host first"
        elif kind == "lvmthin":
            found = wc._get(f"/nodes/{node}/scan/lvmthin", params={"vg": spec["vgname"]}) or []
            if spec["thinpool"] not in {v.get("lv") for v in found}:
                return f"{node} has no thin pool '{spec['thinpool']}' in volume group '{spec['vgname']}' (it has: {', '.join(v.get('lv', '?') for v in found) or 'none'})"
        elif kind == "zfspool" and "/" not in spec["pool"]:
            found = wc._get(f"/nodes/{node}/scan/zfs") or []
            if spec["pool"] not in {v.get("pool") for v in found}:
                return f"{node} has no ZFS pool '{spec['pool']}' (it has: {', '.join(v.get('pool', '?') for v in found) or 'none'})"
    except Exception as exc:  # noqa: BLE001
        return f"{node} could not look for it: {str(exc)[:140]}"
    return None


class StorageError(Exception):
    pass


def validate_nfs(spec: dict) -> tuple[dict, list[str], list[str]]:
    """(clean spec, blocking errors, notes) for a new NFS storage. Pure."""
    errors, notes = [], []
    sid = str(spec.get("id") or "").strip()
    if not ID_RE.match(sid):
        errors.append("the storage ID starts with a letter and uses letters, digits and - _ . (at least 2 characters)")
    server = str(spec.get("server") or "").strip()
    try:
        ipaddress.ip_address(server)
    except ValueError:
        if not HOST_RE.match(server):
            errors.append("the server must be an IP address or a host name")
    export = str(spec.get("export") or "").strip()
    if not export.startswith("/") or ".." in export or any(c in export for c in " ,;\"'"):
        errors.append("the export is the path the NFS server offers, starting with /")
    content = [c for c in (spec.get("content") or []) if c]
    bad = [c for c in content if c not in CONTENT]
    if bad or not content:
        errors.append("choose what the storage holds: " + ", ".join(CONTENT) if not content else f"unknown content type: {', '.join(bad)}")
    content = [c for c in CONTENT if c in content]
    nodes = [str(n).strip() for n in (spec.get("nodes") or []) if str(n).strip()]
    version = str(spec.get("nfs_version") or "").strip() or None
    if version and version not in NFS_VERSIONS:
        errors.append(f"the NFS version must be one of {', '.join(NFS_VERSIONS)}")
    if "backup" in content and "images" in content:
        notes.append("a storage that holds both guest disks and backups is allowed, but backups on the same share as the disks they protect are not a backup if the share fails")
    if not nodes:
        notes.append("no host restriction: every host in the cluster mounts the share")
    return {"id": sid, "server": server, "export": export, "content": content, "nodes": nodes, "nfs_version": version}, errors, notes


def _target(db, cluster: Cluster) -> PveTarget:
    return db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()


def storage_references(db, cluster: Cluster, client, storage: str) -> list[dict]:
    """Guests whose configuration mentions a volume on this storage right now, read from Proxmox (disks, mount points, EFI/TPM, CD/DVD). Pure read."""
    out = []
    for w in db.query(Workload).filter(Workload.cluster_id == cluster.id, Workload.is_missing.is_(False)).all():
        node = db.query(Node).filter(Node.id == w.node_id).one()
        try:
            cfg = (client.qemu_config(node.name, w.vmid) if w.type == "vm" else client.lxc_config(node.name, w.vmid)) or {}
        except Exception:  # noqa: BLE001
            out.append({"id": str(w.id), "vmid": w.vmid, "name": w.name, "slots": ["could not be checked: its host did not answer"]})  # unknown counts as in use
            continue
        slots = [k for k, v in cfg.items() if isinstance(v, str) and v.split(",")[0].startswith(storage + ":") and not k.startswith("net")]
        if slots:
            out.append({"id": str(w.id), "vmid": w.vmid, "name": w.name, "slots": slots})
    return out


def _finish(db, op, actor, ctx, result, blocking):
    op.context = ctx
    db.commit()
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def scan_kind(db, cluster: Cluster, node: str, kind: str, **params) -> list[dict]:
    """What a host can see for one datastore type: iSCSI targets of a portal, LVM volume groups, thin pools of a group, ZFS pools."""
    if kind not in SCAN_KINDS:
        raise StorageError(f"unknown scan: {kind}")
    with PveMaintenanceClient(load_pve_credentials(db, _target(db, cluster), "maintenance")) as wc:
        return wc._get(f"/nodes/{node}/scan/{kind}", params={k: v for k, v in params.items() if v}) or []


def scan_nfs(db, cluster: Cluster, node: str, server: str) -> list[dict]:
    """The exports an NFS server offers, asked through one of the cluster's hosts (the host that would mount it)."""
    with PveMaintenanceClient(load_pve_credentials(db, _target(db, cluster), "maintenance")) as wc:
        return wc._get(f"/nodes/{node}/scan/nfs", params={"server": server}) or []


def dry_run_storage_create(db, cluster: Cluster, *, spec_in: dict, actor: str, correlation_id=None) -> Operation:
    spec, errors, notes = validate_storage(spec_in)
    target = _target(db, cluster)
    ctx = {"cluster": cluster.name, "storage": spec.get("id"), "spec": spec}
    op = create_operation(db, OP_CREATE, cluster_id=cluster.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = list(errors)
    blocking = ["SAFE-STORAGE-003"] if errors else []
    if not errors:
        try:
            online = [n for n in db.query(Node).filter(Node.cluster_id == cluster.id, Node.is_missing.is_(False)).order_by(Node.name).all() if n.status == "online"]
            chosen = [n for n in online if not spec["nodes"] or n.name in spec["nodes"]]
            unknown = [n for n in spec["nodes"] if n not in {x.name for x in online}]
            if unknown:
                reasons.append(f"not an online host of this cluster: {', '.join(unknown)}")
                blocking.append("SAFE-STORAGE-003")
            with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as wc:
                taken = {s["storage"] for s in wc._get("/storage") or []}
                if spec["id"] in taken:
                    reasons.append(f"a storage called '{spec['id']}' already exists")
                    blocking.append("SAFE-STORAGE-003")
                if chosen and spec["type"] == "nfs":
                    try:
                        exports = wc._get(f"/nodes/{chosen[0].name}/scan/nfs", params={"server": spec["server"]}) or []
                        if spec["export"] not in {e.get("path") for e in exports}:
                            reasons.append(f"{spec['server']} does not offer '{spec['export']}' to {chosen[0].name} (it offers: {', '.join(e.get('path', '?') for e in exports) or 'nothing'})")
                            blocking.append("SAFE-STORAGE-003")
                    except Exception as exc:  # noqa: BLE001
                        reasons.append(f"{chosen[0].name} could not ask {spec['server']} for its exports: {str(exc)[:140]} (unreachable server, or no NFS there)")
                        blocking.append("SAFE-STORAGE-003")
                elif chosen:
                    problem = scan_source(wc, chosen[0].name, spec)
                    if problem:
                        reasons.append(problem)
                        blocking.append("SAFE-STORAGE-003")
            lacking = missing_privileges(db, target, 0, ["Datastore.Allocate"], path=f"/storage/{spec['id']}")
            if lacking:
                reasons.append(permission_reason(lacking, "add storages"))
                blocking.append("SAFE-PERMISSION-001")
            if not blocking:
                reasons.insert(0, f"adds the {describe_storage(spec)}, used by " + (", ".join(spec["nodes"]) if spec["nodes"] else "every host in the cluster") + ". Nothing already on it is changed.")
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"could not check the cluster: {str(exc)[:160]}")
            blocking.append("SAFE-STORAGE-002")
    reasons += notes
    result = {"cluster": cluster.name, "storage": spec["id"], "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": sorted(set(blocking)), "workload_name": None}
    return _finish(db, op, actor, ctx, result, blocking)


def dry_run_storage_remove(db, cluster: Cluster, *, storage: str, confirm: str, actor: str, correlation_id=None) -> Operation:
    target = _target(db, cluster)
    ctx = {"cluster": cluster.name, "storage": storage}
    op = create_operation(db, OP_REMOVE, cluster_id=cluster.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    if (confirm or "").strip() != storage:
        reasons.append(f"type the storage's exact name ({storage}) to confirm")
        blocking.append("SAFE-STORAGE-003")
    try:
        with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as wc:
            cfg = wc._get(f"/storage/{storage}") or {}
        if not cfg:
            reasons.append(f"there is no storage '{storage}'")
            blocking.append("SAFE-STORAGE-003")
        client, _cred = build_pve_client(db, target)
        with client:
            refs = storage_references(db, cluster, client, storage) if cfg else []
        if refs:
            reasons.append("still used by " + ", ".join(f"{r['name'] or 'VM ' + str(r['vmid'])} ({', '.join(r['slots'])})" for r in refs) + ": move or remove those disks first")
            blocking.append("SAFE-STORAGE-001")
        if cfg and not blocking:
            reasons.insert(0, f"removes the storage definition '{storage}' ({cfg.get('type')}" + (f" {cfg.get('server')}:{cfg.get('export')}" if cfg.get("server") else "") + "): hosts stop mounting it. The files on the share are not deleted.")
        if cfg.get("content") and "backup" in str(cfg.get("content")):
            reasons.append("it holds backups: backup jobs that write to it will fail until they are pointed elsewhere")
        lacking = missing_privileges(db, target, 0, ["Datastore.Allocate"], path=f"/storage/{storage}")
        if lacking:
            reasons.append(permission_reason(lacking, "remove storages"))
            blocking.append("SAFE-PERMISSION-001")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"could not check the cluster: {str(exc)[:160]}")
        blocking.append("SAFE-STORAGE-002")
    result = {"cluster": cluster.name, "storage": storage, "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": sorted(set(blocking)), "workload_name": None}
    return _finish(db, op, actor, ctx, result, blocking)


def approve(db, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_storage_op(db, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise StorageError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    cluster = db.query(Cluster).filter(Cluster.id == op.cluster_id).one()
    target = _target(db, cluster)
    ctx = dict(op.context or {})
    kind, sid = op.operation_type_id, ctx["storage"]
    try:
        creds = load_pve_credentials(db, target, "maintenance")
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            with PveMaintenanceClient(creds) as wc:
                exists = sid in {s["storage"] for s in wc._get("/storage") or []}
            if (kind == OP_CREATE and exists) or (kind == OP_REMOVE and not exists):
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"the storage '{sid}' {'now exists' if exists else 'is already gone'}; nothing was changed")
            if kind == OP_REMOVE:
                rc, _cred = build_pve_client(db, target)
                with rc:
                    if storage_references(db, cluster, rc, sid):
                        release_locks_for_operation(db, op.id)
                        return block_operation(db, op, blocking_safety_rules=["SAFE-STORAGE-001"], actor="system")
            try:
                acquire_lock(db, resource_type="storage", resource_id=_lock_id(cluster, sid), operation_id=op.id, reason=kind)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")
        if op.status == "executing":
            with PveMaintenanceClient(creds) as wc:
                present = sid in {s["storage"] for s in wc._get("/storage") or []}  # a resumed run may find the change already made
                if kind == OP_CREATE and not present:
                    wc.create_storage(ctx["spec"])
                elif kind == OP_REMOVE and present:
                    wc.remove_storage(sid)
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            problems: list[str] = []
            with PveMaintenanceClient(creds) as wc:
                cfg = {s["storage"]: s for s in wc._get("/storage") or []}
            if kind == OP_REMOVE:
                if sid in cfg:
                    problems.append("Proxmox still lists the storage")
            else:
                if sid not in cfg:
                    problems.append("Proxmox does not list the new storage")
                else:
                    rc, _cred = build_pve_client(db, target)
                    want = [n.name for n in db.query(Node).filter(Node.cluster_id == cluster.id, Node.is_missing.is_(False), Node.status == "online").all() if not ctx["spec"]["nodes"] or n.name in ctx["spec"]["nodes"]]
                    inactive: list[str] = []
                    with rc:
                        for attempt in range(8):  # the hosts mount the share a moment after the definition is saved
                            inactive = [n for n in want if not any(r.get("storage") == sid and r.get("active") for r in rc.storage_list(n) or [])]
                            if not inactive:
                                break
                            time.sleep(3)
                    if inactive:
                        problems.append(f"the datastore is not active on {', '.join(inactive)} after 24 seconds: check that the share, LUN, volume group or pool is really reachable from those hosts")
            op.verification_result = {"storage": sid, "listed": sid in cfg, "problems": problems}
            db.commit()
            release_locks_for_operation(db, op.id)
            if problems:
                return fail_operation(db, op, error="; ".join(problems) + (" (the storage definition was created and is left as it is)" if kind == OP_CREATE and sid in cfg else ""))
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
