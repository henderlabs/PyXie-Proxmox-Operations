"""Single-workload page API: DB overview plus live PVE reads.

Everything here is read-only. Live data comes through the inventory client; a
PVE failure is reported in the payload (`error`) instead of failing the request
so the page can still render what PyXie already knows.
"""
import threading
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from pyxie_core.discovery import build_pve_client
from pyxie_core.models import Cluster, Finding, Node, PveTarget, PveTask, Workload
from pyxie_core.liveness_watch import fresh_liveness
from pyxie_core.vm_liveness import PROBE_TIMEOUT_SECONDS, scan_vm_liveness
from pyxie_core.workload_detail import (
    RRD_TIMEFRAMES,
    facts_from_config,
    guest_addresses,
    live_check,
    summarize_config,
    summarize_status,
    tasks_for_vmid,
    trim_rrd,
)

from ..auth_deps import get_current_user
from ..deps import get_db
from .. import schemas

router = APIRouter(prefix="/api/workloads", tags=["workload-detail"], dependencies=[Depends(get_current_user)])


def _load(db: Session, workload_id: uuid.UUID) -> tuple[Workload, Node, Cluster]:
    w = db.query(Workload).filter(Workload.id == workload_id).one_or_none()
    if w is None:
        raise HTTPException(status_code=404, detail="Workload not found")
    node = db.query(Node).filter(Node.id == w.node_id).one_or_none()
    cluster = db.query(Cluster).filter(Cluster.id == w.cluster_id).one_or_none()
    if node is None or cluster is None:
        raise HTTPException(status_code=404, detail="Workload's host or cluster not found")
    return w, node, cluster


def _client(db: Session, cluster: Cluster):
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one_or_none()
    if target is None:
        raise RuntimeError("no PVE target for this cluster")
    return build_pve_client(db, target)


@router.get("/{workload_id}/overview")
def workload_overview(workload_id: uuid.UUID, db: Session = Depends(get_db)):
    """What PyXie already stores: the row, its host/cluster names, active findings."""
    w, node, cluster = _load(db, workload_id)
    wid = str(w.id)
    findings = []
    for f in db.query(Finding).filter(Finding.active.is_(True)).all():
        evidence = f.evidence if isinstance(f.evidence, dict) else {}
        ids = evidence.get("workload_ids") or []
        if (f.object_type == "workload" and str(f.object_id) == wid) or wid in [str(i) for i in ids]:
            findings.append({"id": str(f.id), "severity": f.severity, "category": f.category, "title": f.title})
    return {
        "workload": schemas.WorkloadOut.model_validate(w).model_dump(mode="json"),
        "node": {"id": str(node.id), "name": node.name, "maintenance_mode": bool(node.maintenance_mode)},
        "cluster": {"id": str(cluster.id), "name": cluster.name},
        "findings": findings,
    }


@router.get("/{workload_id}/live")
def workload_live(workload_id: uuid.UUID, db: Session = Depends(get_db)):
    """Live status + config straight from PVE."""
    w, node, cluster = _load(db, workload_id)
    lxc = w.type == "lxc"
    out: dict = {"status": None, "config": None, "check": None, "error": None}
    try:
        client, _cred = _client(db, cluster)
        with client:
            status = config = status_error = None
            errors = []
            t0 = time.monotonic()
            try:
                # A short timeout and no retry: a VM whose QEMU is hung must show up as "not answering",
                # not stall the page for the client's default 25 s.
                status = (
                    client.lxc_status_current(node.name, w.vmid)
                    if lxc
                    else client.qemu_status_current(node.name, w.vmid, timeout=PROBE_TIMEOUT_SECONDS, retries=0)
                )
            except Exception as e:  # noqa: BLE001 -- surfaced to the page, not hidden
                status_error = str(e)
                if not status_error.lower().lstrip().startswith("timeout"):
                    errors.append(f"status: {e}")
            elapsed = time.monotonic() - t0
            try:
                config = client.lxc_config(node.name, w.vmid) if lxc else client.qemu_config(node.name, w.vmid)
            except Exception as e:  # noqa: BLE001
                errors.append(f"config: {e}")
            out["status"] = summarize_status(status)
            out["config"] = summarize_config(config, lxc=lxc)
            out["check"] = live_check(status, w.status, elapsed, status_error, lxc=lxc)
            out["error"] = "; ".join(errors) or None
    except Exception as e:  # noqa: BLE001
        out["check"] = live_check(None, w.status, 0.0, str(e), lxc=lxc)
        out["error"] = str(e)
    return out


@router.get("/{workload_id}/rrd")
def workload_rrd(workload_id: uuid.UUID, timeframe: str = "hour", db: Session = Depends(get_db)):
    """PVE's own rrddata for this guest (the same series its Summary graphs use)."""
    if timeframe not in RRD_TIMEFRAMES:
        raise HTTPException(status_code=422, detail=f"timeframe must be one of {', '.join(RRD_TIMEFRAMES)}")
    w, node, cluster = _load(db, workload_id)
    try:
        client, _cred = _client(db, cluster)
        with client:
            rows = (
                client.lxc_rrddata(node.name, w.vmid, timeframe)
                if w.type == "lxc"
                else client.qemu_rrddata(node.name, w.vmid, timeframe)
            )
    except Exception as e:  # noqa: BLE001
        return {"timeframe": timeframe, "rows": [], "error": str(e)}
    return {"timeframe": timeframe, "rows": trim_rrd(rows), "error": None}


@router.get("/{workload_id}/ips")
def workload_ips(workload_id: uuid.UUID, db: Session = Depends(get_db)):
    """Guest-agent / container addresses. Separate and lazy: a guest whose agent is
    not answering costs a few seconds, which must not slow the rest of the page."""
    w, node, cluster = _load(db, workload_id)
    if w.status != "running":
        return {"addresses": [], "error": None}
    try:
        client, _cred = _client(db, cluster)
        with client:
            ifaces = client.lxc_interfaces(node.name, w.vmid) if w.type == "lxc" else client.qemu_agent_interfaces(node.name, w.vmid)
    except Exception as e:  # noqa: BLE001
        return {"addresses": [], "error": str(e)}
    out = guest_addresses(ifaces)
    return {"addresses": out, "error": None}


@router.get("/snapshots-summary")
def snapshots_summary(db: Session = Depends(get_db)):
    """Dashboard card: how many snapshots exist, how many VMs hold them, how old the oldest is, and the ones past the warning age
    (from the Reporting inventory's stored copy, refreshed every 30 minutes and after every snapshot action)."""
    from pyxie_core.models import WorkloadSnapshot
    from pyxie_core.reports import SNAPSHOT_CRIT_DAYS, SNAPSHOT_WARN_DAYS

    rows = (
        db.query(WorkloadSnapshot, Workload)
        .join(Workload, Workload.id == WorkloadSnapshot.workload_id)
        .filter(Workload.is_missing.is_(False))
        .all()
    )
    now = datetime.now(timezone.utc)
    items = []
    for snap, wl in rows:
        age = (now - snap.snapshot_time).days if snap.snapshot_time else None
        items.append({"workload_id": str(wl.id), "vmid": wl.vmid, "workload_name": wl.name, "snapshot": snap.name, "age_days": age,
                      "includes_ram": snap.includes_ram, "created": snap.snapshot_time.isoformat() if snap.snapshot_time else None})
    aged = [i for i in items if i["age_days"] is not None and i["age_days"] >= SNAPSHOT_WARN_DAYS]
    oldest = sorted((i for i in items if i["age_days"] is not None), key=lambda i: -i["age_days"])[:5]
    return {
        "total": len(items),
        "workloads": len({i["workload_id"] for i in items}),
        "oldest_days": oldest[0]["age_days"] if oldest else None,
        "warn_days": SNAPSHOT_WARN_DAYS,
        "crit_days": SNAPSHOT_CRIT_DAYS,
        "aged": len(aged),
        "aged_critical": len([i for i in aged if i["age_days"] >= SNAPSHOT_CRIT_DAYS]),
        "oldest": oldest,
    }


_TAG_COLOR_CACHE: dict = {}


@router.get("/tag-colors")
def tag_colors(fresh: bool = False, db: Session = Depends(get_db)):
    """Tag colors per cluster, read live from Proxmox's datacenter tag-style option (cached 15 seconds; ?fresh=1 skips the cache)."""
    from pyxie_core.tag_colors import parse_tag_style, view

    out = []
    for cluster in db.query(Cluster).all():
        key = str(cluster.id)
        hit = _TAG_COLOR_CACHE.get(key)
        if hit and not fresh and time.monotonic() - hit[0] < 15:
            out.append(hit[1])
            continue
        entry = {"cluster_id": key, "cluster": cluster.name, "colors": {}, "ordering": None, "shape": None, "error": None}
        try:
            client, _cred = _client(db, cluster)
            with client:
                raw = (client._get("/cluster/options") or {}).get("tag-style")
            entry.update(view(parse_tag_style(raw)))
        except Exception as exc:  # noqa: BLE001 -- colors are cosmetic: the page still works without them
            entry["error"] = str(exc)[:160]
        _TAG_COLOR_CACHE[key] = (time.monotonic(), entry)
        out.append(entry)
    return {"clusters": out}


@router.get("/{workload_id}/defaults")
def workload_defaults(workload_id: uuid.UUID, db: Session = Depends(get_db)):
    """The defaults that apply to this guest (its cluster, tags and pool) and where it differs from the ones somebody set on purpose."""
    from pyxie_core.defaults import deviations, resolve
    from pyxie_core.hw_compat import split_cpu
    from pyxie_core.models import WorkloadDefault
    from pyxie_core.nic_workflow import net_fields

    w, node, cluster = _load(db, workload_id)
    layers = [{"scope_type": r.scope_type, "scope_value": r.scope_value, "cluster_id": str(r.cluster_id) if r.cluster_id else None, "priority": r.priority, "values": r.values or {}}
              for r in db.query(WorkloadDefault).all()]
    values, sources = resolve(layers, cluster_id=str(cluster.id), tags=w.tags or [], pool=w.pool, ostype=w.os_type, cluster_names={str(cluster.id): cluster.name})
    out = {"values": values, "sources": sources, "deviations": [], "pool": w.pool, "error": None}
    if w.type != "vm" or w.is_missing:
        return out
    try:
        client, _cred = _client(db, cluster)
        with client:
            config = client.qemu_config(node.name, w.vmid)
        actual = {"machine": config.get("machine"), "cpu_type": split_cpu(config.get("cpu"))[0] or "kvm64", "bios": config.get("bios") or "seabios",
                  "agent": str(config.get("agent", "")).split(",", 1)[0].strip().lower() in ("1", "true", "yes") or "enabled=1" in str(config.get("agent", "")),
                  "scsihw": config.get("scsihw") or "lsi",
                  "nic_model": [net_fields(v)["model"] for k, v in sorted(config.items()) if k.startswith("net") and k[3:].isdigit()]}
        out["deviations"] = deviations(actual, values, sources)
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:200]
    return out


@router.get("/{workload_id}/nic-options")
def workload_nic_options(workload_id: uuid.UUID, db: Session = Depends(get_db)):
    """What the network-device editor offers: the bridges any host has (each with the hosts that lack it, and whether it is VLAN-aware), the NIC models,
    and the VM's devices as Proxmox has them now."""
    from pyxie_core.nic_workflow import MODELS, bridges_by_node, net_fields, next_free_slot

    w, node, cluster = _load(db, workload_id)
    out = {"supported": w.type == "vm", "models": MODELS, "bridges": [], "nics": [], "next_slot": None, "hosts": [], "error": None}
    if w.type != "vm" or w.is_missing:
        return out
    try:
        client, _cred = _client(db, cluster)
        with client:
            config = client.qemu_config(node.name, w.vmid)
        topo = bridges_by_node(db, cluster)
        out["hosts"] = sorted(topo)
        names = sorted({b for t in topo.values() for b in t})
        out["bridges"] = [{"name": b, "vlan_aware": all(bool(topo[n][b].get("vlan_aware")) for n in topo if b in topo[n]),
                           "allowed_vlans": next((topo[n][b].get("allowed_vlans") for n in topo if b in topo[n]), None),
                           "missing_on": [n for n in sorted(topo) if b not in topo[n]]} for b in names]
        out["nics"] = [{"net_id": k, **net_fields(v)} for k, v in sorted(config.items()) if k.startswith("net") and k[3:].isdigit()]
        out["next_slot"] = next_free_slot(config)
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:200]
    return out


@router.get("/{workload_id}/hw-options")
def workload_hw_options(workload_id: uuid.UUID, db: Session = Depends(get_db)):
    """What the machine/CPU type editor offers: the VM's current values, every machine and CPU type any host offers (each with the hosts
    that do NOT offer it), whether the VM can be snapshotted, and the snapshot-first default."""
    from pyxie_core.hw_compat import split_cpu
    from pyxie_core.hw_edit_workflow import read_hosts

    w, node, cluster = _load(db, workload_id)
    out = {"supported": w.type == "vm", "machine": None, "cpu": None, "machines": [], "cpus": [], "hosts": [], "can_snapshot": None, "error": None}
    if w.type != "vm" or w.is_missing:
        return out
    try:
        client, _cred = _client(db, cluster)
        with client:
            config = client.qemu_config(node.name, w.vmid)
            hosts = read_hosts(db, cluster, client, node.name)
            try:
                out["can_snapshot"] = client.qemu_feature(node.name, w.vmid, "snapshot")
            except Exception:  # noqa: BLE001
                pass
        out["machine"] = config.get("machine")
        out["cpu"] = split_cpu(config.get("cpu"))[0] or None
        names = sorted({n for h in hosts for n in (h["machines"] or [])})
        out["machines"] = [{"id": n, "missing_on": [h["name"] for h in hosts if h["machines"] is not None and n not in h["machines"]]} for n in names]
        cpu_names = sorted({n for h in hosts for n in (h["models"] or {})}, key=str.lower)
        vendor = {n: v for h in hosts for n, v in (h["models"] or {}).items()}
        out["cpus"] = [{"name": n, "vendor": vendor.get(n), "missing_on": [h["name"] for h in hosts if h["models"] is not None and n not in h["models"]]} for n in cpu_names]
        out["hosts"] = [{"name": h["name"], "is_current": h["is_current"], "cpu_model": h["cpu_model"], "readable": h["models"] is not None} for h in hosts]
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:200]
    return out


@router.get("/tags")
def known_tags(db: Session = Depends(get_db)):
    """Every tag in use across the workloads (with how many carry it), for the tag editor's suggestions."""
    counts: dict[str, int] = {}
    for (tags,) in db.query(Workload.tags).filter(Workload.is_missing.is_(False)).all():
        for t in tags or []:
            counts[t] = counts.get(t, 0) + 1
    return [{"tag": t, "count": n} for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


@router.get("/{workload_id}/snapshots")
def workload_snapshots(workload_id: uuid.UUID, db: Session = Depends(get_db)):
    """Live snapshot list straight from Proxmox (also refreshes PyXie's stored copy), plus whether Proxmox says the VM can be
    snapshotted at all."""
    from pyxie_core.snapshot_workflow import real_snapshots, refresh_stored_snapshots, suggest_name

    w, node, cluster = _load(db, workload_id)
    out = {"snapshots": [], "can_snapshot": None, "suggested_name": suggest_name(), "supported": True, "memory": w.type == "vm", "error": None}
    if w.is_missing:
        return out
    try:
        client, _cred = _client(db, cluster)
        with client:
            from pyxie_core import guest_kind as gk
            rows = gk.snapshots(client, w, node.name)
            out["can_snapshot"] = gk.can_snapshot(client, w, node.name)  # None when Proxmox could not be asked: the list still helps
        out["snapshots"] = [
            {"name": s.get("name"), "description": (s.get("description") or "").strip() or None, "snaptime": s.get("snaptime"),
             "vmstate": bool(s.get("vmstate")), "parent": s.get("parent")}
            for s in real_snapshots(rows)
        ]
        refresh_stored_snapshots(db, w, rows)
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:200]
    return out


@router.get("/{workload_id}/tasks")
def workload_tasks(workload_id: uuid.UUID, db: Session = Depends(get_db)):
    """Recent PVE tasks naming this guest, across every node it has lived on."""
    w, _node, _cluster = _load(db, workload_id)
    rows = (
        db.query(PveTask)
        .filter(PveTask.cluster_id == w.cluster_id, PveTask.upid.like(f"%:{w.vmid}:%"))
        .order_by(PveTask.started_at.desc().nullslast())
        .limit(500)
        .all()
    )
    return [
        {
            "id": str(t.id), "upid": t.upid, "task_type": t.task_type, "status": t.status, "exit_status": t.exit_status,
            "user": t.user, "node_id": str(t.node_id) if t.node_id else None,
            "started_at": t.started_at.isoformat() if t.started_at else None,
            "ended_at": t.ended_at.isoformat() if t.ended_at else None,
        }
        for t in tasks_for_vmid(rows, w.vmid)
    ]


_FACTS_TTL_SECONDS = 30
_facts_cache: dict = {"ts": 0.0, "data": None}
_facts_lock = threading.Lock()


def _build_facts(db: Session) -> dict:
    """Per-workload table facts: config-derived (Start at boot, agent, OS, disk) plus live
    uptime/lock from one cluster/resources call per cluster. About 1.5 s for 130 guests,
    which is why it is cached rather than read per request."""
    facts: dict[str, dict] = {}
    errors = 0
    workloads = db.query(Workload).filter(Workload.is_missing.is_(False)).all()
    nodes = {n.id: n for n in db.query(Node).all()}
    by_cluster: dict = {}
    for w in workloads:
        by_cluster.setdefault(w.cluster_id, []).append(w)
    for cluster_id, group in by_cluster.items():
        cluster = db.query(Cluster).filter(Cluster.id == cluster_id).one_or_none()
        if cluster is None:
            continue
        try:
            client, _cred = _client(db, cluster)
        except Exception:  # noqa: BLE001
            errors += len(group)
            continue
        with client:
            try:
                resources = {r.get("vmid"): r for r in (client.cluster_resources("vm") or [])}
            except Exception:  # noqa: BLE001
                resources = {}
            for w in group:
                node = nodes.get(w.node_id)
                res = resources.get(w.vmid, {})
                entry = {
                    "uptime": res.get("uptime"), "lock": res.get("lock"), "template": bool(res.get("template")),
                    "start_at_boot": None, "agent_enabled": None, "ostype": None, "disk_bytes": None, "storages": [], "protection": None,
                }
                if node is not None:
                    lxc = w.type == "lxc"
                    try:
                        cfg = client.lxc_config(node.name, w.vmid) if lxc else client.qemu_config(node.name, w.vmid)
                        entry.update(facts_from_config(cfg, lxc=lxc) or {})
                    except Exception:  # noqa: BLE001
                        errors += 1
                facts[str(w.id)] = entry
    return {"facts": facts, "errors": errors, "generated_at": datetime.now(timezone.utc).isoformat()}


@router.get("/facts")
def workload_facts(db: Session = Depends(get_db)):
    """Bulk per-workload facts for the Workloads table columns, cached for 30 s."""
    with _facts_lock:
        if _facts_cache["data"] is not None and time.monotonic() - _facts_cache["ts"] < _FACTS_TTL_SECONDS:
            return _facts_cache["data"]
        data = _build_facts(db)
        _facts_cache.update(ts=time.monotonic(), data=data)
        return data


_LIVENESS_TTL_SECONDS = 60
_liveness_cache: dict = {"ts": 0.0, "data": None}
_liveness_lock = threading.Lock()


def _build_liveness(db: Session) -> dict:
    """One timed status call per running VM, cluster by cluster. About 0.06 s each when healthy
    (6 s for the 116 VMs on CRE), which is why it is cached."""
    out: dict[str, dict] = {}
    workloads = (
        db.query(Workload)
        .filter(Workload.is_missing.is_(False), Workload.type == "vm", Workload.status == "running")
        .all()
    )
    nodes = {n.id: n for n in db.query(Node).all()}
    by_cluster: dict = {}
    for w in workloads:
        by_cluster.setdefault(w.cluster_id, []).append(w)
    for cluster_id, group in by_cluster.items():
        cluster = db.query(Cluster).filter(Cluster.id == cluster_id).one_or_none()
        if cluster is None:
            continue
        try:
            client, _cred = _client(db, cluster)
        except Exception:  # noqa: BLE001
            continue
        guests = [(nodes[w.node_id].name, w.vmid) for w in group if w.node_id in nodes]
        with client:
            results = scan_vm_liveness(client, guests)
        for w in group:
            if w.vmid in results:
                out[str(w.id)] = results[w.vmid]
    return {"liveness": out, "generated_at": datetime.now(timezone.utc).isoformat()}


@router.get("/liveness")
def workloads_liveness(db: Session = Depends(get_db)):
    """Is each running VM's QEMU answering? For the Workloads table's Live Check column.

    Served from the worker's background watch (results under 3 minutes old). Only if that is not
    running (most VMs have no fresh result) does it fall back to probing now, cached 60 s."""
    running = db.query(Workload).filter(Workload.is_missing.is_(False), Workload.type == "vm", Workload.status == "running").count()
    fresh = fresh_liveness(db)
    if running == 0 or len(fresh) >= 0.5 * running:
        return {"liveness": fresh, "generated_at": datetime.now(timezone.utc).isoformat(), "source": "watch"}
    with _liveness_lock:
        if _liveness_cache["data"] is not None and time.monotonic() - _liveness_cache["ts"] < _LIVENESS_TTL_SECONDS:
            return _liveness_cache["data"]
        data = _build_liveness(db)
        _liveness_cache.update(ts=time.monotonic(), data=data)
        return data
