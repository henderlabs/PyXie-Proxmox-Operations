from pyxie_core.storage_workflow import validate_nfs

BASE = {"id": "nas-iso", "server": "192.168.111.90", "export": "/volume1/ISO", "content": ["iso", "vztmpl"]}


def test_valid_nfs():
    s, errors, notes = validate_nfs(BASE)
    assert errors == [] and s["content"] == ["iso", "vztmpl"] and s["nodes"] == [] and any("every host" in n for n in notes)
    s, errors, notes = validate_nfs({**BASE, "server": "nas.example.lan", "nodes": ["n1"], "nfs_version": "4.2", "content": ["vztmpl", "iso"]})
    assert errors == [] and s["content"] == ["iso", "vztmpl"] and s["nodes"] == ["n1"] and s["nfs_version"] == "4.2" and notes == []


def test_invalid_nfs():
    _, errors, _ = validate_nfs({"id": "1bad", "server": "not a host!", "export": "volume1/x", "content": ["games"], "nfs_version": "2"})
    assert len(errors) == 5
    assert validate_nfs({**BASE, "content": []})[1] and validate_nfs({**BASE, "export": "/a/../b"})[1] and validate_nfs({**BASE, "id": "a"})[1]


def test_disks_plus_backups_note():
    _, errors, notes = validate_nfs({**BASE, "content": ["images", "backup"]})
    assert errors == [] and any("not a backup" in n for n in notes)
