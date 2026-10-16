"""Create-VM wizard API: what the wizard can offer (read live from Proxmox) and the preview that checks a choice."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pyxie_core.models import Cluster

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db
from .operations import _serialize

router = APIRouter(prefix="/api/vm-create", tags=["vm-create"], dependencies=[Depends(get_current_user)])


def _cluster(db: Session, cluster_id: uuid.UUID) -> Cluster:
    c = db.query(Cluster).filter(Cluster.id == cluster_id).one_or_none()
    if c is None:
        raise HTTPException(404, "cluster not found")
    return c


@router.get("/options")
def options(cluster_id: uuid.UUID, db: Session = Depends(get_db)):
    from pyxie_core.vm_create_workflow import vm_create_options
    return vm_create_options(db, _cluster(db, cluster_id))


class CreateRequest(BaseModel):
    cluster_id: uuid.UUID
    spec: dict


@router.post("/dry-run", dependencies=[Depends(require_admin)])
def dry_run(payload: CreateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.vm_create_workflow import VmCreateError, dry_run_vm_create
    try:
        op = dry_run_vm_create(db, _cluster(db, payload.cluster_id), spec_in=payload.spec, actor=user.email)
    except VmCreateError as exc:
        raise HTTPException(400, str(exc))
    return _serialize(op)
