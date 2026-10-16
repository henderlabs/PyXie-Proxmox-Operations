"""Restore a VM or container from a backup: its restore points, the choices the form needs, and the (previewed) restore."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pyxie_core.discovery import build_pve_client
from pyxie_core.models import Cluster, Node, Operation, PveTarget, Workload
from pyxie_core.restore_points import read_restore_points
from pyxie_core.restore_workflow import dry_run_restore

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db

router = APIRouter(prefix="/api/restore", tags=["restore"], dependencies=[Depends(get_current_user)])
LIVE = ["pending", "dry_run", "awaiting_approval", "approved", "revalidating", "restoring", "configuring", "starting_up", "verifying"]


def _workload(db: Session, workload_id) -> Workload:
    w = db.query(Workload).filter(Workload.id == workload_id).one_or_none()
    if w is None or w.type not in ("vm", "lxc"):
        raise HTTPException(404, "guest not found")
    return w


@router.get("/points/{workload_id}")
def points(workload_id: uuid.UUID, db: Session = Depends(get_db)):
    return read_restore_points(db, _workload(db, workload_id))


@router.get("/options/{workload_id}")
def options(workload_id: uuid.UUID, node: str | None = None, db: Session = Depends(get_db)):
    """What the restore form offers: online hosts, the storages of the chosen host that can hold the guest's disks, the next free ID, the pools."""
    w = _workload(db, workload_id)
    cluster = db.query(Cluster).filter(Cluster.id == w.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    nodes = db.query(Node).filter(Node.cluster_id == cluster.id, Node.is_missing.is_(False)).order_by(Node.name).all()
    own = db.query(Node).filter(Node.id == w.node_id).one()
    chosen = node or own.name
    want = "rootdir" if w.type == "lxc" else "images"
    out = {"kind": "ct" if w.type == "lxc" else "vm", "name": w.name, "node": own.name, "nodes": [{"name": n.name, "online": n.status == "online", "maintenance": bool(n.maintenance_mode)} for n in nodes],
           "storages": [], "next_id": None, "pools": [], "status": w.status}
    try:
        client, _c = build_pve_client(db, target)
        with client:
            out["next_id"] = int(client.cluster_next_id())
            out["pools"] = sorted(p.get("poolid") for p in client.pools() or [] if p.get("poolid"))
            out["storages"] = [{"name": s["storage"], "avail": s.get("avail"), "type": s.get("type")} for s in client.storage_list(chosen) or []
                               if want in str(s.get("content") or "") and s.get("active", 1)]
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"could not read the cluster: {str(exc)[:120]}"
    return out


class RestoreRequest(BaseModel):
    workload_id: uuid.UUID
    volid: str
    mode: str = "new"
    newid: int | None = None
    name: str | None = None
    node: str | None = None
    storage: str | None = None
    pool: str | None = None
    start_after: bool = False
    nics_connected: bool = False
    confirm_name: str | None = None


@router.post("/dry-run", dependencies=[Depends(require_admin)])
def dry_run(payload: RestoreRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    w = _workload(db, payload.workload_id)
    for o in db.query(Operation).filter(Operation.operation_type_id == "workload.restore", Operation.status.in_(LIVE)).all():
        if (o.context or {}).get("source_workload_id") == str(w.id):
            raise HTTPException(409, "a restore of this guest is already open: confirm or cancel it first")
    op = dry_run_restore(db, w, volid=payload.volid, mode=payload.mode, newid=payload.newid, name=payload.name, target_node=payload.node, storage=payload.storage,
                         pool=payload.pool, start_after=payload.start_after, nics_connected=payload.nics_connected, confirm_name=payload.confirm_name, actor=user.email)
    return {"id": str(op.id), "status": op.status, "dry_run_result": op.dry_run_result}
