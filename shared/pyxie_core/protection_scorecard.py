"""Builds the Protection page's data: 3-2-1 scorecard rows, datastore cards, and read-only job info.
Read only: it reads PBS (inventory token) and Proxmox VE backup jobs and writes nothing."""

import time

from sqlalchemy.orm import Session

from .crypto import decrypt_secret
from .models import Cluster, PveTarget, ProtectionCredential, ProtectionTarget, Workload
from .pbs_client import PbsClient, PbsCredentials
from . import protection_321 as p321
from .protection_workflow import list_pbs_backup_jobs


def _pbs_targets(db: Session):
    for t in db.query(ProtectionTarget).all():
        c = db.query(ProtectionCredential).filter(
            ProtectionCredential.protection_target_id == t.id, ProtectionCredential.slot_name == "inventory").one_or_none()
        if c:
            yield t, PbsCredentials(t.hostname, t.api_port, c.token_user, c.token_id, decrypt_secret(c.encrypted_secret), t.tls_verify)


def build_scorecard(db: Session, now: float | None = None) -> dict:
    now = now or time.time()
    errors, pbs_list = [], []
    for t, creds in _pbs_targets(db):
        try:
            with PbsClient(creds) as client:
                data = p321.read_pbs(client)
            data["target"] = t.name
            data["host"] = t.hostname
            pbs_list.append(data)
        except Exception as exc:
            errors.append(f"{t.name}: could not read PBS ({type(exc).__name__})")

    stores = [dict(s, target=d["target"], host=d["host"]) for d in pbs_list for s in d["stores"]]
    last: dict = {}
    for d in pbs_list:
        for key, per in d["last"].items():
            last.setdefault(key, {}).update(per)

    store_cards = []
    for d in pbs_list:
        for s in d["stores"]:
            prune = [j for j in d["prune"] if j.get("store") == s["name"]]
            verify = [j for j in d["verify"] if j.get("store") == s["name"]]
            sync_in = [j for j in d["sync"] if j.get("store") == s["name"]]
            card = dict(s, target=d["target"], host=d["host"], pbs_url=f"https://{d['host']}:8007/#DataStore-{s['name']}", offsite=p321.is_offsite(s),
                        prune=[{"id": j["id"], "schedule": j.get("schedule"), "retention": p321.describe_retention(j),
                                "last_state": j.get("last-run-state"), "last_end": j.get("last-run-endtime"), "next": j.get("next-run")} for j in prune],
                        verify=[{"id": j["id"], "schedule": j.get("schedule"), "last_state": j.get("last-run-state"),
                                 "last_end": j.get("last-run-endtime"), "next": j.get("next-run")} for j in verify],
                        sync=[{"id": j["id"], "schedule": j.get("schedule"), "remote": j.get("remote"), "last_state": j.get("last-run-state"),
                               "last_end": j.get("last-run-endtime")} for j in sync_in])
            card["notes"] = p321.store_notes(s, prune, verify, sync_in, s["newest"], now)
            store_cards.append(card)

    jobs, covered = [], {}
    for cluster in db.query(Cluster).filter(Cluster.is_missing.is_(False)).all():
        try:
            cluster_jobs = list_pbs_backup_jobs(db, cluster)
        except Exception as exc:
            errors.append(f"{cluster.name}: could not read backup jobs ({type(exc).__name__})")
            continue
        tgt = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one_or_none()
        pve_url = f"https://{tgt.hostname}:8006/#v1:0:18:4:::::::" if tgt else None
        for j in cluster_jobs:
            jobs.append({"pve_url": pve_url, "cluster_id": str(cluster.id), "cluster_name": cluster.name, "job_id": j["job_id"], "storage": j["storage"],
                         "schedule": j["schedule"], "enabled": j["enabled"], "all_guests": j["all_guests"],
                         "member_count": sum(1 for m in j["members"] if m["included"]), "scope_node": j["scope_node"]})
            for m in j["members"]:
                if m["included"] and j["enabled"]:
                    covered.setdefault(m["workload_id"], []).append(j["job_id"])

    rows = []
    type_map = {"vm": "vm", "lxc": "ct"}
    for w in db.query(Workload).filter(Workload.is_missing.is_(False), Workload.is_template.is_(False)).order_by(Workload.name).all():
        key = (type_map.get(w.type), str(w.vmid))
        row = p321.score_guest(last.get(key, {}), stores, covered.get(str(w.id), []), now)
        row.update({"workload_id": str(w.id), "vmid": w.vmid, "name": w.name, "type": w.type, "tags": getattr(w, "tags", None)})
        rows.append(row)
    order = {"none": 0, "partial": 1, "ok": 2}
    rows.sort(key=lambda r: (order[r["status"]], r["name"] or ""))
    return {"summary": p321.summarize(rows), "rows": rows, "stores": store_cards, "jobs": jobs, "errors": errors,
            "rules": {"backup_max_age_h": p321.BACKUP_MAX_AGE_H, "offsite_max_age_h": p321.OFFSITE_MAX_AGE_H}}
