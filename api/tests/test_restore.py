from pyxie_core import restore_points as rp
from pyxie_core.restore_workflow import _storage_problems, _timeout_for

CFG = """#note
boot: order=scsi0;net0
cores: 2
memory: 2048
name: web
net0: virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0,firewall=1
net1: virtio=AA:BB:CC:DD:EE:00,bridge=vmbr1,link_down=1
scsi0: ST-SYN01-DS01:vm-100-disk-0,iothread=1,size=32G
scsi1: local-lvm:vm-100-disk-1,size=512M
ide2: none,media=cdrom
efidisk0: local-lvm:vm-100-disk-2,efitype=4m,size=4M
[snap1]
cores: 8
"""


def test_volid_must_match_a_proxmox_backup_volume():
    assert rp.VOLID_RE.match("st-pbs:backup/vm/11150/2026-10-16T08:00:02Z")
    assert rp.VOLID_RE.match("st-pbs:backup/ct/100/2026-10-16T08:00:02Z")
    assert not rp.VOLID_RE.match("st-pbs:iso/debian.iso")
    assert not rp.VOLID_RE.match("../etc:backup/vm/1/2026-10-16T08:00:02Z")


def test_config_text_stops_at_the_first_snapshot_section():
    cfg = rp.parse_config_text(CFG)
    assert cfg["cores"] == "2" and cfg["name"] == "web"


def test_disks_skip_cdroms_and_count_sizes():
    d = rp.config_disks(rp.parse_config_text(CFG), "vm")
    assert {x["key"] for x in d} == {"scsi0", "scsi1", "efidisk0"}
    assert next(x for x in d if x["key"] == "scsi0")["size"] == 32 * 2 ** 30
    assert next(x for x in d if x["key"] == "scsi1")["storage"] == "local-lvm"


def test_container_disks_skip_bind_mounts():
    cfg = {"rootfs": "local-lvm:vm-5-disk-0,size=8G", "mp0": "/srv/data,mp=/data", "mp1": "local-lvm:vm-5-disk-1,size=1G,mp=/x"}
    assert {x["key"] for x in rp.config_disks(cfg, "ct")} == {"rootfs", "mp1"}


def test_nic_link_state_is_set_and_cleared():
    line = "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0,firewall=1"
    down = rp.net_with_link(line, False)
    assert rp.net_is_down(down) and not rp.net_is_down(line)
    assert rp.net_with_link(down, True) == line
    assert rp.net_with_link(down, False).count("link_down") == 1


def test_points_merge_marks_pbs_only_copies_not_restorable():
    pve = [{"storage": "st-pbs", "datastore": "2-NAS", "volid": "st-pbs:backup/vm/1/2026-10-16T08:00:00Z", "time": 100, "size": 5, "verified": None}]
    pbs = [{"datastore": "2-NAS", "time": 100, "verified": "ok", "offsite": False}, {"datastore": "3-OFFSITE", "time": 90, "verified": "ok", "offsite": True}]
    out = rp.merge_points(pve, pbs)
    assert [p["datastore"] for p in out] == ["2-NAS", "3-OFFSITE"]
    assert out[0]["restorable"] and out[0]["verified"] == "ok" and out[0]["volid"]
    assert not out[1]["restorable"] and out[1]["volid"] is None and "storage" in out[1]["reason"]


def test_storage_checks_and_timeout():
    rows = [{"storage": "fast", "active": 1, "content": "images,rootdir", "avail": 100 * 2 ** 30}, {"storage": "iso", "active": 1, "content": "iso", "avail": 10 ** 12}]
    assert _storage_problems(rows, "fast", 50 * 2 ** 30, "vm") == []
    assert _storage_problems(rows, "fast", 95 * 2 ** 30, "vm")
    assert _storage_problems(rows, "iso", 1, "vm")
    assert _storage_problems(rows, "nope", 1, "vm")
    assert 1800 <= _timeout_for(0) < _timeout_for(500 * 2 ** 30) <= 36000


def test_disks_marked_backup_0_are_not_in_the_archive():
    cfg = {"efidisk0": "intel:vm-1-disk-0,size=4M", "virtio0": "intel:vm-1-disk-1,backup=0,size=100G", "scsi0": "x:vm-1-disk-2,size=8G"}
    assert {d["key"] for d in rp.config_disks(cfg, "vm")} == {"efidisk0", "scsi0"}
