"""Changing a VM's storage: which disks move, and whether the target can take them."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.storage_move_workflow import check_target, tpm_live_move_problem
from pyxie_core.pve_write_client import update_migration_progress

GB = 1024 ** 3


def rows(**kw):
    base = {"storage": "intel-ssd-101", "content": "images,rootdir", "active": 1, "avail": 500 * GB}
    return [{**base, **kw}]


def test_a_good_target_has_no_problems():
    assert check_target(rows(), "intel-ssd-101", 100 * GB) == []


def test_each_way_a_target_can_be_unfit_is_named():
    assert "not available" in check_target(rows(), "nas", GB)[0]
    assert "not active" in check_target(rows(active=0), "intel-ssd-101", GB)[0]
    assert "VM disks" in check_target(rows(content="iso,vztmpl,backup"), "intel-ssd-101", GB)[0]
    assert "free" in check_target(rows(avail=105 * GB), "intel-ssd-101", 100 * GB)[0]  # 100 GB + 10% margin does not fit in 105 GB


def test_move_disk_progress_lines_are_read_like_a_migration_mirror():
    p = None
    p = update_migration_progress(p, [{"n": 1, "t": "drive-scsi0: transferred 10.0 GiB of 100.0 GiB (10.00%) in 1m 5s"}])
    assert p["storage"]["pct"] == 10.0 and p["phase"] == "storage"
    p = update_migration_progress(p, [{"n": 9, "t": "drive-scsi0: transferred 100.0 GiB of 100.0 GiB (100.00%) in 9m 2s"}, {"n": 10, "t": "drive-scsi1: transferred 0.0 B of 50.0 GiB (0.00%) in 0s"}])
    assert p["storage"]["total_bytes"] == 150 * GB and round(p["storage"]["pct"]) == 67


def test_a_running_vms_tpm_disk_is_flagged_but_a_stopped_vms_is_not():
    disks = [{"slot": "scsi0", "storage": "a", "size_bytes": GB}, {"slot": "tpmstate0", "storage": "a", "size_bytes": 4 * 1024 ** 2}]
    assert "tpmstate0" in tpm_live_move_problem(disks, True) and "shut the VM down" in tpm_live_move_problem(disks, True)
    assert tpm_live_move_problem(disks, False) is None
    assert tpm_live_move_problem(disks[:1], True) is None
