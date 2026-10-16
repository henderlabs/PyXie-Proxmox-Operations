"""ISO library (storage.iso_download / storage.iso_delete / storage.iso_enable): add an ISO to a Proxmox storage by URL, remove one, and switch ISO images on for a storage.

Same Safety Contract as every other operation: dry-run -> approve -> revalidate -> lock -> execute (a Proxmox task) -> read back -> verify -> audit.
The download is done by the Proxmox host itself (`download-url`), straight from the source to the storage; PyXie never handles the file. A checksum, when
given, is verified by Proxmox, which fails the task and removes the partial file on a mismatch. Proxmox has no rename for ISOs, so none is offered.
Deleting refuses while a VM still has the ISO attached (per PyXie's inventory, at most 30 minutes old, re-checked live at execution).
"""

import re
import uuid
from urllib.parse import unquote, urlparse

from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Node, Operation, PveTarget, Workload, WorkloadDisk
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_permissions import friendly_error, missing_privileges, permission_reason
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient, TaskTimeoutError
from . import rollback

OP_DOWNLOAD, OP_DELETE, OP_ENABLE = "storage.iso_download", "storage.iso_delete", "storage.iso_enable"
GIB = 1 << 30
ISO_CAPABLE = {"dir", "nfs", "cifs", "cephfs", "glusterfs"}  # Proxmox storage types that can hold ISO images
ALGORITHMS = {"md5": 32, "sha1": 40, "sha224": 56, "sha256": 64, "sha384": 96, "sha512": 128}
FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+()\-]{0,120}\.iso$", re.I)
STORAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,39}$")
VOLID_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]{0,39}):iso/([A-Za-z0-9][A-Za-z0-9_.+()\-]{0,120}\.iso)$", re.I)


class IsoError(Exception):
    pass


def filename_from_url(url: str) -> str | None:
    """The last path segment of a URL, URL-decoded, when it looks like an ISO name. Pure."""
    seg = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    return seg if FILENAME_RE.match(seg) else None


def validate_download(spec: dict) -> tuple[dict, list[str], list[str]]:
    """(clean spec, blocking errors, notes) for a download request. Pure."""
    errors, notes = [], []
    url = str(spec.get("url") or "").strip()
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.netloc or any(c in url for c in " \t\r\n\"'<>"):
        errors.append("the URL must be a plain http:// or https:// address")
    elif u.username or u.password:
        errors.append("do not put a user name or password in the URL: it would be stored in the task log")
    elif u.scheme == "http":
        notes.append("the URL is plain http: the download can be altered on the way. Give a checksum from the publisher to be sure it is the right file")
    filename = str(spec.get("filename") or "").strip() or filename_from_url(url) or ""
    if not FILENAME_RE.match(filename):
        errors.append("the file name must end in .iso and use letters, digits and - _ . + (it could not be taken from the URL)")
    checksum = str(spec.get("checksum") or "").strip().lower()
    algo = str(spec.get("algorithm") or "").strip().lower() or ("sha256" if checksum else "")
    if checksum:
        if algo not in ALGORITHMS:
            errors.append(f"checksum algorithm must be one of {', '.join(ALGORITHMS)}")
        elif not re.fullmatch(r"[0-9a-f]+", checksum) or len(checksum) != ALGORITHMS[algo]:
            errors.append(f"a {algo} checksum is {ALGORITHMS[algo]} hex digits")
    elif u.scheme == "https" or u.scheme == "http":
        notes.append("no checksum given: Proxmox cannot confirm the file is the one the publisher released")
    storage = str(spec.get("storage") or "").strip()
    node = str(spec.get("node") or "").strip()
    if not STORAGE_RE.match(storage):
        errors.append("choose a storage")
    if not node:
        errors.append("choose the host that downloads it")
    return {"url": url, "filename": filename, "checksum": checksum or None, "algorithm": algo or None, "storage": storage, "node": node,
            "verify_certificates": bool(spec.get("verify_certificates", True))}, errors, notes


def check_space(row: dict | None, needed_bytes: int) -> list[str]:
    """Why a storage cannot take a file of this size, from Proxmox's own storage row (10% margin). Pure."""
    if row is None:
        return ["the storage is not available on that host"]
    avail = row.get("avail")
    if avail is not None and needed_bytes * 1.10 > avail:
        return [f"storage '{row.get('storage')}' has {avail / GIB:.1f} GiB free, the ISO needs {needed_bytes / GIB:.1f} GiB plus a 10% margin"]
    return []


def parse_volid(volid: str) -> tuple[str, str] | None:
    m = VOLID_RE.match(volid or "")
    return (m.group(1), m.group(2)) if m else None


def storage_can_hold_isos(stype: str | None) -> bool:
    return (stype or "") in ISO_CAPABLE


def _target(db, cluster: Cluster) -> PveTarget:
    return db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()


def _storage_config(db, target, storage: str) -> dict:
    """The datacenter's configuration of one storage. Proxmox only lets accounts with Datastore.Allocate read it, which PyXie's read-only account lacks, so the admin token reads it."""
    with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as wc:
        return wc._get(f"/storage/{storage}") or {}


def _lock_id(cluster: Cluster, storage: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"pyxie:{cluster.id}:storage:{storage}")


def attached_to(db, cluster: Cluster, volid: str, node_id=None) -> list[dict]:
    """VMs whose CD-ROM drive holds this ISO, per PyXie's last inventory (only those on one host when the storage is local to it). Pure read."""
    rows = (db.query(WorkloadDisk, Workload).join(Workload, Workload.id == WorkloadDisk.workload_id)
            .filter(WorkloadDisk.is_cdrom.is_(True), WorkloadDisk.volume == volid, Workload.cluster_id == cluster.id, Workload.is_missing.is_(False)))
    if node_id is not None:
        rows = rows.filter(Workload.node_id == node_id)
    rows = rows.all()
    return [{"id": str(w.id), "vmid": w.vmid, "name": w.name, "slot": d.slot} for d, w in rows]


def live_holders(db, cluster: Cluster, client, volid: str) -> list[dict]:
    """VMs whose CD/DVD drive holds this ISO right now, read from Proxmox itself (not PyXie's inventory, which can be 30 minutes old)."""
    out = []
    for w in db.query(Workload).filter(Workload.cluster_id == cluster.id, Workload.is_missing.is_(False), Workload.type == "vm").all():
        node = db.query(Node).filter(Node.id == w.node_id).one()
        try:
            cfg = client.qemu_config(node.name, w.vmid) or {}
        except Exception:  # noqa: BLE001
            out.append({"id": str(w.id), "vmid": w.vmid, "name": w.name, "slot": "could not be checked: its host did not answer"})  # unknown counts as in use
            continue
        for k, v in cfg.items():
            if k.startswith(("ide", "sata", "scsi")) and str(v).startswith(volid + ",") and "media=cdrom" in str(v):
                out.append({"id": str(w.id), "vmid": w.vmid, "name": w.name, "slot": k})
    return out


def iso_library(db, cluster: Cluster) -> dict:
    """Every ISO per storage (read live from Proxmox), who holds each, and the storages that could take ISOs but have them switched off."""
    target = _target(db, cluster)
    nodes = db.query(Node).filter(Node.cluster_id == cluster.id, Node.is_missing.is_(False), Node.status == "online").order_by(Node.name).all()
    storages: dict[str, dict] = {}
    candidates: dict[str, dict] = {}
    errors: list[str] = []
    client, _cred = build_pve_client(db, target)
    with client:
        for n in nodes:
            try:
                rows = client.storage_list(n.name) or []
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{n.name}: {str(exc)[:120]}")
                continue
            for r in rows:
                name, shared, active = r["storage"], bool(r.get("shared")), bool(r.get("active", 1))
                if not active:
                    continue
                if "iso" in str(r.get("content") or ""):
                    key = name if shared else f"{name}@{n.name}"  # a storage that is local to each host is a different set of files on each one
                    s = storages.get(key)
                    if s is None:
                        s = storages[key] = {"key": key, "label": name if shared else f"{name} on {n.name}", "name": name, "type": r.get("type"), "shared": shared, "avail": r.get("avail"), "total": r.get("total"), "nodes": [], "files": []}
                    s["nodes"].append(n.name)
                    if s.get("read"):
                        continue
                    try:
                        for c in client.storage_content(n.name, name, "iso") or []:
                            vol = parse_volid(c.get("volid", ""))
                            s["files"].append({"volid": c["volid"], "name": vol[1] if vol else c["volid"], "size": c.get("size"), "attached": attached_to(db, cluster, c["volid"], None if shared else n.id)})
                        s["read"] = True  # a shared store is the same files from every host: one successful read is enough
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"{name} on {n.name}: {str(exc)[:120]}")
                elif storage_can_hold_isos(r.get("type")):
                    c = candidates.setdefault(name, {"name": name, "type": r.get("type"), "shared": shared, "nodes": []})
                    c["nodes"].append(n.name)
    for s in storages.values():
        s.pop("read", None)
        s["files"].sort(key=lambda f: f["name"].lower())
    return {"storages": sorted(storages.values(), key=lambda s: (s["name"].lower(), s["key"])), "candidates": sorted(candidates.values(), key=lambda c: c["name"].lower()), "errors": errors}


def _finish_dry_run(db, op, actor, ctx, result, blocking):
    op.context = ctx
    db.commit()
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def dry_run_iso_download(db, cluster: Cluster, *, spec_in: dict, actor: str, correlation_id=None) -> Operation:
    spec, errors, notes = validate_download(spec_in)
    target = _target(db, cluster)
    ctx = {"cluster": cluster.name, **spec}
    op = create_operation(db, OP_DOWNLOAD, cluster_id=cluster.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = list(errors)
    blocking = ["SAFE-ISO-001"] if errors else []
    size = None
    if not errors:
        try:
            client, _cred = build_pve_client(db, target)
            with client:
                rows = client.storage_list(spec["node"]) or []
                row = next((r for r in rows if r.get("storage") == spec["storage"]), None)
                if row is None or not row.get("active", 1):
                    reasons.append(f"storage '{spec['storage']}' is not active on {spec['node']}")
                    blocking.append("SAFE-ISO-001")
                elif "iso" not in str(row.get("content") or ""):
                    reasons.append(f"storage '{spec['storage']}' is not set up to hold ISO images: switch ISO images on for it first")
                    blocking.append("SAFE-ISO-001")
                else:
                    if any(c.get("volid") == f"{spec['storage']}:iso/{spec['filename']}" for c in client.storage_content(spec["node"], spec["storage"], "iso") or []):
                        reasons.append(f"{spec['storage']} already has {spec['filename']}: delete it first or choose another file name")
                        blocking.append("SAFE-ISO-001")
                try:
                    with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as wc:  # the lookup runs on the node and needs the admin token's privileges
                        meta = wc._get(f"/nodes/{spec['node']}/query-url-metadata", params={"url": spec["url"], "verify-certificates": 1 if spec["verify_certificates"] else 0}) or {}
                    size = meta.get("size")
                    if meta.get("mimetype") and "html" in str(meta["mimetype"]).lower():
                        reasons.append(f"the URL returns a web page ({meta['mimetype']}), not an ISO file")
                        blocking.append("SAFE-ISO-001")
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"{spec['node']} could not look the URL up ({str(exc)[:110]}): the size is unknown and the download may still fail")
                if size and row:
                    for p in check_space(row, int(size)):
                        reasons.append(p)
                        blocking.append("SAFE-STORAGE-002")
            lacking = missing_privileges(db, target, 0, ["Datastore.AllocateTemplate"], path=f"/storage/{spec['storage']}")
            if lacking:
                reasons.append(permission_reason(lacking, f"add ISO images to {spec['storage']}"))
                blocking.append("SAFE-PERMISSION-001")
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"could not check the cluster: {str(exc)[:160]}")
            blocking.append("SAFE-STORAGE-002")
    reasons += notes
    if not blocking:
        reasons.insert(0, f"{spec['node']} downloads {spec['filename']}" + (f" ({int(size) / GIB:.2f} GiB)" if size else "") + f" from {urlparse(spec['url']).netloc} into {spec['storage']}"
                       + (f", checked against the {spec['algorithm']} checksum" if spec["checksum"] else ""))
    result = {"cluster": cluster.name, "node": spec["node"], "storage": spec["storage"], "filename": spec["filename"], "size": size, "eligible": not blocking,
              "reasons": reasons, "blocking_safety_rules": sorted(set(blocking)), "workload_name": None}
    return _finish_dry_run(db, op, actor, ctx, result, blocking)


def dry_run_iso_delete(db, cluster: Cluster, *, volid: str, node: str, actor: str, correlation_id=None) -> Operation:
    target = _target(db, cluster)
    parsed = parse_volid(volid)
    ctx = {"cluster": cluster.name, "volid": volid, "node": node}
    op = create_operation(db, OP_DELETE, cluster_id=cluster.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    if not parsed:
        reasons.append("that is not an ISO volume (storage:iso/name.iso)")
        blocking.append("SAFE-ISO-001")
    else:
        storage = parsed[0]
        try:
            client, _cred = build_pve_client(db, target)
            with client:
                files = {c.get("volid"): c for c in client.storage_content(node, storage, "iso") or []}
                holders = live_holders(db, cluster, client, volid) if volid in files else []
            if volid not in files:
                reasons.append(f"{volid} is not on {node}'s storage '{storage}' (anymore)")
                blocking.append("SAFE-ISO-001")
            else:
                if holders:
                    reasons.append("still attached to " + ", ".join(f"{h['name'] or 'VM ' + str(h['vmid'])} ({h['slot']})" for h in holders) + ": eject it there first, or that VM would not start")
                    blocking.append("SAFE-ISO-002")
                reasons.append(f"{parsed[1]} ({(files[volid].get('size') or 0) / GIB:.2f} GiB) is deleted from {storage}. It cannot be recovered; download it again to get it back.")
            lacking = missing_privileges(db, target, 0, ["Datastore.AllocateTemplate"], path=f"/storage/{storage}")
            if lacking:
                reasons.append(permission_reason(lacking, f"delete ISO images from {storage}"))
                blocking.append("SAFE-PERMISSION-001")
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"could not check the storage: {str(exc)[:160]}")
            blocking.append("SAFE-STORAGE-002")
    result = {"cluster": cluster.name, "node": node, "volid": volid, "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": sorted(set(blocking)), "workload_name": None}
    return _finish_dry_run(db, op, actor, ctx, result, blocking)


def dry_run_iso_enable(db, cluster: Cluster, *, storage: str, actor: str, correlation_id=None) -> Operation:
    target = _target(db, cluster)
    ctx = {"cluster": cluster.name, "storage": storage}
    op = create_operation(db, OP_ENABLE, cluster_id=cluster.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    new_content = None
    if not STORAGE_RE.match(storage or ""):
        reasons.append("choose a storage")
        blocking.append("SAFE-ISO-001")
    else:
        try:
            cfg = _storage_config(db, target, storage)
            content = [c for c in str(cfg.get("content") or "").split(",") if c]
            if not storage_can_hold_isos(cfg.get("type")):
                reasons.append(f"a '{cfg.get('type')}' storage cannot hold ISO images (Proxmox allows them on directory, NFS, CIFS, CephFS and GlusterFS storages)")
                blocking.append("SAFE-ISO-001")
            elif "iso" in content:
                reasons.append(f"'{storage}' already holds ISO images")
                blocking.append("SAFE-ISO-001")
            else:
                new_content = ",".join(content + ["iso"])
                reasons.append(f"'{storage}' content types: {', '.join(content) or 'none'} -> {', '.join(content + ['iso'])}. This is a datacenter setting: every host that uses the storage can then keep ISO images on it. Nothing already stored is touched.")
            lacking = missing_privileges(db, target, 0, ["Datastore.Allocate"], path=f"/storage/{storage}")
            if lacking:
                reasons.append(permission_reason(lacking, f"change the content types of {storage}"))
                blocking.append("SAFE-PERMISSION-001")
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"could not read the storage configuration: {str(exc)[:160]}")
            blocking.append("SAFE-STORAGE-002")
    ctx["new_content"] = new_content
    result = {"cluster": cluster.name, "storage": storage, "new_content": new_content, "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": sorted(set(blocking)), "workload_name": None}
    return _finish_dry_run(db, op, actor, ctx, result, blocking)


def approve(db, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_iso_op(db, operation_id) -> Operation:
    """Resumable: a running Proxmox task is picked up again by its UPID. One executor for all three operation types."""
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise IsoError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    cluster = db.query(Cluster).filter(Cluster.id == op.cluster_id).one()
    target = _target(db, cluster)
    ctx = dict(op.context or {})
    kind = op.operation_type_id
    storage = ctx["storage"] if kind != OP_DELETE else parse_volid(ctx["volid"])[0]
    node = ctx.get("node")
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            rc, _cred = build_pve_client(db, target)
            with rc:
                if kind == OP_DELETE:
                    if ctx["volid"] not in {c.get("volid") for c in rc.storage_content(node, storage, "iso") or []}:
                        release_locks_for_operation(db, op.id)
                        return fail_operation(db, op, error=f"{ctx['volid']} is gone already; nothing was changed")
                    if live_holders(db, cluster, rc, ctx["volid"]):
                        release_locks_for_operation(db, op.id)
                        return block_operation(db, op, blocking_safety_rules=["SAFE-ISO-002"], actor="system")
                elif kind == OP_ENABLE:
                    cfg = _storage_config(db, target, storage)
                    if "iso" in str(cfg.get("content") or "").split(","):
                        release_locks_for_operation(db, op.id)
                        return fail_operation(db, op, error=f"'{storage}' already holds ISO images; nothing was changed")
                    ctx["new_content"] = ",".join([c for c in str(cfg.get("content") or "").split(",") if c] + ["iso"])
            try:
                acquire_lock(db, resource_type="storage", resource_id=_lock_id(cluster, storage), operation_id=op.id, reason=kind)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op.context = ctx
            db.commit()
            op = enter_stage(db, op, status="executing", stage="executing")
        if op.status == "executing":
            with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as wc:
                if kind == OP_ENABLE:
                    wc.set_storage_content(storage, ctx["new_content"])
                else:
                    if not op.pve_upid:
                        op.pve_upid = (wc.download_url(node, storage, ctx["url"], ctx["filename"], checksum=ctx.get("checksum"), algorithm=ctx.get("algorithm"), verify_certificates=ctx.get("verify_certificates", True))
                                       if kind == OP_DOWNLOAD else wc.delete_volume(node, storage, ctx["volid"]))
                        db.commit()
                    res = wc.wait_for_task(node, op.pve_upid, timeout=7200 if kind == OP_DOWNLOAD else 600)
                    op.pve_upid = None
                    db.commit()
                    if not res.succeeded:
                        release_locks_for_operation(db, op.id)
                        return fail_operation(db, op, error=f"Proxmox reported the {'download' if kind == OP_DOWNLOAD else 'delete'} did not succeed: {res.exit_status or res.raw}")
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            rc, _cred = build_pve_client(db, target)
            with rc:
                if kind == OP_ENABLE:
                    ok = "iso" in str(_storage_config(db, target, storage).get("content") or "").split(",")
                    op.verification_result = {"iso_enabled": ok}
                else:
                    volid = f"{storage}:iso/{ctx['filename']}" if kind == OP_DOWNLOAD else ctx["volid"]
                    files = {c.get("volid"): c for c in rc.storage_content(node, storage, "iso") or []}
                    ok = (volid in files and (files[volid].get("size") or 0) > 0) if kind == OP_DOWNLOAD else volid not in files
                    op.verification_result = {"volid": volid, "present": volid in files, "size": (files.get(volid) or {}).get("size")}
            db.commit()
            release_locks_for_operation(db, op.id)
            if not ok:
                return fail_operation(db, op, error="read-back from Proxmox did not match what was expected")
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.IRREVERSIBLE if kind == OP_DELETE else rollback.MANUAL_REVERSIBLE)
        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")
    except (MutationsDisabledError, TaskTimeoutError) as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=friendly_error(exc) if "Permission check failed" in str(exc) else f"unexpected error: {exc}")
