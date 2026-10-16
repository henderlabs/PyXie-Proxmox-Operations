from pyxie_core.hw_edit_workflow import disk_size_bytes, is_growable_disk, parse_size_bytes, pending_value

GIB = 1 << 30


def test_parse_sizes():
    assert parse_size_bytes("100G") == 100 * GIB
    assert parse_size_bytes("512M") == 512 << 20
    assert parse_size_bytes("1.5T") == int(1.5 * (1 << 40))
    assert parse_size_bytes("123") == 123
    assert parse_size_bytes("abc") is None and parse_size_bytes(None) is None


def test_disk_size_from_config_line():
    assert disk_size_bytes("intel-ssd-101:vm-11166-disk-0,iothread=1,size=100G") == 100 * GIB
    assert disk_size_bytes("local-lvm:vm-1-disk-0,size=32G,discard=on") == 32 * GIB
    assert disk_size_bytes("local-lvm:vm-1-disk-0") is None


def test_which_disks_can_grow():
    assert is_growable_disk("scsi0", "local-lvm:vm-1-disk-0,size=32G")
    assert is_growable_disk("virtio1", "nas:vm-1-disk-1,size=10G")
    assert not is_growable_disk("ide2", "local:iso/win.iso,media=cdrom,size=5G")
    assert not is_growable_disk("ide0", "none,media=cdrom")
    assert not is_growable_disk("efidisk0", "local-lvm:vm-1-disk-1,size=4M")
    assert not is_growable_disk("tpmstate0", "local-lvm:vm-1-disk-2,size=4M")
    assert not is_growable_disk("net0", "virtio=AA:BB")


def test_pending_value_prefers_a_pending_change():
    rows = [{"key": "cpu", "value": "kvm64", "pending": "host"}, {"key": "machine", "value": "q35"}]
    cfg = {"cpu": "kvm64", "machine": "q35", "cores": 2}
    assert pending_value(rows, cfg, "cpu") == "host"
    assert pending_value(rows, cfg, "machine") == "q35"
    assert pending_value([], cfg, "cores") == 2
