"""Restore points of one guest: every backup of it, on every backup datastore PyXie can see, with what can be restored from here.

Two sources are merged. Proxmox VE lists the backups on its Proxmox Backup Server storages (those can be restored: each has a
volume id). The backup server's own API lists everything on every datastore (verification state, offsite copies that no Proxmox
storage points at). Pure helpers first, then the reader.
"""

import re
from datetime import datetime, timezone

VOLID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*:backup/(vm|ct)/(\d+)/\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
QEMU_DISK_RE = re.compile(r"^((scsi|virtio|sata|ide)\d+|efidisk\d+|tpmstate\d+)$")
LXC_DISK_RE = re.compile(r"^(rootfs|mp\d+)$")
NET_RE = re.compile(r"^net\d+$")
_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)([KMGT]?)$")
_MULT = {"": 1, "K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}


def iso_from_ts(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_config_text(text: str) -> dict:
    """The guest configuration inside a backup (Proxmox's extractconfig text): the lines before the first [snapshot] section."""
    out: dict = {}
    for line in (text or "").splitlines():
        if line.startswith("["):
            break
        if not line.strip() or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def _size(s: str | None) -> int:
    m = _SIZE_RE.match((s or "").strip().upper())
    return int(float(m.group(1)) * _MULT[m.group(2)]) if m else 0


def config_disks(cfg: dict, kind: str) -> list[dict]:
    """[{key, storage, size}] for the guest's disks (not CD drives, not bind mounts). kind: 'vm' or 'ct'."""
    rx = LXC_DISK_RE if kind == "ct" else QEMU_DISK_RE
    out = []
    for k, v in cfg.items():
        if not rx.match(k):
            continue
        v = str(v)
        # not in the archive: a CD drive, a bind mount, or a disk marked backup=0 (Proxmox leaves those out of the backup and turns them into a comment on restore)
        if v.startswith("none") or "media=cdrom" in v or v.startswith("/") or "backup=0" in v.split(","):
            continue
        first = v.split(",")[0]
        storage = first.split(":")[0] if ":" in first else None
        size = next((p.split("=", 1)[1] for p in v.split(",")[1:] if p.startswith("size=")), None)
        out.append({"key": k, "storage": storage, "size": _size(size)})
    return out


def disks_not_in_backup(current_cfg: dict, backup_disks: list[dict], kind: str) -> list[dict]:
    """The disks the guest has now that a backup does not hold: [{key, size, storage}]. A disk is left out of a backup when it has backup=0.
    `backup_disks` is config_disks() of the configuration inside the backup. CD drives and bind mounts are not data disks. Pure."""
    rx = LXC_DISK_RE if kind == "ct" else QEMU_DISK_RE
    held = {d["key"] for d in backup_disks}
    out = []
    for k, v in (current_cfg or {}).items():
        v = str(v)
        if not rx.match(k) or k in held or v.startswith("none") or "media=cdrom" in v or v.startswith("/"):
            continue
        first = v.split(",")[0]
        size = next((p.split("=", 1)[1] for p in v.split(",")[1:] if p.startswith("size=")), None)
        out.append({"key": k, "size": _size(size), "storage": first.split(":")[0] if ":" in first else None})
    return sorted(out, key=lambda d: d["key"])


def describe_disks(disks: list[dict]) -> str:
    """'scsi0 (101 GiB), scsi1 (20 GiB)'. Pure."""
    return ", ".join(f"{d['key']} ({d['size'] / (1 << 30):g} GiB)" if d.get("size") else d["key"] for d in disks)


def config_nets(cfg: dict) -> dict:
    return {k: str(v) for k, v in cfg.items() if NET_RE.match(k)}


def net_with_link(line: str, connected: bool) -> str:
    """The same device line with link_down set (disconnected) or cleared (connected)."""
    parts = [p for p in line.split(",") if not p.startswith("link_down=")]
    if not connected:
        parts.append("link_down=1")
    return ",".join(parts)


def net_is_down(line: str) -> bool:
    return any(p.strip() == "link_down=1" for p in str(line).split(","))


def merge_points(pve_points: list[dict], pbs_points: list[dict]) -> list[dict]:
    """One list, newest first. A point seen by Proxmox is restorable (has a volid). A PBS-only point is listed with why not."""
    out: dict = {}
    for p in pve_points:
        out[(p.get("datastore") or p["storage"], p.get("namespace") or "", p["time"])] = {**p, "restorable": True, "reason": None}
    for p in pbs_points:
        key = (p["datastore"], p.get("namespace") or "", p["time"])
        if key in out:
            cur = out[key]
            cur.update({k: v for k, v in p.items() if v not in (None, "") and k not in ("volid", "storage")})
            cur["restorable"] = True
        else:
            out[key] = {**p, "volid": None, "storage": None, "restorable": False,
                        "reason": "Proxmox has no storage for this datastore" + (f" and namespace {p['namespace']}" if p.get("namespace") else "") + ", so it cannot restore from it yet"}
    return sorted(out.values(), key=lambda r: r["time"], reverse=True)


def _job_applies(job: dict, store: str, ns: str) -> bool:
    """Does a PBS prune job act on this datastore and namespace? A job at a namespace covers it and, up to its max-depth, the namespaces below it (no max-depth = all of them)."""
    if job.get("disable") or job.get("store") != store:
        return False
    jns = job.get("ns") or ""
    if jns and ns != jns and not ns.startswith(jns + "/"):
        return False
    depth = (len(ns.split("/")) if ns else 0) - (len(jns.split("/")) if jns else 0)
    md = job.get("max-depth")
    return depth == 0 or (depth > 0 and (md is None or depth <= int(md)))


def annotate_retention(points: list[dict], servers: dict, pve_jobs: list[dict], vmid: int, all_vmids: set[int]) -> None:
    """Gives every restore point a `retention`: kept (and as which rule), removed at the next prune (and by whom), protected, or unlimited (nothing prunes it).
    Several things can prune the same backups, a Proxmox job's retention and a datastore prune job, and a backup survives only if all of them keep it. Edits `points`."""
    from .backup_policy import covers
    from .prune_sim import combine, simulate
    from .retention_workflow import parse_prune
    groups: dict[tuple, list[dict]] = {}
    for p in points:
        groups.setdefault((p.get("server_id"), p["datastore"], p.get("namespace") or ""), []).append(p)
    for (sid, store, ns), pts in groups.items():
        info = servers.get(sid) or {}
        snaps = [{"time": q["time"], "protected": bool(q.get("protected"))} for q in pts]
        results, sched = [], {}
        for j in info.get("prune", []):
            keeps = {k: j[k] for k in ("keep-last", "keep-hourly", "keep-daily", "keep-weekly", "keep-monthly", "keep-yearly") if j.get(k)}
            if keeps and _job_applies(j, store, ns):
                name = f"prune job {j['id']}"
                results.append((name, simulate(snaps, keeps, info.get("offset", 0))))
                sched[name] = f"runs {j.get('schedule')}" if j.get("schedule") else "runs on its schedule"
        stores_here = {q.get("storage") for q in pts if q.get("storage")}
        for jb in pve_jobs:
            keeps = parse_prune(jb.get("prune-backups"))
            if keeps and jb.get("storage") in stores_here and covers(jb, vmid, all_vmids):
                name = f"backup job {jb.get('id')}"
                results.append((name, simulate(snaps, keeps, info.get("offset", 0))))
                sched[name] = "after each backup run"
        res = combine(results, [q["time"] for q in pts])
        for q in pts:
            r = dict(res[q["time"]])
            r["gc"] = (info.get("gc") or {}).get(store)
            r["when"] = sched.get(r.get("source") or "")
            q["retention"] = r


def read_restore_points(db, workload) -> dict:
    from .credentials import load_pve_credentials
    from .discovery import build_pve_client
    from .models import Cluster, PveTarget
    from .pve_write_client import PveMaintenanceClient
    from .pbs_client import PbsClient
    from .protection_321 import is_offsite
    from .protection_scorecard import _pbs_targets

    kind = "ct" if workload.type == "lxc" else "vm"
    cluster = db.query(Cluster).filter(Cluster.id == workload.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    errors: list[str] = []
    pve_points: list[dict] = []
    storages: dict = {}
    pve_jobs: list[dict] = []
    try:
        client, _cred = build_pve_client(db, target)
        with client, PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as adm:
            try:
                pve_jobs = client.backup_jobs() or []
            except Exception:  # noqa: BLE001
                pve_jobs = []
            from .models import Node
            node = next((n.name for n in db.query(Node).filter(Node.cluster_id == cluster.id, Node.status == "online", Node.is_missing.is_(False)).order_by(Node.name)), None)
            if node is None:
                errors.append("no online host to ask Proxmox for backups")
            else:
                for row in client.storage_list(node) or []:
                    if row.get("type") != "pbs" or "backup" not in str(row.get("content") or "") or not row.get("active", 1):
                        continue
                    name = row["storage"]
                    try:
                        sdef = adm._get(f"/storage/{name}") or {}  # the storage definition needs more than the read-only token holds
                        ds, ns_of = sdef.get("datastore"), sdef.get("namespace") or ""
                        items = adm.backup_content(node, name, workload.vmid)
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"could not list backups on {name}: {str(exc)[:100]}")
                        continue
                    storages[name] = ds
                    for it in items:
                        m = VOLID_RE.match(str(it.get("volid", "")))
                        if not m or m.group(1) != kind or int(m.group(2)) != workload.vmid:
                            continue
                        pve_points.append({"storage": name, "datastore": ds or name, "volid": it["volid"], "time": int(it.get("ctime") or 0),
                                           "size": it.get("size"), "verified": (it.get("verification") or {}).get("state"),
                                           "notes": it.get("notes"), "protected": bool(it.get("protected")), "offsite": False, "namespace": ns_of})
    except Exception as exc:  # noqa: BLE001
        errors.append(f"could not read backups from Proxmox: {str(exc)[:120]}")
    pbs_points: list[dict] = []
    servers: dict = {}
    for t, creds in _pbs_targets(db):
        try:
            with PbsClient(creds) as pc:
                info = {"prune": [], "offset": 0, "gc": {}}
                try:
                    info["prune"] = pc.job_config("prune") or []
                except Exception:  # noqa: BLE001
                    pass
                try:
                    tm = pc._get("/nodes/localhost/time") or {}
                    info["offset"] = int(tm.get("localtime", 0)) - int(tm.get("time", 0)) if tm.get("localtime") and tm.get("time") else 0
                except Exception:  # noqa: BLE001
                    pass
                try:
                    info["gc"] = {d["name"]: d.get("gc-schedule") for d in (pc._get("/config/datastore") or [])}
                except Exception:  # noqa: BLE001
                    pass
                servers[str(t.id)] = info
                for d in pc.datastores() or []:
                    name = d["store"]
                    store = {"name": name, "backend": d.get("backend-type") or "filesystem"}
                    for ns in pc.namespaces(name):
                        for s in pc.snapshots(name, kind, str(workload.vmid), ns) or []:
                            pbs_points.append({"datastore": name, "namespace": ns, "time": int(s["backup-time"]), "size": s.get("size"),
                                               "verified": (s.get("verification") or {}).get("state"), "protected": bool(s.get("protected")),
                                               "notes": s.get("comment"), "offsite": is_offsite(store), "backend": store["backend"], "server": t.name, "server_id": str(t.id)})
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{t.name}: could not read its backups ({type(exc).__name__})")
    points = merge_points(pve_points, pbs_points)
    try:
        from .models import Workload as _W
        all_vmids = {w.vmid for w in db.query(_W).filter(_W.cluster_id == workload.cluster_id, _W.is_missing.is_(False), _W.is_template.isnot(True)).all()}
        annotate_retention(points, servers, pve_jobs, workload.vmid, all_vmids)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"could not work out what pruning does to these backups ({type(exc).__name__})")
    return {"points": points, "errors": errors, "kind": kind}
