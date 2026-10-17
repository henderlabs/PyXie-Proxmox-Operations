from datetime import datetime, timezone

from pyxie_core.prune_sim import combine, simulate


def ts(y, m, d, h=2):
    return int(datetime(y, m, d, h, tzinfo=timezone.utc).timestamp())


def nightly(n, start=(2026, 10, 16)):
    base = ts(*start)
    return [{"time": base - i * 86400, "protected": False} for i in range(n)]


def test_14_daily_4_weekly_keeps_a_little_over_18():
    snaps = nightly(60)
    res = simulate(snaps, {"keep-daily": 14, "keep-weekly": 4})
    kept = [t for t, r in res.items() if r["state"] == "kept"]
    daily = [t for t, r in res.items() if r["by"] == "daily"]
    weekly = [t for t, r in res.items() if r["by"] == "weekly"]
    assert len(daily) == 14 and 3 <= len(weekly) <= 4 and 17 <= len(kept) <= 18  # the two overlap a little
    newest = max(res)
    assert res[newest]["by"] == "daily" and res[min(res)]["state"] == "removed"
    # every one of the 14 newest days is kept
    assert all(res[s["time"]]["state"] == "kept" for s in snaps[:14])


def test_protected_and_no_rules():
    snaps = nightly(5)
    snaps[4]["protected"] = True
    res = simulate(snaps, {"keep-daily": 2})
    assert res[snaps[4]["time"]]["state"] == "protected"
    assert res[snaps[0]["time"]]["state"] == "kept" and res[snaps[1]["time"]]["state"] == "kept" and res[snaps[2]["time"]]["state"] == "removed"
    assert all(r["state"] == "kept" for r in simulate(nightly(3), {}).values())  # no limits: nothing is removed


def test_two_a_day_only_the_newest_counts_as_daily():
    base = ts(2026, 10, 16, 3)
    snaps = [{"time": base, "protected": False}, {"time": base - 3600, "protected": False}, {"time": base - 86400, "protected": False}]
    res = simulate(snaps, {"keep-daily": 5})
    assert res[base]["state"] == "kept" and res[base - 3600]["state"] == "removed" and res[base - 86400]["state"] == "kept"


def test_local_time_decides_the_day():
    t = ts(2026, 10, 16, 1)  # 01:00 UTC is still the 15th in UTC-5
    a = {"time": t, "protected": False}
    b = {"time": t - 3600 * 2, "protected": False}  # 23:00 UTC on the 15th
    assert simulate([a, b], {"keep-daily": 5}, 0)[t]["state"] == "kept" and simulate([a, b], {"keep-daily": 5}, 0)[b["time"]]["state"] == "kept"
    assert simulate([a, b], {"keep-daily": 5}, -5 * 3600)[b["time"]]["state"] == "removed"  # both are the 15th locally: only the newest is kept


def test_combine_a_backup_survives_only_if_every_source_keeps_it():
    snaps = nightly(30)
    job = simulate(snaps, {"keep-daily": 14, "keep-weekly": 4})
    ds = simulate(snaps, {"keep-daily": 21, "keep-weekly": 8, "keep-monthly": 6})
    both = combine([("job pyxie-standard", job), ("prune-archive", ds)], [s["time"] for s in snaps])
    assert both[snaps[0]["time"]]["state"] == "kept"
    old = min(s["time"] for s in snaps)
    assert both[old]["state"] == "removed" and both[old]["source"] == "job pyxie-standard"
    assert combine([], [1])[1]["state"] == "unlimited"


def test_annotate_restore_points_with_job_and_prune_job():
    from pyxie_core.restore_points import annotate_retention
    base = ts(2026, 10, 16)
    pts = [{"server_id": "s1", "datastore": "nas", "namespace": "", "storage": "st-pbs", "time": base - i * 86400, "protected": i == 20} for i in range(30)]
    servers = {"s1": {"prune": [{"id": "prune-archive", "store": "nas", "keep-daily": 21, "keep-weekly": 8, "schedule": "05:00"}, {"id": "other", "store": "elsewhere", "keep-daily": 1}], "offset": 0, "gc": {"nas": "sat 18:15"}}}
    jobs = [{"id": "pyxie-standard", "storage": "st-pbs", "all": 1, "prune-backups": {"keep-daily": "14", "keep-weekly": "4"}}]
    annotate_retention(pts, servers, jobs, 101, {101})
    newest, day20, old = pts[0], pts[20], pts[29]
    assert newest["retention"]["state"] == "kept" and newest["retention"]["by"] == "daily"
    assert day20["retention"]["state"] == "protected"            # protected backups are never pruned
    assert pts[16]["retention"]["state"] in ("kept", "removed")  # decided by the stricter source
    assert old["retention"]["state"] == "removed" and old["retention"]["source"] in ("backup job pyxie-standard", "prune job prune-archive") and old["retention"]["gc"] == "sat 18:15"
    assert pts[15]["retention"]["state"] == "removed" and pts[15]["retention"]["when"] in ("after each backup run", "runs 05:00")
    # no rule anywhere: nothing prunes it
    free = [{"server_id": "s9", "datastore": "x", "namespace": "", "time": base, "protected": False}]
    annotate_retention(free, {}, [], 5, {5})
    assert free[0]["retention"]["state"] == "unlimited"


def test_known_causes_and_friendly_task_status():
    from pyxie_core.protection_321 import friendly_task_status, known_causes
    assert "dropped connection" in friendly_task_status("backup ended but finished state is not set.")
    assert "connection to the backup server dropped" in friendly_task_status("connection error: not connected")
    c = known_causes(state="stopped", exempt=False, has_plan=True, covered=True, failures=[{"time": 1760000000, "status": "backup ended but finished state is not set."}] * 3, now=1760100000)
    assert [x["level"] for x in c] == ["info", "warn"] and "powered off" in c[0]["text"] and "3 failed backup runs" in c[1]["text"]
    assert known_causes(state="running", exempt=True, has_plan=True, covered=False, failures=[], now=0)[0]["text"].startswith("No backup is chosen")
    assert "No plan applies" in known_causes(state="running", exempt=False, has_plan=False, covered=False, failures=[], now=0)[0]["text"]
    assert known_causes(state="running", exempt=False, has_plan=True, covered=True, failures=[], now=0) == []
