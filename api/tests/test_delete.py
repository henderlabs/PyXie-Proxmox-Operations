from pyxie_core.delete_workflow import owned_disks


def test_owned_disks_vm():
    cfg = {"scsi0": "local-lvm:vm-1-disk-0,iothread=1,size=32G", "ide2": "local:iso/x.iso,media=cdrom,size=5G", "efidisk0": "local-lvm:vm-1-disk-1,efitype=4m,size=4M",
           "net0": "virtio,bridge=vmbr0", "unused0": "nas:vm-1-disk-3", "sata1": "none,media=cdrom", "scsi1": "/dev/sdb,size=100G", "name": "x"}
    got = {d["slot"]: d for d in owned_disks(cfg, "qemu")}
    assert set(got) == {"scsi0", "efidisk0", "unused0"}
    assert got["scsi0"] == {"slot": "scsi0", "storage": "local-lvm", "size": "32G"} and got["unused0"]["size"] is None


def test_owned_disks_lxc():
    cfg = {"rootfs": "local-lvm:vm-200-disk-0,size=8G", "mp0": "nas:200/vm-200-disk-1.raw,mp=/data,size=50G", "net0": "name=eth0,bridge=vmbr0", "mp1": "/host/path,mp=/mnt"}
    assert [d["slot"] for d in owned_disks(cfg, "lxc")] == ["mp0", "rootfs"]
