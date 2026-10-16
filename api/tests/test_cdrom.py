from pyxie_core.cdrom_workflow import cdrom_line, free_ide_slot, is_cdrom_line, new_boot_order


def test_lines_and_slots():
    assert cdrom_line("local:iso/a.iso") == "local:iso/a.iso,media=cdrom" and cdrom_line(None) == "none,media=cdrom"
    assert is_cdrom_line("local:iso/a.iso,media=cdrom,size=5G") and not is_cdrom_line("local-lvm:vm-1-disk-0,size=1G")
    assert free_ide_slot({}) == "ide0" and free_ide_slot({"ide0": "x", "ide2": "y"}) == "ide1" and free_ide_slot({f"ide{i}": "x" for i in range(4)}) is None


def test_boot_order_moves_the_drive_first():
    assert new_boot_order("order=scsi0;ide2;net0", "ide2") == "order=ide2;scsi0;net0"
    assert new_boot_order("order=scsi0;net0", "ide2") == "order=ide2;scsi0;net0"
    assert new_boot_order("", "ide2") == "order=ide2"


def test_legacy_boot_setting_is_converted():
    cfg = {"scsi0": "a:vm-1-disk-0", "ide2": "iso:x.iso,media=cdrom", "net0": "virtio=AA", "bootdisk": "scsi0"}
    assert new_boot_order("cdn", "ide2", cfg) == "order=ide2;scsi0;net0"
    assert new_boot_order("c", "ide3", cfg) == "order=ide3;scsi0"
