from pyxie_core import protection_321 as p
from pyxie_core.pbs_datastore_workflow import validate_snapshot
from pyxie_core.pbs_storage_workflow import default_storage_id


def test_snapshot_selection_is_validated():
    ok, errs = validate_snapshot({"backup_type": "vm", "backup_id": "101", "backup_time": 1780905602})
    assert not errs and ok == {"backup-type": "vm", "backup-id": "101", "backup-time": 1780905602}
    assert validate_snapshot({"backup_time": 5})[1]
    assert validate_snapshot({"backup_type": "vm", "backup_time": 5})[1]
    assert validate_snapshot({"backup_type": "bad"})[1]
    assert validate_snapshot({})[1] == [] and validate_snapshot({})[0] == {}


def test_no_garbage_collection_schedule_is_a_warning_and_mentions_s3_cost():
    store = {"name": "3-OFFSITE", "backend": "s3", "gc_known": True, "gc_schedule": None}
    notes = p.store_notes(store, [{"id": "x"}], [{"id": "v"}], [], 1, 10)
    assert any("garbage collection" in n["text"].lower() and "money" in n["text"] for n in notes)
    store2 = {"name": "NAS", "backend": "filesystem", "gc_known": True, "gc_schedule": "sat 18:15"}
    assert not any("garbage" in n["text"].lower() for n in p.store_notes(store2, [{"id": "x"}], [{"id": "v"}], [], 1, 10))
    # when the server could not tell (no config readable), say nothing
    assert not any("garbage" in n["text"].lower() for n in p.store_notes({"name": "x", "backend": "filesystem"}, [{"id": "x"}], [{"id": "v"}], [], 1, 10))


def test_default_storage_id_is_a_valid_proxmox_id():
    import re
    sid = default_storage_id("3-OFFSITE_Wasabi")
    assert sid == "pbs-3-OFFSITE_Wasabi" and re.match(r"^[A-Za-z][A-Za-z0-9._-]{1,39}$", sid)
    assert len(default_storage_id("x" * 80)) <= 40
