"""Container API: wizard options, create preview, and the container-template library (installed templates, Proxmox's official list, download, delete)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pyxie_core.models import Cluster

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db
from .operations import _serialize

SEG = r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,62}$"

router = APIRouter(prefix="/api/ct", tags=["containers"], dependencies=[Depends(get_current_user)])


def _cluster(db: Session, cluster_id: uuid.UUID) -> Cluster:
    c = db.query(Cluster).filter(Cluster.id == cluster_id).one_or_none()
    if c is None:
        raise HTTPException(404, "cluster not found")
    return c


@router.get("/options")
def options(cluster_id: uuid.UUID, db: Session = Depends(get_db)):
    from pyxie_core.ct_workflow import ct_options
    return ct_options(db, _cluster(db, cluster_id))


@router.get("/appliances", dependencies=[Depends(require_admin)])
def appliances(cluster_id: uuid.UUID, node: Annotated[str, Query(pattern=SEG)], db: Session = Depends(get_db)):
    from pyxie_core.ct_workflow import appliance_list
    try:
        return appliance_list(db, _cluster(db, cluster_id), node)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"could not read Proxmox's template list: {str(exc)[:200]}")


class CreateIn(BaseModel):
    cluster_id: uuid.UUID
    spec: dict


class TplDownloadIn(BaseModel):
    cluster_id: uuid.UUID
    node: str = Field(pattern=SEG)
    storage: str = Field(pattern=SEG)
    template: str


class TplDeleteIn(BaseModel):
    cluster_id: uuid.UUID
    node: str = Field(pattern=SEG)
    volid: str


@router.post("/dry-run", dependencies=[Depends(require_admin)])
def create_dry_run(p: CreateIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.ct_workflow import dry_run_ct_create
    return _serialize(dry_run_ct_create(db, _cluster(db, p.cluster_id), spec_in=p.spec, actor=user.email))


@router.post("/template-download/dry-run", dependencies=[Depends(require_admin)])
def template_download(p: TplDownloadIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.ct_workflow import dry_run_template_download
    return _serialize(dry_run_template_download(db, _cluster(db, p.cluster_id), node=p.node, storage=p.storage, template=p.template, actor=user.email))


@router.post("/template-delete/dry-run", dependencies=[Depends(require_admin)])
def template_delete(p: TplDeleteIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.ct_workflow import dry_run_template_delete
    return _serialize(dry_run_template_delete(db, _cluster(db, p.cluster_id), node=p.node, volid=p.volid, actor=user.email))
