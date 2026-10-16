from pyxie_core.iso_workflow import check_space, filename_from_url, parse_volid, storage_can_hold_isos, validate_download

SHA = "a" * 64


def ok(**kw):
    spec, errors, notes = validate_download({"url": "https://example.org/dl/debian-12.iso", "storage": "nas", "node": "n1", **kw})
    assert errors == [], errors
    return spec, notes


def test_filename_from_url():
    assert filename_from_url("https://x.org/a/b/ubuntu-24.04.1-live-server-amd64.iso") == "ubuntu-24.04.1-live-server-amd64.iso"
    assert filename_from_url("https://x.org/a/My%20Disc.iso") is None  # a space is not allowed in the stored name
    assert filename_from_url("https://x.org/download?id=3") is None


def test_download_validation():
    s, notes = ok()
    assert s["filename"] == "debian-12.iso" and s["checksum"] is None and any("no checksum" in n for n in notes) and s["verify_certificates"] is True
    s, notes = ok(checksum=SHA.upper(), filename="d.iso")
    assert s["algorithm"] == "sha256" and s["checksum"] == SHA and s["filename"] == "d.iso" and notes == []
    _, errors, _ = validate_download({"url": "ftp://x/y.iso", "storage": "nas", "node": "n1"})
    assert errors
    _, errors, _ = validate_download({"url": "https://u:p@x.org/y.iso", "storage": "nas", "node": "n1"})
    assert any("user name" in e for e in errors)
    _, errors, _ = validate_download({"url": "https://x.org/y.iso", "storage": "nas", "node": "n1", "checksum": "abc", "algorithm": "sha256"})
    assert any("64 hex" in e for e in errors)
    _, errors, _ = validate_download({"url": "https://x.org/y.iso", "storage": "nas", "node": "n1", "checksum": SHA, "algorithm": "crc32"})
    assert errors
    _, errors, _ = validate_download({"url": "https://x.org/download", "storage": "", "node": ""})
    assert len(errors) == 3  # no usable file name, no storage, no host
    _, notes = ok(url="http://x.org/y.iso")
    assert any("plain http" in n for n in notes)


def test_volid_and_storage_types():
    assert parse_volid("local:iso/a-b_1.2.iso") == ("local", "a-b_1.2.iso")
    assert parse_volid("ST-SYN01-DS01:iso/SW_DVD9_Win_Server_2025_X23-81891.ISO") == ("ST-SYN01-DS01", "SW_DVD9_Win_Server_2025_X23-81891.ISO")
    assert filename_from_url("https://x.org/a/Win11_22H2_English_x64v1.ISO") == "Win11_22H2_English_x64v1.ISO"
    assert parse_volid("local:vztmpl/a.tar.zst") is None and parse_volid("local:iso/../x.iso") is None and parse_volid("") is None
    assert storage_can_hold_isos("nfs") and storage_can_hold_isos("dir") and not storage_can_hold_isos("lvmthin") and not storage_can_hold_isos("zfspool") and not storage_can_hold_isos(None)


def test_space_check():
    assert check_space({"storage": "nas", "avail": 100 << 30}, 5 << 30) == []
    assert check_space({"storage": "nas", "avail": 5 << 30}, 5 << 30)  # no room for the 10% margin
    assert check_space(None, 1)
    assert check_space({"storage": "nas"}, 1 << 40) == []  # free space unknown: Proxmox decides
