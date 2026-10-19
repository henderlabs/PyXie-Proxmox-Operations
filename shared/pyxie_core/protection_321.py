"""3-2-1 backup posture: pure scoring plus a read-only PBS reader.

3-2-1 means: 3 copies of the data (the live guest plus two backups), on 2 kinds
of storage, with 1 copy offsite. PyXie reads what Proxmox VE and Proxmox
Backup Server (PBS) already do and scores it; it changes nothing here.

A PBS datastore counts as offsite when it is S3-backed or its name says
"offsite". A copy only counts when it is recent (backup_max_age_h for local
datastores, offsite_max_age_h for offsite ones).
"""

from datetime import datetime, timezone
from typing import Optional

BACKUP_MAX_AGE_H = 48
OFFSITE_MAX_AGE_H = 24 * 8


def is_offsite(store: dict) -> bool:
    return store.get("backend") == "s3" or "offsite" in str(store.get("name", "")).lower()


def schedule_allowance_h(schedules: list) -> Optional[int]:
    """How old a copy may be before it is late, given how often the job that feeds it runs (PBS calendar text).
    monthly -> 35 days, weekly -> 9 days, anything else (daily or a time of day) -> 48 hours. None if no schedule."""
    best = None
    for sc in schedules:
        t = str(sc or "").strip().lower()
        if not t:
            continue
        h = 24 * 35 if t in ("monthly",) or t.startswith("*-*-01") or t.startswith("*-01") else (24 * 9 if t in ("weekly",) or t[:3] in ("mon", "tue", "wed", "thu", "fri", "sat", "sun") else 48)
        best = max(best or 0, h)
    return best


def _age_h(ts: Optional[int], now: float) -> Optional[float]:
    return None if not ts else max(0.0, (now - ts) / 3600)


def fmt_age(hours: Optional[float]) -> str:
    if hours is None:
        return "never"
    if hours < 48:
        return f"{int(hours)}h ago"
    return f"{int(hours // 24)}d ago"


def score_guest(last_by_store: dict, stores: list[dict], covered_by: list[str], now: float,
                backup_max_age_h: int = BACKUP_MAX_AGE_H, offsite_max_age_h: int = OFFSITE_MAX_AGE_H) -> dict:
    """last_by_store: {store_name: newest backup unix time or None}. Returns the per-guest scorecard row."""
    copies = []
    for st in stores:
        ts = last_by_store.get(st["name"])
        age = _age_h(ts, now)
        limit = (st.get("max_age_h") or offsite_max_age_h) if is_offsite(st) else backup_max_age_h
        copies.append({
            "store": st["name"], "offsite": is_offsite(st), "backend": st["backend"],
            "last": ts, "age_h": age, "fresh": age is not None and age <= limit,
            "age_text": fmt_age(age),
        })
    fresh = [c for c in copies if c["fresh"]]
    n_copies = 1 + len(fresh)
    media = {"live"} | {c["backend"] for c in fresh}
    has_offsite = any(c["offsite"] for c in fresh)
    problems = []
    if not covered_by:
        problems.append("No backup job includes it")
    # say exactly which place is late, how late, and what is allowed, instead of a count
    for c, st in zip(copies, stores):
        if c["fresh"]:
            continue
        limit = (st.get("max_age_h") or offsite_max_age_h) if is_offsite(st) else backup_max_age_h
        allowed = f"{int(limit // 24)} days" if limit >= 48 and limit % 24 == 0 else f"{int(limit)} hours"
        where = f"{c['store']}{' (offsite)' if c['offsite'] else ''}"
        problems.append(f"{where}: no backup yet" if not c["last"] else f"{where}: newest backup is {c['age_text'].replace(' ago', '')} old, should be under {allowed}")
    if fresh and len(media) < 2:
        problems.append("All backups are on the same kind of storage")
    if fresh and not has_offsite and not any(c["offsite"] for c in copies):
        problems.append("No offsite datastore has this guest")
    ok = n_copies >= 3 and len(media) >= 2 and has_offsite
    status = "ok" if ok else ("none" if not fresh else "partial")
    return {"copies": copies, "copy_count": n_copies, "media_count": len(media), "offsite": has_offsite,
            "status": status, "problems": problems, "covered_by": covered_by}


def _day(ts) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%b %-d, %Y")


def _disk_label(d: dict) -> str:
    return f"{d['slot']} ({d['size_bytes'] / (1 << 30):g} GiB)" if d.get("size_bytes") else d["slot"]


def apply_disk_coverage(row: dict, disks: list[dict], *, powered_on: bool = True, last_data: dict | None = None, accepted: set | None = None) -> dict:
    """A guest is only protected when its data disks are in a backup. `disks`: [{slot, size_bytes, backup}] (CD drives left out); a disk with
    backup=False is never saved by Proxmox. `last_data`: {slot: unix time of the newest backup that holds that disk, or None}. `accepted`: slots
    left out on purpose. Adds row["disk_coverage"] and row["notes"] (muted lines), and:
      - accepted disk: a note only;
      - powered off and an earlier backup holds the disk: a note naming the last complete backup (a stopped guest is not started for a backup);
      - otherwise a problem (running: changes since the last backup holding it are not saved; no backup holds it at all) and 'ok' becomes 'partial'. Pure."""
    last_data, accepted = last_data or {}, accepted or set()
    total = len(disks)
    left_out = [d for d in disks if d.get("backup") is False]
    row["disk_coverage"] = {"total": total, "included": total - len(left_out),
                            "excluded": [{"slot": d["slot"], "size_bytes": d.get("size_bytes"), "accepted": d["slot"] in accepted, "last_backup": last_data.get(d["slot"])} for d in left_out]}
    row.setdefault("notes", [])
    for d in left_out:
        lab, last = _disk_label(d), last_data.get(d["slot"])
        if d["slot"] in accepted:
            row["notes"].append(f"Disk {lab} is left out of backups on purpose")
        elif last and not powered_on:
            row["notes"].append(f"Powered off. Disk {lab} has backup turned off, but the last complete backup ({_day(last)}) holds it: restoring gives the guest as it was then")
        elif last:
            row["problems"].append(f"Disk {lab} has backup turned off: the newest backup that holds it is from {_day(last)}, so changes since then are not saved")
        elif len(left_out) == total:
            row["problems"].append(f"No backup holds the data of disk {lab} (backup is turned off for it): a restore gives an empty guest. Turn backup on for the disk (Hardware tab)")
        else:
            row["problems"].append(f"No backup holds the data of disk {lab} (backup is turned off for it)")
        if d["slot"] not in accepted and not (last and not powered_on) and row["status"] == "ok":
            row["status"] = "partial"
    return row


def removed_and_unknown(current_keys: set, missing: list[dict], last: dict) -> tuple[list[dict], list[dict]]:
    """Backups whose guest no longer exists. `current_keys`: {(kind, vmid)} of guests that exist. `missing`: guests PyXie knew that are gone
    ({workload_id, kind, vmid, name}); `last`: {(kind, vmid): {store: newest backup time}}. Returns (removed: gone guests PyXie knows, which can be
    restored from here; unknown: backup groups of guests PyXie never saw). An ID that a new guest reuses is that guest's, not a removed one. Pure."""
    removed, seen = [], set()
    for m in missing:
        key = (m["kind"], str(m["vmid"]))
        per = last.get(key)
        if key in current_keys or key in seen or not per:
            continue
        seen.add(key)
        removed.append({**m, "vmid": m["vmid"], "last": max(per.values()), "stores": per})
    unknown = [{"kind": k[0], "vmid": k[1], "last": max(per.values()), "stores": per}
               for k, per in sorted(last.items()) if k[0] in ("vm", "ct") and k not in current_keys and k not in seen and per
               and not any((m["kind"], str(m["vmid"])) == k for m in missing)]
    return sorted(removed, key=lambda r: -r["last"]), sorted(unknown, key=lambda r: -r["last"])


def store_notes(store: dict, prune: list[dict], verify: list[dict], sync_into: list[dict], newest: Optional[int], now: float,
                offsite_max_age_h: int = OFFSITE_MAX_AGE_H) -> list[dict]:
    """Warnings about one datastore, as {level, text}."""
    notes = []
    name = store["name"]
    if not prune:
        notes.append({"level": "warn", "text": "No prune job: old backups are never removed here"})
    for p in prune:
        if p.get("last-run-state") not in (None, "OK"):
            notes.append({"level": "bad", "text": f"Prune job {p['id']} last run: {p.get('last-run-state')}"})
    if not verify:
        notes.append({"level": "warn", "text": "No verify job: backups are never checked for corruption"})
    if store.get("gc_known") and not store.get("gc_schedule"):
        notes.append({"level": "warn", "text": "No garbage collection schedule: space from pruned backups is only freed when someone starts garbage collection by hand"
                      + (" (on S3 storage the unused data keeps costing money)" if store.get("backend") == "s3" else "")})
    for v in verify:
        if v.get("last-run-state") not in (None, "OK"):
            notes.append({"level": "bad", "text": f"Verify job {v['id']} last run: {v.get('last-run-state')}"})
    if is_offsite(store):
        limit = store.get("max_age_h") or offsite_max_age_h
        age = _age_h(newest, now)
        if age is None:
            notes.append({"level": "bad", "text": "Offsite store is empty"})
        elif age > limit:
            notes.append({"level": "bad", "text": f"Newest offsite backup is {fmt_age(age)}, later than its schedule allows ({int(limit // 24)} days)"})
        if not sync_into:
            notes.append({"level": "info", "text": "No sync job is visible to PyXie. If one feeds this store, its API token may lack permission to list it"})
    return notes


def friendly_task_status(status: str) -> str:
    """A backup server task result in words a person can act on. Pure."""
    t = str(status or "").strip().rstrip(".")
    low = t.lower()
    if "finished state is not set" in low:
        return "the backup ended without finishing (often a dropped connection, the backup server restarting, or a very busy guest)"
    if "connection" in low and ("reset" in low or "error" in low or "not connected" in low):
        return "the connection to the backup server dropped"
    if "timeout" in low or "timed out" in low:
        return "it timed out"
    if "no space" in low or "enospc" in low:
        return "the datastore ran out of space"
    return t[:140] or "it failed"


def known_causes(*, state: str | None, exempt: bool, has_plan: bool, covered: bool, failures: list[dict], now: float) -> list[dict]:
    """The reasons PyXie already knows for a guest that is not fully protected: [{"level": "warn" | "info", "text"}]. `failures`: [{"time", "status"}] of failed backup
    runs in the last 7 days, newest first. Pure."""
    import time as _t
    out: list[dict] = []
    if exempt:
        out.append({"level": "info", "text": "No backup is chosen for this guest (its plan is set to no backup)"})
    elif not has_plan and not covered:
        out.append({"level": "warn", "text": "No plan applies to this guest (no default plan is set)"})
    if state and state not in ("running", "online"):
        out.append({"level": "info", "text": f"The guest is {'powered off' if state in ('stopped', 'shutdown') else state}"})
    if failures:
        f = failures[0]
        when = _t.strftime("%b %d %H:%M", _t.localtime(f["time"]))
        out.append({"level": "warn", "text": f"{len(failures)} failed backup run{'s' if len(failures) != 1 else ''} in the last 7 days, the last on {when}: {friendly_task_status(f['status'])}"})
    return out


def summarize(rows: list[dict]) -> dict:
    out = {"ok": 0, "partial": 0, "none": 0}
    for r in rows:
        out[r["status"]] += 1
    out["total"] = len(rows)
    return out


def describe_retention(job: dict) -> str:
    parts = [f"{k.replace('keep-', '')} {job[k]}" for k in ("keep-last", "keep-hourly", "keep-daily", "keep-weekly", "keep-monthly", "keep-yearly") if job.get(k)]
    return "keep " + ", ".join(parts) if parts else "keep all"


def read_pbs(client) -> dict:
    """Read-only. Everything the scorecard needs from one PBS: datastores, newest backup per guest per datastore,
    and the prune, verify and sync jobs with their last run. Missing endpoints (permissions) degrade to empty."""
    cfg = {d["name"]: d for d in (client._get("/config/datastore") or [])}
    stores, last = [], {}
    for d in client.datastores() or []:
        name = d["store"]
        c = cfg.get(name, {})
        backend = d.get("backend-type") or ("s3" if "s3" in str(c.get("backend", "")) else "filesystem")
        newest, groups_n, snaps = None, 0, 0
        groups = []
        for ns in (client.namespaces(name) if hasattr(client, "namespaces") else [""]):  # a copy in a namespace is still a copy on this datastore
            try:
                groups += (client._get(f"/admin/datastore/{name}/groups", {"ns": ns}) if ns else client.groups(name)) or []
            except Exception:
                continue
        for g in groups:
            groups_n += 1
            snaps += int(g.get("backup-count") or 0)
            ts = g.get("last-backup")
            if g.get("backup-type") in ("vm", "ct") and ts:
                key = (g["backup-type"], str(g["backup-id"]))
                last.setdefault(key, {})[name] = max(ts, last.get(key, {}).get(name, 0))
                newest = max(newest or 0, ts)
        stores.append({"name": name, "backend": backend, "gc_schedule": c.get("gc-schedule"), "gc_known": bool(c), "path": c.get("path"), "groups": groups_n, "snapshots": snaps, "newest": newest})

    def jobs(path):
        try:
            return client._get(path) or []
        except Exception:
            return []
    return {"stores": stores, "last": last, "prune": jobs("/admin/prune"), "verify": jobs("/admin/verify"), "sync": jobs("/admin/sync")}
