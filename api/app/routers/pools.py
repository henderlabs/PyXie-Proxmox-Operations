"""Pools API: the cluster's pools with their members, and the previews for creating, deleting and changing a pool."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pyxie_core.models import Cluster, PveTarget

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db
from .operations import _serialize

router = APIRouter(prefix="/api/pools", tags=["pools"], dependencies=[Depends(get_current_user)])


def _cluster(db: Session, cluster_id: uuid.UUID) -> Cluster:
    c = db.query(Cluster).filter(Cluster.id == cluster_id).one_or_none()
    if c is None:
        raise HTTPException(404, "cluster not found")
    return c


@router.get("", dependencies=[Depends(require_admin)])
def list_pools(cluster_id: uuid.UUID, db: Session = Depends(get_db)):
    from pyxie_core.credentials import load_pve_credentials
    from pyxie_core.pool_workflow import read_pools
    from pyxie_core.pve_write_client import PveMaintenanceClient
    cluster = _cluster(db, cluster_id)
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    try:
        with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as wc:
            pools = read_pools(wc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"could not read the pools: {str(exc)[:200]}")
    return [{"name": k, "comment": v["comment"], "members": [{"vmid": m, "type": x["type"], "name": x.get("name")} for m, x in sorted(v["members"].items())]} for k, v in sorted(pools.items())]


class CreateIn(BaseModel):
    cluster_id: uuid.UUID
    pool: str
    comment: str = ""


class DeleteIn(BaseModel):
    cluster_id: uuid.UUID
    pool: str
    confirm: str = ""


class MembersIn(BaseModel):
    cluster_id: uuid.UUID
    pool: str
    add: list[uuid.UUID] = []
    remove: list[uuid.UUID] = []


@router.post("/create/dry-run", dependencies=[Depends(require_admin)])
def create_dry_run(p: CreateIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.pool_workflow import dry_run_pool_create
    return _serialize(dry_run_pool_create(db, _cluster(db, p.cluster_id), pool=p.pool, comment=p.comment, actor=user.email))


@router.post("/delete/dry-run", dependencies=[Depends(require_admin)])
def delete_dry_run(p: DeleteIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.pool_workflow import dry_run_pool_delete
    return _serialize(dry_run_pool_delete(db, _cluster(db, p.cluster_id), pool=p.pool, confirm=p.confirm, actor=user.email))


@router.post("/members/dry-run", dependencies=[Depends(require_admin)])
def members_dry_run(p: MembersIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.pool_workflow import dry_run_pool_members
    return _serialize(dry_run_pool_members(db, _cluster(db, p.cluster_id), pool=p.pool, add=p.add, remove=p.remove, actor=user.email))
