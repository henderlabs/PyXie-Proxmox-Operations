from pyxie_core import protection_321 as p

def test_allowance():
    assert p.schedule_allowance_h(["monthly"]) == 24 * 35
    assert p.schedule_allowance_h(["sun 06:00"]) == 24 * 9
    assert p.schedule_allowance_h(["05:00"]) == 48
    assert p.schedule_allowance_h([]) is None

def test_monthly_offsite_15d_is_on_track():
    NOW = 1_800_000_000
    off = {"name": "3-OFFSITE", "backend": "s3", "max_age_h": 24 * 35}
    nas = {"name": "2-NAS", "backend": "filesystem"}
    r = p.score_guest({"2-NAS": NOW - 3600, "3-OFFSITE": NOW - 15 * 86400}, [nas, off], ["j"], NOW)
    assert r["status"] == "ok"
    notes = p.store_notes(dict(off, newest=NOW - 15 * 86400), [{"id": "x"}], [{"id": "v"}], [], NOW - 15 * 86400, NOW)
    assert all(n["level"] == "info" for n in notes)
