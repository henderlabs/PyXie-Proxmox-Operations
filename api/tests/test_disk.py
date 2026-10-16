from pyxie_core.disk_workflow import build_new_disk, boot_uses, disk_keys, free_slot, parse_disk, unused_keys, volume_name


def test_parse_and_keys():
    assert parse_disk("local-lvm:vm-101-disk-0,cache=none,size=32G") == {"volume": "local-lvm:vm-101-disk-0", "options": {"cache": "none", "size": "32G"}}
    cfg = {"scsi0": "a:vm-1-disk-0,size=10G", "scsi1": "a:vm-1-disk-1", "ide2": "iso:x.iso,media=cdrom", "unused1": "a:vm-1-disk-5", "unused0": "a:vm-1-disk-4", "net0": "virtio"}
    assert disk_keys(cfg) == ["scsi0", "scsi1"] and unused_keys(cfg) == ["unused0", "unused1"]


def test_free_slot():
    assert free_slot({"scsi0": "x", "scsi1": "x"}, "scsi") == "scsi2"
    assert free_slot({f"ide{i}": "x" for i in range(4)}, "ide") is None
    assert free_slot({}, "nvme") is None


def test_new_disk_line():
    assert build_new_disk("local-lvm", 20, bus="scsi", cache="none", discard=False, ssd=False, iothread=False) == "local-lvm:20"
    assert build_new_disk("s", 5, bus="scsi", cache="writeback", discard=True, ssd=True, iothread=True) == "s:5,cache=writeback,discard=on,ssd=1,iothread=1"
    assert build_new_disk("s", 5, bus="virtio", cache="none", discard=False, ssd=True, iothread=True) == "s:5,iothread=1"
    assert build_new_disk("s", 5, bus="sata", cache="none", discard=False, ssd=False, iothread=True) == "s:5"


def test_boot_and_names():
    assert boot_uses({"boot": "order=scsi0;ide2"}, "scsi0") and not boot_uses({"boot": "order=scsi0;ide2"}, "scsi1")
    assert boot_uses({"bootdisk": "scsi1"}, "scsi1")
    assert volume_name("local-lvm:vm-101-disk-1") == "vm-101-disk-1"
