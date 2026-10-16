"""Storage setup API: what the form offers (hosts, existing storages, NFS servers already in use), an NFS export scan, and the previews for adding an NFS storage and removing one."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pyxie_core.models import Cluster, Node

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db
from .operations import _serialize

SEG = r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,62}$"

router = APIRouter(prefix="/api/storage-setup", tags=["storage-setup"], dependencies=[Depends(get_current_user)])


def _cluster(db: Session, cluster_id: uuid.UUID) -> Cluster:
    c = db.query(Cluster).filter(Cluster.id == cluster_id).one_or_none()
    if c is None:
        raise HTTPException(404, "cluster not found")
    return c


@router.get("/definitions")
def definitions(db: Session = Depends(get_db)):
    """Every datastore as Proxmox defines it (what it holds, where it lives, which hosts mount it), all clusters. Read only."""
    from pyxie_core.credentials import load_pve_credentials
    from pyxie_core.models import PveTarget
    from pyxie_core.pve_write_client import PveMaintenanceClient
    out = []
    for cluster in db.query(Cluster).filter(Cluster.is_missing.is_(False)).order_by(Cluster.name).all():
        target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one_or_none()
        if target is None:
            continue
        try:
            with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as wc:
                cfgs = wc._get("/storage") or []
        except Exception:  # noqa: BLE001 -- one unreachable cluster must not blank the page
            continue
        for s in cfgs:
            out.append({
                "cluster_id": str(cluster.id), "name": s.get("storage"), "type": s.get("type"),
                "content": [c for c in str(s.get("content") or "").split(",") if c],
                "server": s.get("server"), "export": s.get("export"), "path": s.get("path") or s.get("vgname") or s.get("pool") or s.get("portal"),
                "nodes": [n for n in str(s.get("nodes") or "").split(",") if n], "shared": bool(s.get("shared")), "disabled": bool(s.get("disable")),
            })
    return out


@router.get("/options", dependencies=[Depends(require_admin)])
def options(cluster_id: uuid.UUID, db: Session = Depends(get_db)):
    from pyxie_core.credentials import load_pve_credentials
    from pyxie_core.models import PveTarget
    from pyxie_core.pve_write_client import PveMaintenanceClient
    cluster = _cluster(db, cluster_id)
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as wc:
        cfgs = wc._get("/storage") or []
    nodes = db.query(Node).filter(Node.cluster_id == cluster.id, Node.is_missing.is_(False)).order_by(Node.name).all()
    return {
        "nodes": [{"name": n.name, "online": n.status == "online"} for n in nodes],
        "storages": [{"name": s["storage"], "type": s.get("type"), "server": s.get("server"), "export": s.get("export"), "content": s.get("content"), "nodes": s.get("nodes")} for s in cfgs],
        "servers": sorted({s["server"] for s in cfgs if s.get("type") == "nfs" and s.get("server")}),
    }


@router.get("/scan-nfs", dependencies=[Depends(require_admin)])
def scan(cluster_id: uuid.UUID, node: Annotated[str, Query(pattern=SEG)], server: str, db: Session = Depends(get_db)):
    from pyxie_core.storage_workflow import HOST_RE, scan_nfs
    if not HOST_RE.match(server):
        raise HTTPException(400, "the server must be an IP address or a host name")
    try:
        return scan_nfs(db, _cluster(db, cluster_id), node, server)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"{node} could not ask {server} for its exports: {str(exc)[:200]}")


@router.get("/scan/{kind}", dependencies=[Depends(require_admin)])
def scan_other(kind: str, cluster_id: uuid.UUID, node: Annotated[str, Query(pattern=SEG)], portal: str = "", vg: str = "", db: Session = Depends(get_db)):
    from pyxie_core.storage_workflow import HOST_RE, SCAN_KINDS, VG_RE, scan_kind
    if kind not in SCAN_KINDS:
        raise HTTPException(404, "unknown scan")
    if portal and not HOST_RE.match(portal.rsplit(":", 1)[0] if portal.count(":") == 1 else portal):
        raise HTTPException(400, "the portal must be an IP address or a host name, optionally with :port")
    if vg and not VG_RE.match(vg):
        raise HTTPException(400, "bad volume group name")
    try:
        return scan_kind(db, _cluster(db, cluster_id), node, kind, portal=portal, vg=vg)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"{node} could not look: {str(exc)[:200]}")


class CreateIn(BaseModel):
    cluster_id: uuid.UUID
    spec: dict


class RemoveIn(BaseModel):
    cluster_id: uuid.UUID
    storage: str = Field(pattern=SEG)
    confirm: str = ""


@router.post("/create/dry-run", dependencies=[Depends(require_admin)])
def create_dry_run(p: CreateIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.storage_workflow import dry_run_storage_create
    return _serialize(dry_run_storage_create(db, _cluster(db, p.cluster_id), spec_in=p.spec, actor=user.email))


@router.post("/remove/dry-run", dependencies=[Depends(require_admin)])
def remove_dry_run(p: RemoveIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.storage_workflow import dry_run_storage_remove
    return _serialize(dry_run_storage_remove(db, _cluster(db, p.cluster_id), storage=p.storage, confirm=p.confirm, actor=user.email))
