from pyxie_core.vm_create import build_params, is_windows, validate_spec

BASE = {"name": "web01", "node": "n1", "disk_storage": "local-lvm", "nic_bridge": "vmbr0"}


def ok(**kw):
    spec, errors, notes = validate_spec({**BASE, **kw})
    assert errors == [], errors
    return spec, notes


def test_minimum_gets_the_wizard_values():
    s, _ = ok()
    assert (s["cores"], s["sockets"], s["memory_mb"], s["disk_size_gb"], s["cpu_type"], s["bios"], s["scsihw"], s["nic_model"]) == (1, 1, 2048, 32, "x86-64-v2-AES", "seabios", "virtio-scsi-single", "virtio")
    p = build_params(s, 150)
    assert p["scsi0"] == "local-lvm:32,iothread=1" and p["net0"] == "virtio,bridge=vmbr0" and p["boot"] == "order=scsi0;net0"
    assert p["vmid"] == 150 and p["cpu"] == "x86-64-v2-AES" and "machine" not in p and "efidisk0" not in p and "agent" not in p


def test_full_windows_11_style_vm():
    s, notes = ok(ostype="win11", bios="ovmf", machine="q35", tpm=True, cpu_type="x86-64-v3", cores=4, memory_mb=8192, balloon=False, agent=True, disk_size_gb=80,
                  disk_discard=True, disk_ssd=True, disk_cache="writeback", iso="local:iso/win11.iso", nic_vlan=20, nic_firewall=True, tags=["windows", "Prod", "prod"],
                  pool="Prod Pool", start_at_boot=True, efi_storage="fast")
    p = build_params(s, 201)
    assert p["scsi0"] == "local-lvm:80,cache=writeback,discard=on,ssd=1,iothread=1"
    assert p["efidisk0"] == "fast:1,efitype=4m,pre-enrolled-keys=1" and p["tpmstate0"] == "local-lvm:1,version=v2.0"
    assert p["ide2"] == "local:iso/win11.iso,media=cdrom" and p["boot"] == "order=ide2;scsi0;net0"
    assert p["net0"] == "virtio,bridge=vmbr0,tag=20,firewall=1" and p["tags"] == "windows;Prod" and p["pool"] == "Prod Pool"
    assert p["balloon"] == 0 and p["agent"] == 1 and p["onboot"] == 1 and p["machine"] == "q35" and p["bios"] == "ovmf" and p["cores"] == 4
    assert notes == []


def test_tpm_forces_uefi_and_win11_warns_without():
    s, notes = ok(tpm=True)
    assert s["bios"] == "ovmf" and any("OVMF" in n for n in notes) and s["efi_storage"] == "local-lvm"
    _, notes = ok(ostype="win11")
    assert any("Windows 11" in n for n in notes)
    assert is_windows("win10") and is_windows("w2k8") and not is_windows("l26")


def test_virtio_disk_rules():
    spec, errors, _ = validate_spec({**BASE, "disk_bus": "virtio", "disk_ssd": True})
    assert any("SSD emulation" in e for e in errors)
    s, notes = ok(disk_bus="sata", disk_iothread=True)
    assert s["disk_iothread"] is False and any("IO thread" in n for n in notes) and build_params(s, 1)["sata0"] == "local-lvm:32"
    s, notes = ok(disk_bus="scsi", scsihw="lsi")
    assert any("virtio-scsi-single" in n for n in notes)


def test_validation_errors():
    bad = {"name": "bad name!", "node": "", "vmid": 5, "cores": 0, "memory_mb": 10, "disk_bus": "nvme", "disk_storage": "", "nic_model": "ne2k", "nic_bridge": "",
           "nic_vlan": 5000, "iso": "not-a-volume", "tags": ["has space"], "bios": "uefi", "ostype": "Bad OS", "cpu_type": "x y"}
    _, errors, _ = validate_spec(bad)
    assert len(errors) >= 13
    assert validate_spec({})[1]


def test_boot_media_note():
    s, notes = ok(start_after=True)
    assert any("no installation media" in n for n in notes)
    _, notes = ok(start_after=True, iso="local:iso/x.iso")
    assert notes == []
