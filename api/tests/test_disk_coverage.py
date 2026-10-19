from pyxie_core.protection_321 import apply_disk_coverage
from pyxie_core.restore_points import config_disks, describe_disks, disks_not_in_backup
from pyxie_core.setup_status import build_steps

GIB = 1 << 30


def row(status="ok"):
    return {"status": status, "problems": []}


def d(slot, gib, backup):
    return {"slot": slot, "size_bytes": gib * GIB, "backup": backup}


def test_all_disks_in_backup_changes_nothing():
    r = apply_disk_coverage(row(), [d("scsi0", 10, True), {"slot": "scsi1", "size_bytes": None, "backup": None}])
    assert r["status"] == "ok" and r["problems"] == [] and r["notes"] == []
    assert r["disk_coverage"]["included"] == 2


def test_running_guest_with_no_backup_holding_its_only_disk_is_partial():
    r = apply_disk_coverage(row(), [d("scsi0", 101, False)], powered_on=True, last_data={"scsi0": None})
    assert r["status"] == "partial" and "No backup holds the data of disk scsi0 (101 GiB)" in r["problems"][0] and "empty guest" in r["problems"][0]


def test_powered_off_guest_with_an_earlier_backup_holding_the_disk_is_fine_with_a_note():
    r = apply_disk_coverage(row(), [d("scsi0", 101, False)], powered_on=False, last_data={"scsi0": 1790000000})
    assert r["status"] == "ok" and r["problems"] == []
    assert "Powered off" in r["notes"][0] and "last complete backup" in r["notes"][0]


def test_powered_off_guest_where_no_backup_holds_the_disk_is_still_partial():
    r = apply_disk_coverage(row(), [d("scsi0", 101, False)], powered_on=False, last_data={"scsi0": None})
    assert r["status"] == "partial" and "No backup holds" in r["problems"][0]


def test_running_guest_with_an_old_backup_of_the_disk_says_changes_are_not_saved():
    r = apply_disk_coverage(row(), [d("scsi0", 8, True), d("scsi1", 50, False)], powered_on=True, last_data={"scsi1": 1790000000})
    assert r["status"] == "partial" and "changes since then are not saved" in r["problems"][0]


def test_disk_left_out_on_purpose_is_only_a_note():
    r = apply_disk_coverage(row(), [d("scsi1", 50, False)], powered_on=True, last_data={"scsi1": None}, accepted={"scsi1"})
    assert r["status"] == "ok" and r["problems"] == [] and "on purpose" in r["notes"][0]
    assert r["disk_coverage"]["excluded"][0]["accepted"] is True


def test_none_status_stays_none_but_problem_is_added():
    r = apply_disk_coverage(row("none"), [d("scsi0", 1, False)])
    assert r["status"] == "none" and len(r["problems"]) == 1


def test_guest_without_disk_records_is_untouched():
    r = apply_disk_coverage(row(), [])
    assert r["status"] == "ok" and r["disk_coverage"]["total"] == 0


def test_disks_not_in_backup_lists_backup_off_disks_only():
    current = {"scsi0": "intel-ssd-104:vm-1-disk-0,backup=0,iothread=1,size=101G", "scsi1": "intel-ssd-104:vm-1-disk-1,size=20G",
               "ide2": "none,media=cdrom", "net0": "virtio=AA:BB,bridge=vmbr0"}
    in_backup = config_disks({"scsi1": "intel-ssd-104:vm-1-disk-1,size=20G"}, "vm")
    left = disks_not_in_backup(current, in_backup, "vm")
    assert [d["key"] for d in left] == ["scsi0"] and left[0]["size"] == 101 * GIB
    assert describe_disks(left) == "scsi0 (101 GiB)"


def test_disks_not_in_backup_empty_when_backup_holds_everything():
    cfg = {"rootfs": "local-lvm:vm-5-disk-0,size=8G", "mp0": "/mnt/x,mp=/data"}
    assert disks_not_in_backup(cfg, config_disks({"rootfs": cfg["rootfs"]}, "ct"), "ct") == []


def _steps(**settings):
    t = {"id": "t", "name": "Lab", "inventory_status": "valid", "maintenance_status": "valid", "nodes": 1}
    return {x["key"]: x for x in build_steps({"sites": 1, "targets": [t], "nodes": [], "hostmaint": [], "settings": settings})}


def test_notifications_not_done_when_rule_has_no_recipient():
    s = _steps(smtp_enabled=True, notification_recipient_set=False, notification_rules=1, notification_rules_on=1)["notifications"]
    assert s["state"] != "done" and "no one to email" in s["summary"].lower() or "anyone to email" in s["summary"].lower()


def test_notifications_done_when_rule_resolves_to_someone():
    s = _steps(smtp_enabled=True, notification_recipient_set=True, notification_rules=1, notification_rules_on=1)["notifications"]
    assert s["state"] == "done"


def test_removed_guests_known_and_unknown_backups_are_separated():
    from pyxie_core.protection_321 import removed_and_unknown
    current = {("vm", "101")}
    missing = [{"workload_id": "w1", "kind": "vm", "vmid": 200, "name": "old"}, {"workload_id": "w2", "kind": "vm", "vmid": 101, "name": "reused"}, {"workload_id": "w3", "kind": "ct", "vmid": 300, "name": "nobackups"}]
    last = {("vm", "101"): {"a": 5}, ("vm", "200"): {"a": 10, "b": 20}, ("vm", "999"): {"a": 7}, ("ct", "300"): {}}
    removed, unknown = removed_and_unknown(current, missing, last)
    assert [r["vmid"] for r in removed] == [200] and removed[0]["last"] == 20
    assert [u["vmid"] for u in unknown] == ["999"]
