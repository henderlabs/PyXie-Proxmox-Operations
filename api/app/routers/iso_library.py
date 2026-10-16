"""ISO library API: what ISOs each storage holds, and the previews for adding one by URL, deleting one, and switching ISO images on for a storage."""
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pyxie_core.models import Cluster

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db
from .operations import _serialize

SEG = r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,62}$"

router = APIRouter(prefix="/api/iso-library", tags=["iso-library"], dependencies=[Depends(get_current_user)])


def _cluster(db: Session, cluster_id: uuid.UUID) -> Cluster:
    c = db.query(Cluster).filter(Cluster.id == cluster_id).one_or_none()
    if c is None:
        raise HTTPException(404, "cluster not found")
    return c


@router.get("")
def library(cluster_id: uuid.UUID, db: Session = Depends(get_db)):
    from pyxie_core.iso_workflow import iso_library
    return iso_library(db, _cluster(db, cluster_id))


class DownloadIn(BaseModel):
    cluster_id: uuid.UUID
    node: str = Field(pattern=SEG)
    storage: str = Field(pattern=SEG)
    url: str
    filename: str | None = None
    checksum: str | None = None
    algorithm: str | None = None
    verify_certificates: bool = True


class DeleteIn(BaseModel):
    cluster_id: uuid.UUID
    node: str = Field(pattern=SEG)
    volid: str


class EnableIn(BaseModel):
    cluster_id: uuid.UUID
    storage: str = Field(pattern=SEG)


@router.post("/download/dry-run", dependencies=[Depends(require_admin)])
def download_dry_run(p: DownloadIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.iso_workflow import dry_run_iso_download
    return _serialize(dry_run_iso_download(db, _cluster(db, p.cluster_id), spec_in=p.model_dump(exclude={"cluster_id"}), actor=user.email))


@router.post("/delete/dry-run", dependencies=[Depends(require_admin)])
def delete_dry_run(p: DeleteIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.iso_workflow import dry_run_iso_delete
    return _serialize(dry_run_iso_delete(db, _cluster(db, p.cluster_id), volid=p.volid, node=p.node, actor=user.email))


@router.post("/enable/dry-run", dependencies=[Depends(require_admin)])
def enable_dry_run(p: EnableIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.iso_workflow import dry_run_iso_enable
    return _serialize(dry_run_iso_enable(db, _cluster(db, p.cluster_id), storage=p.storage, actor=user.email))


class UploadCheckIn(BaseModel):
    cluster_id: uuid.UUID
    node: str = Field(pattern=SEG)
    storage: str = Field(pattern=SEG)
    filename: str
    size: int
    checksum: str | None = None
    algorithm: str | None = None


@router.post("/upload/check", dependencies=[Depends(require_admin)])
def upload_check(p: UploadCheckIn, db: Session = Depends(get_db)):
    """What would happen if this file were uploaded: the same checks the upload runs first, before the browser sends a single byte."""
    from pyxie_core.iso_upload import preflight_upload
    reasons, ok = preflight_upload(db, _cluster(db, p.cluster_id), node=p.node, storage=p.storage, filename=p.filename, size=p.size, checksum=p.checksum, algorithm=p.algorithm)
    return {"ok": ok, "reasons": reasons}


@router.post("/upload", dependencies=[Depends(require_admin)])
async def upload(request: Request, cluster_id: uuid.UUID, node: str, storage: str, filename: str, checksum: str | None = None, algorithm: str | None = None,
                 user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Streams the request body (the raw file) to the Proxmox host's upload API as a multipart form; nothing is written to PyXie's disk."""
    from pyxie_core.audit import write_audit_event
    from pyxie_core.credentials import load_pve_credentials
    from pyxie_core.discovery import build_pve_client
    from pyxie_core.iso_upload import build_multipart, preflight_upload
    from pyxie_core.models import PveTarget
    from pyxie_core.pve_write_client import PveMaintenanceClient

    cluster = _cluster(db, cluster_id)
    try:
        size = int(request.headers.get("content-length", "0"))
    except ValueError:
        size = 0
    reasons, ok = await run_in_threadpool(preflight_upload, db, cluster, node=node, storage=storage, filename=filename, size=size, checksum=checksum, algorithm=algorithm)
    if not ok:
        raise HTTPException(400, "; ".join(reasons))
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    creds = load_pve_credentials(db, target, "maintenance")
    ctype, pre, post, total = build_multipart(filename, size, checksum=checksum, algorithm=algorithm)
    meta = {"node": node, "storage": storage, "filename": filename, "size": size, "checksum": bool(checksum)}
    write_audit_event(db, event_category="settings", event_type="storage.iso_upload.started", actor=user.email, actor_type="user", cluster_id=cluster.id, metadata=meta)
    cluster_pk, target_pk, actor_email = cluster.id, target.id, user.email  # read them now: after the session closes these objects are detached
    db.commit()
    db.close()  # an ISO upload can run for hours: do not hold a database connection (and an open transaction) the whole time; it is re-opened when needed
    received = 0

    async def body():
        nonlocal received
        yield pre
        async for chunk in request.stream():
            received += len(chunk)
            if received > size:
                raise ValueError("the upload is larger than it announced")
            yield chunk
        yield post

    try:
        async with httpx.AsyncClient(base_url=f"https://{creds.hostname}:{creds.api_port}/api2/json", verify=creds.tls_verify, timeout=httpx.Timeout(None, connect=15.0),
                                     headers={"Authorization": f"PVEAPIToken={creds.token_user}!{creds.token_id}={creds.token_secret}"}) as client:
            resp = await client.post(f"/nodes/{node}/storage/{storage}/upload", content=body(), headers={"Content-Type": ctype, "Content-Length": str(total)})
        if resp.status_code >= 400:
            raise RuntimeError(f"Proxmox answered {resp.status_code}: {resp.text[:300]}")
        upid = resp.json().get("data")

        def finish():
            with PveMaintenanceClient(creds) as wc:
                res = wc.wait_for_task(node, upid, timeout=3600) if upid else None
            rc, _cred = build_pve_client(db, db.query(PveTarget).filter(PveTarget.id == target_pk).one())
            with rc:
                files = {c.get("volid"): c for c in rc.storage_content(node, storage, "iso") or []}
            return res, files.get(f"{storage}:iso/{filename}")

        res, found = await run_in_threadpool(finish)
        if res is not None and not res.succeeded:
            raise RuntimeError(f"Proxmox reported the upload task failed: {res.exit_status or res.raw}")
        if not found or int(found.get("size") or 0) != size:
            raise RuntimeError(f"read-back from Proxmox shows {found.get('size') if found else 'no file'}, expected {size} bytes")
    except Exception as exc:  # noqa: BLE001
        write_audit_event(db, event_category="settings", event_type="storage.iso_upload.failed", actor=actor_email, actor_type="user", cluster_id=cluster_pk, result="failure", error=str(exc)[:300], metadata=meta)
        raise HTTPException(502, f"upload failed: {str(exc)[:300]}")
    write_audit_event(db, event_category="settings", event_type="storage.iso_upload.completed", actor=actor_email, actor_type="user", cluster_id=cluster_pk, metadata=meta)
    return {"ok": True, "volid": f"{storage}:iso/{filename}", "size": size}
