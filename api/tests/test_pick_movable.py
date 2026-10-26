"""The per-disk "Move storage" action moves only the chosen disk; the all-disks action still moves every disk
that is not on the target already."""

from types import SimpleNamespace

from pyxie_core.storage_move_workflow import pick_movable


def _disk(slot, storage, size=10, volume="vol"):
    return SimpleNamespace(slot=slot, storage=storage, size_bytes=size, volume=volume)


DISKS = [_disk("scsi1", "intel-ssd-106", 120), _disk("scsi0", "ST-SYN01-DS01", 32), _disk("scsi2", "local-lvm", 8)]


def test_all_disks_not_on_target_move_sorted_by_slot():
    got = pick_movable(DISKS, "ST-SYN01-DS01")
    assert [d["slot"] for d in got] == ["scsi1", "scsi2"]


def test_only_slot_limits_the_move_to_that_disk():
    got = pick_movable(DISKS, "ST-SYN01-DS01", "scsi1")
    assert got == [{"slot": "scsi1", "storage": "intel-ssd-106", "size_bytes": 120}]


def test_only_slot_already_on_target_moves_nothing():
    assert pick_movable(DISKS, "ST-SYN01-DS01", "scsi0") == []


def test_unknown_slot_moves_nothing():
    assert pick_movable(DISKS, "ST-SYN01-DS01", "scsi9") == []


def test_empty_drives_and_missing_storage_are_never_moved():
    rows = [_disk("ide2", None), _disk("scsi0", "local-lvm", volume="none"), _disk("scsi1", "local-lvm")]
    assert [d["slot"] for d in pick_movable(rows, "ST-SYN01-DS01")] == ["scsi1"]
