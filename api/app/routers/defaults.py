"""Defaults and templates API: list / create / change / delete layers, and resolve what applies to a guest (or to a cluster + tags + pool + template for the
create-VM wizard). Admin-only writes, every change in the audit log."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pyxie_core.audit import write_audit_event
from pyxie_core.defaults import BUILTIN, FIELDS, SCOPE_TYPES, resolve, validate_scope, validate_values
from pyxie_core.models import Cluster, Storage, Workload, WorkloadDefault

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db

router = APIRouter(prefix="/api/defaults", tags=["defaults"], dependencies=[Depends(get_current_user)])


def _ser(r: WorkloadDefault) -> dict:
    return {"id": str(r.id), "scope_type": r.scope_type, "scope_value": r.scope_value, "cluster_id": str(r.cluster_id) if r.cluster_id else None, "priority": r.priority,
            "description": r.description, "values": r.values or {}, "created_by": r.created_by, "updated_at": r.updated_at.isoformat() if r.updated_at else None}


def _layers(db: Session) -> list[dict]:
    return [_ser(r) for r in db.query(WorkloadDefault).all()]


def _cluster_names(db: Session) -> dict:
    return {str(c.id): c.name for c in db.query(Cluster).all()}


class DefaultIn(BaseModel):
    scope_type: str
    scope_value: str | None = None
    cluster_id: uuid.UUID | None = None
    priority: int = 0
    description: str | None = None
    values: dict


def _check(db: Session, p: DefaultIn, existing_id=None) -> dict:
    bad = validate_scope(p.scope_type, p.scope_value)
    if bad:
        raise HTTPException(400, bad)
    if p.cluster_id and not db.query(Cluster).filter(Cluster.id == p.cluster_id).first():
        raise HTTPException(400, "cluster not found")
    clean, errors = validate_values(p.values)
    if errors:
        raise HTTPException(400, "; ".join(errors))
    if not clean:
        raise HTTPException(400, "set at least one value: a default that sets nothing does nothing")
    dup = db.query(WorkloadDefault).filter(WorkloadDefault.scope_type == p.scope_type, WorkloadDefault.scope_value == ((p.scope_value or "").strip() or None),
                                           WorkloadDefault.cluster_id == p.cluster_id).first()
    if dup and dup.id != existing_id:
        raise HTTPException(409, "there is already a default for exactly this scope: edit that one")
    return clean


@router.get("")
def list_defaults(db: Session = Depends(get_db)):
    """Every stored layer, plus what the editor needs to offer: clusters, tags in use (with counts), pools in use, bridges and storages."""
    tags: dict[str, int] = {}
    pools: dict[str, int] = {}
    for tg, pool in db.query(Workload.tags, Workload.pool).filter(Workload.is_missing.is_(False)).all():
        for t in tg or []:
            tags[t] = tags.get(t, 0) + 1
        if pool:
            pools[pool] = pools.get(pool, 0) + 1
    return {
        "defaults": sorted(_layers(db), key=lambda l: (SCOPE_TYPES.index(l["scope_type"]), str(l["scope_value"] or ""))),
        "builtin": BUILTIN, "fields": {k: {"label": v[0], "group": v[1]} for k, v in FIELDS.items()},
        "clusters": [{"id": str(c.id), "name": c.name} for c in db.query(Cluster).order_by(Cluster.name).all()],
        "tags": [{"tag": t, "count": n} for t, n in sorted(tags.items(), key=lambda kv: (-kv[1], kv[0]))],
        "pools": [{"pool": p, "count": n} for p, n in sorted(pools.items())],
        "storages": sorted({s.name for s in db.query(Storage).all()}),
    }


@router.post("", dependencies=[Depends(require_admin)])
def create_default(payload: DefaultIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    clean = _check(db, payload)
    row = WorkloadDefault(scope_type=payload.scope_type, scope_value=(payload.scope_value or "").strip() or None, cluster_id=payload.cluster_id,
                          priority=payload.priority, description=payload.description, values=clean, created_by=user.email)
    db.add(row)
    db.commit()
    write_audit_event(db, event_category="settings", event_type="defaults.created", actor=user.email, actor_type="user", cluster_id=row.cluster_id,
                      state_after=_ser(row), metadata={"default_id": str(row.id)})
    return _ser(row)


@router.put("/{default_id}", dependencies=[Depends(require_admin)])
def update_default(default_id: uuid.UUID, payload: DefaultIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(WorkloadDefault).filter(WorkloadDefault.id == default_id).one_or_none()
    if row is None:
        raise HTTPException(404, "default not found")
    clean = _check(db, payload, existing_id=row.id)
    before = _ser(row)
    row.scope_type, row.scope_value, row.cluster_id = payload.scope_type, (payload.scope_value or "").strip() or None, payload.cluster_id
    row.priority, row.description, row.values = payload.priority, payload.description, clean
    db.commit()
    write_audit_event(db, event_category="settings", event_type="defaults.updated", actor=user.email, actor_type="user", cluster_id=row.cluster_id,
                      state_before=before, state_after=_ser(row), metadata={"default_id": str(row.id)})
    return _ser(row)


@router.delete("/{default_id}", dependencies=[Depends(require_admin)])
def delete_default(default_id: uuid.UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(WorkloadDefault).filter(WorkloadDefault.id == default_id).one_or_none()
    if row is None:
        raise HTTPException(404, "default not found")
    before = _ser(row)
    db.delete(row)
    db.commit()
    write_audit_event(db, event_category="settings", event_type="defaults.deleted", actor=user.email, actor_type="user", cluster_id=before["cluster_id"],
                      state_before=before, metadata={"default_id": str(default_id)})
    return {"deleted": True}


@router.get("/resolve")
def resolve_defaults(cluster_id: uuid.UUID, tags: str = "", pool: str | None = None, template: str | None = None, ostype: str | None = None, db: Session = Depends(get_db)):
    """What applies to a new VM in this cluster with these tags (comma separated), this pool and, optionally, a chosen template."""
    values, sources = resolve(_layers(db), cluster_id=str(cluster_id), tags=[t for t in tags.split(",") if t], pool=pool, template=template, ostype=ostype, cluster_names=_cluster_names(db))
    return {"values": values, "sources": sources}


@router.get("/templates")
def list_templates(db: Session = Depends(get_db)):
    return [{"name": r.scope_value, "cluster_id": str(r.cluster_id) if r.cluster_id else None, "description": r.description} for r in
            db.query(WorkloadDefault).filter(WorkloadDefault.scope_type == "template").order_by(WorkloadDefault.scope_value).all()]
