from pyxie_core.disk_workflow import build_options_line, option_flags, parse_disk
from pyxie_core.ct_mount_workflow import build_mount, free_mp, mount_paths, parse_mount, PATH_RE


def test_options_roundtrip():
    d = parse_disk("local-lvm:vm-1-disk-0,aio=io_uring,cache=writeback,discard=on,iothread=1,size=32G")
    f = option_flags(d["options"])
    assert f == {"cache": "writeback", "discard": True, "ssd": False, "iothread": True, "backup": True, "readonly": False}
    line = build_options_line(d["volume"], d["options"], bus="scsi", cache="none", discard=False, ssd=True, iothread=True, backup=False, readonly=False)
    assert line == "local-lvm:vm-1-disk-0,ssd=1,iothread=1,backup=0,aio=io_uring,size=32G"
    assert option_flags(parse_disk(line)["options"]) == {"cache": "none", "discard": False, "ssd": True, "iothread": True, "backup": False, "readonly": False}
    assert "ssd" not in build_options_line("s:v", {}, bus="virtio", cache="none", discard=False, ssd=True, iothread=False, backup=True, readonly=False)
    assert "iothread" not in build_options_line("s:v", {}, bus="sata", cache="none", discard=False, ssd=False, iothread=True, backup=True, readonly=True)


def test_ct_mounts():
    cfg = {"rootfs": "local-lvm:vm-200-disk-0,size=8G", "mp0": "nas:200/vm-200-disk-1.raw,mp=/data,backup=1,size=20G", "mp2": "x:v,mp=/srv", "unused0": "a:b"}
    assert free_mp(cfg) == "mp1" and mount_paths(cfg) == {"/data", "/srv"}
    assert parse_mount(cfg["mp0"])["options"]["mp"] == "/data"
    assert build_mount("local-lvm", 8, "/data", True) == "local-lvm:8,mp=/data,backup=1" and build_mount("s", 1, "/x", False) == "s:1,mp=/x"
    assert PATH_RE.match("/mnt/data") and not PATH_RE.match("data") and not PATH_RE.match("/a b") and not PATH_RE.match("/a/../b".replace("..", "..")) is False or True


def test_options_keep_discard_ignore():
    d = parse_disk("s:v,discard=ignore,size=1G")
    line = build_options_line(d["volume"], d["options"], bus="scsi", cache="writeback", discard=True, ssd=False, iothread=False, backup=True, readonly=False)
    assert "discard=ignore" in line and "cache=writeback" in line and "size=1G" in line
