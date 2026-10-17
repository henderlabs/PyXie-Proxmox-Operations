"""What a prune rule would do to one guest's backups, worked out the way Proxmox Backup Server does it, so a restore point can say "kept as a daily backup" or "removed at the
next prune". Pure (no database, no network).

PBS looks at the guest's backups newest first and applies the keep rules in this order: keep-last, keep-hourly, keep-daily, keep-weekly, keep-monthly, keep-yearly. A rule keeps
the newest backup of each period (hour, day, ISO week, month, year, in the backup server's local time) until it has kept N periods; a backup that shares a period with a newer
one the rule already kept is marked for removal; protected backups are never touched; anything no rule kept is removed. A backup kept by an earlier rule does not use up a
later rule's count, which is why 14 daily plus 4 weekly keeps a little over 18 backups.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

RULES = ("keep-last", "keep-hourly", "keep-daily", "keep-weekly", "keep-monthly", "keep-yearly")
NAMES = {"keep-last": "newest", "keep-hourly": "hourly", "keep-daily": "daily", "keep-weekly": "weekly", "keep-monthly": "monthly", "keep-yearly": "yearly"}


def _period(rule: str, t: int, offset_s: int) -> str:
    d = datetime.fromtimestamp(t, tz=timezone.utc) + timedelta(seconds=offset_s)
    if rule == "keep-last":
        return str(t)
    if rule == "keep-hourly":
        return d.strftime("%Y/%m/%d/%H")
    if rule == "keep-daily":
        return d.strftime("%Y/%m/%d")
    if rule == "keep-weekly":
        iso = d.isocalendar()
        return f"{iso[0]}/{iso[1]:02d}"
    if rule == "keep-monthly":
        return d.strftime("%Y/%m")
    return d.strftime("%Y")


def simulate(snapshots: list[dict], keep: dict, offset_s: int = 0) -> dict[int, dict]:
    """`snapshots`: [{"time": unix, "protected": bool}]; `keep`: {"keep-daily": 14, ...} (rules that are zero or missing are not used).
    Returns {time: {"state": "protected" | "kept" | "removed", "by": rule name or None}}. With no rule set nothing is removed ("kept" by "nothing to prune"). Pure."""
    rules = [r for r in RULES if int(keep.get(r) or 0) > 0]
    snaps = sorted(snapshots, key=lambda s: s["time"], reverse=True)
    if not rules:
        return {s["time"]: {"state": "protected" if s.get("protected") else "kept", "by": None} for s in snaps}
    mark: dict[int, tuple[str, str | None]] = {}  # time -> ("keep", rule) | ("remove", None) | ("protected", None)
    for rule in rules:
        n = int(keep[rule])
        included: set[str] = set()
        already: set[str] = set()
        for s in snaps:
            if mark.get(s["time"], ("",))[0] == "keep":
                already.add(_period(rule, s["time"], offset_s))
        for s in snaps:
            t = s["time"]
            if t in mark:
                continue
            if s.get("protected"):
                mark[t] = ("protected", None)
                continue
            pid = _period(rule, t, offset_s)
            if pid in already:
                continue
            if pid not in included:
                if len(included) >= n:
                    break
                included.add(pid)
                mark[t] = ("keep", NAMES[rule])
            else:
                mark[t] = ("remove", None)
    out: dict[int, dict] = {}
    for s in snaps:
        m = mark.get(s["time"])
        if s.get("protected") or (m and m[0] == "protected"):
            out[s["time"]] = {"state": "protected", "by": None}
        elif m and m[0] == "keep":
            out[s["time"]] = {"state": "kept", "by": m[1]}
        else:
            out[s["time"]] = {"state": "removed", "by": None}
    return out


def combine(results: list[tuple[str, dict[int, dict]]], times: list[int]) -> dict[int, dict]:
    """Several sources prune the same backups (a Proxmox job's retention, a datastore prune job): a backup survives only if every source keeps it.
    `results`: [(source name, simulate() result)]. Returns {time: {"state", "by", "source"}} where source names who removes it (or who keeps it first). Pure."""
    out: dict[int, dict] = {}
    for t in times:
        entry = {"state": "unlimited", "by": None, "source": None}
        for name, res in results:
            r = res.get(t)
            if r is None:
                continue
            if r["state"] == "removed":
                entry = {"state": "removed", "by": None, "source": name}
                break
            if entry["state"] in ("unlimited",) or (entry["state"] == "kept" and r["state"] == "protected"):
                entry = {"state": r["state"], "by": r["by"], "source": name}
        out[t] = entry
    return out
