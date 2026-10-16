from pyxie_core.storage_workflow import describe_storage, validate_storage


def test_nfs_still_works():
    spec, errs, _ = validate_storage({"type": "nfs", "id": "nas1", "server": "10.0.0.5", "export": "/vol/a", "content": ["iso"]})
    assert not errs and spec["type"] == "nfs"


def test_iscsi():
    ok, errs, notes = validate_storage({"type": "iscsi", "id": "san1", "portal": "10.0.0.9:3260", "target": "iqn.2005-10.org.example:disk1", "content": ["images"]})
    assert not errs and ok["portal"] == "10.0.0.9:3260" and any("CHAP" in n for n in notes)
    assert validate_storage({"type": "iscsi", "id": "san1", "portal": "10.0.0.9", "target": "not-an-iqn", "content": ["images"]})[1]
    assert validate_storage({"type": "iscsi", "id": "san1", "portal": "10.0.0.9", "target": "iqn.2005-10.org.example:d", "content": ["iso"]})[1]


def test_dir_and_paths():
    assert not validate_storage({"type": "dir", "id": "d1", "path": "/mnt/data", "content": ["iso", "backup"]})[1]
    for bad in ("mnt/data", "/mnt/../etc", "/mnt/da ta", "/"):
        assert validate_storage({"type": "dir", "id": "d1", "path": bad, "content": ["iso"]})[1], bad


def test_lvm_shared_and_thin():
    ok, errs, _ = validate_storage({"type": "lvm", "id": "fc1", "vgname": "vg_fc", "content": ["images"], "shared": True})
    assert not errs and ok["shared"] is True
    ok, errs, _ = validate_storage({"type": "lvmthin", "id": "t1", "vgname": "pve", "thinpool": "data", "content": ["images", "rootdir"]})
    assert not errs and ok["thinpool"] == "data" and "shared" not in ok
    assert validate_storage({"type": "lvmthin", "id": "t1", "vgname": "pve", "thinpool": "", "content": ["images"]})[1]
    assert validate_storage({"type": "lvm", "id": "fc1", "vgname": "vg;rm", "content": ["images"]})[1]


def test_zfs_and_unknown():
    assert not validate_storage({"type": "zfspool", "id": "z1", "pool": "tank/vm", "content": ["images"]})[1]
    assert validate_storage({"type": "zfspool", "id": "z1", "pool": "-x", "content": ["images"]})[1]
    assert validate_storage({"type": "cifs", "id": "c1", "content": ["iso"]})[1]


def test_describe():
    spec, _, _ = validate_storage({"type": "lvm", "id": "fc1", "vgname": "vg_fc", "content": ["images"], "shared": True})
    assert "volume group vg_fc, shared" in describe_storage(spec)
