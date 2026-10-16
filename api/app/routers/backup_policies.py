"""Backup policies API: the policies, who they apply to, the Proxmox jobs behind them, drift, the previews, and one VM's backup picture for its Backup tab."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pyxie_core.backup_policy import covers, describe_retention, desired_membership, is_pyxie_job, plan_job_writes, resolve_assignments
from pyxie_core.backup_policy_workflow import BackupPolicyError, dry_run_policy_change, guests, load_state
from pyxie_core.credentials import load_pve_credentials
from pyxie_core.models import Cluster, Node, PveTarget, Workload
from pyxie_core.pve_write_client import PveMaintenanceClient

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db
from .operations import _serialize

router = APIRouter(prefix="/api/backup-policies", tags=["backup-policies"], dependencies=[Depends(get_current_user)])


def _cluster(db: Session, cluster_id: uuid.UUID) -> Cluster:
    c = db.query(Cluster).filter(Cluster.id == cluster_id).one_or_none()
    if c is None:
        raise HTTPException(404, "cluster not found")
    return c


def _pve(db: Session, cluster: Cluster):
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    return PveMaintenanceClient(load_pve_credentials(db, target, "maintenance"))


def _in_job_words(j: dict) -> str:
    return "every guest" if str(j.get("all")) in ("1", "True", "true") else "listed guests"


@router.get("")
def overview(cluster_id: uuid.UUID, db: Session = Depends(get_db)):
    cluster = _cluster(db, cluster_id)
    policies, assignments = load_state(db, cluster)
    gs = guests(db, cluster)
    try:
        with _pve(db, cluster) as wc:
            jobs = wc._get("/cluster/backup") or []
            stores = [{"name": s["storage"], "type": s.get("type")} for s in wc._get("/storage") or [] if "backup" in str(s.get("content") or "") and not s.get("disable")]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"could not read the backup jobs from Proxmox: {str(exc)[:200]}")
    resolved = resolve_assignments([{"id": g["id"], "tags": g["tags"]} for g in gs], assignments)
    membership = desired_membership(resolved, {g["id"]: g["vmid"] for g in gs})
    drift = plan_job_writes(policies, membership, jobs)
    all_vmids = {g["vmid"] for g in gs}
    pol_by_id = {p["id"]: p for p in policies}
    job_by_id = {j["id"]: j for j in jobs}
    out_pol = []
    for p in policies:
        j = job_by_id.get(p["job_id"])
        out_pol.append({**p, "retention": describe_retention(p), "members": membership.get(p["id"], []), "job": ({"id": j["id"], "enabled": bool(int(j.get("enabled", 1))), "next_run": j.get("next-run")} if j else None)})
    out_guests = []
    for g in gs:
        r = resolved.get(g["id"])
        others = [j["id"] for j in jobs if not is_pyxie_job(j) and covers(j, g["vmid"], all_vmids)]
        out_guests.append({**g, "policy_id": r["policy_id"] if r else None, "policy_name": (pol_by_id.get(r["policy_id"]) or {}).get("name") if r else None, "source": r["source"] if r else None,
                           "other_jobs": others})
    other_jobs = [{"id": j["id"], "storage": j.get("storage"), "schedule": j.get("schedule"), "enabled": bool(int(j.get("enabled", 1))), "covers": _in_job_words(j)} for j in jobs if not is_pyxie_job(j)]
    tags = sorted({t for g in gs for t in g["tags"]})
    return {"policies": out_pol, "assignments": assignments, "guests": out_guests, "other_jobs": other_jobs, "storages": stores, "tags": tags,
            "drift": [{"action": w["action"], "job_id": w["job_id"], "why": w["why"]} for w in drift]}


class ChangeIn(BaseModel):
    cluster_id: uuid.UUID
    change: dict
    leave_other: bool = True


@router.post("/dry-run", dependencies=[Depends(require_admin)])
def dry_run(p: ChangeIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        return _serialize(dry_run_policy_change(db, _cluster(db, p.cluster_id), change=p.change, leave_other=p.leave_other, actor=user.email))
    except (BackupPolicyError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc) or "bad request")


@router.get("/workload/{workload_id}")
def workload_backup(workload_id: uuid.UUID, db: Session = Depends(get_db)):
    w = db.query(Workload).filter(Workload.id == workload_id).one_or_none()
    if w is None:
        raise HTTPException(404, "workload not found")
    cluster = db.query(Cluster).filter(Cluster.id == w.cluster_id).one()
    node = db.query(Node).filter(Node.id == w.node_id).one()
    policies, assignments = load_state(db, cluster)
    gs = guests(db, cluster)
    resolved = resolve_assignments([{"id": g["id"], "tags": g["tags"]} for g in gs], assignments)
    r = resolved.get(str(w.id))
    pol = next((p for p in policies if r and p["id"] == r["policy_id"]), None)
    out = {"policy": ({"id": pol["id"], "name": pol["name"], "retention": describe_retention(pol), "schedule": pol["schedule"], "storage": pol["storage"], "mode": pol["mode"], "source": r["source"]} if pol else None),
           "exempt": bool(r and r["policy_id"] is None), "source": r["source"] if r else None, "jobs": [], "backups": [], "error": None, "policies": [{"id": p["id"], "name": p["name"]} for p in policies],
           "cluster_id": str(cluster.id)}
    try:
        with _pve(db, cluster) as wc:
            jobs = wc._get("/cluster/backup") or []
            all_vmids = {g["vmid"] for g in gs}
            cov = [j for j in jobs if covers(j, w.vmid, all_vmids)]
            out["jobs"] = [{"id": j["id"], "storage": j.get("storage"), "schedule": j.get("schedule"), "enabled": bool(int(j.get("enabled", 1))), "mode": j.get("mode"), "retention": j.get("prune-backups"),
                            "next_run": j.get("next-run"), "by_policy": is_pyxie_job(j), "covers": _in_job_words(j)} for j in cov]
            stores = sorted({j.get("storage") for j in cov if j.get("storage")} | ({pol["storage"]} if pol else set()))
            for s in stores:
                try:
                    items = wc._get(f"/nodes/{node.name}/storage/{s}/content", params={"content": "backup", "vmid": w.vmid}) or []
                except Exception:  # noqa: BLE001
                    items = []
                for i in items:
                    out["backups"].append({"volid": i.get("volid"), "storage": s, "ctime": i.get("ctime"), "size": i.get("size"), "notes": i.get("notes"),
                                           "verified": (i.get("verification") or {}).get("state") if isinstance(i.get("verification"), dict) else None, "protected": bool(i.get("protected"))})
            out["backups"].sort(key=lambda b: b.get("ctime") or 0, reverse=True)
            out["backups"] = out["backups"][:50]
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"could not read the backup jobs from Proxmox: {str(exc)[:160]}"
    return out
