"""Containers in maintenance plans and the start-at-boot window (2026-11-13, st-pve101 / st-pve105).

Pure tests: no database, no Proxmox. They pin the facts the fix rests on."""

from types import SimpleNamespace

from pyxie_core import boot_window, node_maintenance_workflow as nmw
from pyxie_core.maintenance import _workload_disk_storage_names, lxc_bind_mounts
from pyxie_core.pve_write_client import PveMaintenanceClient


class FakeRead:
    def __init__(self, vms, cts):
        self._vms, self._cts = vms, cts

    def qemu_list(self, node):
        return self._vms

    def lxc_list(self, node):
        return self._cts

    def close(self):
        pass


def test_running_check_counts_containers_and_vms(monkeypatch):
    client = FakeRead([{"vmid": 1, "status": "stopped"}], [{"vmid": 101, "status": "running"}])
    monkeypatch.setattr(nmw, "build_pve_client", lambda db, target: (client, None))
    assert nmw.running_on_node_live(None, object(), SimpleNamespace(name="n1", id=1)) == 1


def test_container_disks_are_read_from_rootfs_and_mount_points():
    config = {
        "rootfs": "local-lvm:vm-101-disk-0,size=8G",
        "mp0": "ST-SYN01-DS01:vm-101-disk-1,mp=/data,size=20G",
        "mp1": "/srv/share,mp=/share",
        "memory": 512,
    }
    assert _workload_disk_storage_names(config) == {"local-lvm", "ST-SYN01-DS01"}
    assert lxc_bind_mounts(config) == ["/srv/share"]


def test_vm_disks_unchanged_by_container_keys():
    config = {"scsi0": "local-lvm:vm-100-disk-0,size=32G", "ide2": "local:iso/x.iso,media=cdrom", "net0": "virtio=AA:BB"}
    assert _workload_disk_storage_names(config) == {"local-lvm"}
    assert lxc_bind_mounts(config) == []


class FakeWrite(PveMaintenanceClient):
    def __init__(self):  # no network
        self.calls = []

    def _request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs.get("data")))
        return "UPID:fake"


def test_container_migrate_uses_restart_and_hyphenated_storage(monkeypatch):
    monkeypatch.setenv("PVE_MUTATIONS_ENABLED", "true")
    import pyxie_core.pve_write_client as w
    monkeypatch.setattr(w, "_mutations_enabled", lambda: True)
    c = FakeWrite()
    assert c.migrate_ct("n1", 101, "n2", restart=True, target_storage="local-lvm") == "UPID:fake"
    method, path, data = c.calls[0]
    assert (method, path) == ("POST", "/nodes/n1/lxc/101/migrate")
    assert data["restart"] == 1 and data["target"] == "n2" and data["target-storage"] == "local-lvm"
    c.migrate_ct("n1", 102, "n2", restart=False)
    assert "restart" not in c.calls[1][2] and "target-storage" not in c.calls[1][2]


def _item(name, vmid, starts_on_boot=True, included=False):
    return {"workload_id": f"w{vmid}", "vmid": vmid, "name": name, "starts_on_boot": starts_on_boot, "included": included, "reasons": ["r"]}


def test_boot_default_is_leave_stopped_in_a_window_when_allowed(monkeypatch):
    monkeypatch.setattr(boot_window, "missing_privileges", lambda db, target, vmid, needed: [])
    items = [_item("ST-PRTG", 1), _item("moved", 2, included=True), _item("plain", 3, starts_on_boot=False)]
    boot_window.set_default(None, None, items, window=True)
    assert items[0]["boot_off"] is True and items[0]["boot_off_available"] is True
    assert "boot_off" not in items[1] and "boot_off" not in items[2]


def test_boot_default_stays_as_is_when_not_a_window(monkeypatch):
    monkeypatch.setattr(boot_window, "missing_privileges", lambda db, target, vmid, needed: [])
    items = [_item("a", 1)]
    boot_window.set_default(None, None, items, window=False)
    assert items[0]["boot_off"] is False and items[0]["boot_off_available"] is True


def test_boot_off_unavailable_without_privilege_says_why(monkeypatch):
    monkeypatch.setattr(boot_window, "missing_privileges", lambda db, target, vmid, needed: ["VM.Config.Options"])
    items = [_item("a", 1)]
    boot_window.set_default(None, None, items, window=True)
    assert items[0]["boot_off"] is False and items[0]["boot_off_available"] is False
    assert "VM.Config.Options" in items[0]["reasons"][-1]


def test_boot_default_unknown_privilege_keeps_todays_behaviour(monkeypatch):
    monkeypatch.setattr(boot_window, "missing_privileges", lambda db, target, vmid, needed: None)
    items = [_item("a", 1)]
    boot_window.set_default(None, None, items, window=True)
    assert items[0]["boot_off"] is False


def test_pending_skips_done_and_moved():
    items = [{**_item("a", 1), "boot_off": True}, {**_item("b", 2), "boot_off": True}, {**_item("c", 3), "boot_off": True, "included": True}]
    assert [i["vmid"] for i in boot_window.pending({"boot_disabled": ["w1"]}, items)] == [2]


from unittest.mock import MagicMock

from pyxie_core.placement import fit_ct_storage


class _Client:
    def __init__(self, rows):
        self._rows = rows

    def storage_list(self, node):
        return self._rows


def _db(storages):
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = storages
    return db


def _st(name, scope, free_gb=100):
    return SimpleNamespace(id=name, name=name, scope=scope, node_id="n3", capacity_bytes=(free_gb + 10) * 2**30, used_bytes=10 * 2**30, is_missing=False)


def test_container_storage_falls_back_when_target_cannot_hold_rootdirs():
    client = _Client([
        {"storage": "NAS", "active": 1, "content": "images,backup"},
        {"storage": "local-lvm", "active": 1, "content": "images,rootdir"},
    ])
    ct = SimpleNamespace(type="lxc")
    dest = SimpleNamespace(id="n3", name="st-pve103")
    rec = {"id": "NAS", "name": "NAS", "scope": "cluster-shared", "reason": "pinned"}
    out = fit_ct_storage(_db([_st("NAS", "cluster-shared"), _st("local-lvm", "node-local")]), client, ct, dest, rec, False)
    assert out["name"] == "local-lvm" and "cannot hold container disks" in out["reason"]


def test_container_storage_kept_when_it_holds_rootdirs_and_vms_untouched():
    client = _Client([{"storage": "NAS", "active": 1, "content": "images,rootdir"}])
    dest = SimpleNamespace(id="n3", name="st-pve103")
    rec = {"id": "NAS", "name": "NAS", "scope": "cluster-shared", "reason": "pinned"}
    assert fit_ct_storage(_db([]), client, SimpleNamespace(type="lxc"), dest, rec, True) is rec
    assert fit_ct_storage(_db([]), _Client([]), SimpleNamespace(type="vm"), dest, rec, True) is rec
    assert fit_ct_storage(_db([]), client, SimpleNamespace(type="lxc"), dest, None, True) is None


def test_guest_running_in_maintenance_finding_names_guest_and_host():
    from datetime import datetime, timezone

    from pyxie_core.findings import _guest_in_maintenance_findings

    node = SimpleNamespace(id="n1", name="st-pve105", maintenance_mode_since=datetime(2026, 11, 13, 16, 52, tzinfo=timezone.utc))
    ct = SimpleNamespace(id="w1", vmid=101, name="pyxie-test-ct", type="lxc", node_id="n1")
    vm = SimpleNamespace(id="w2", vmid=11115, name="ST-Tools", type="vm", node_id="n1")
    db = MagicMock()
    db.query.return_value.filter.side_effect = [SimpleNamespace(all=lambda: [node]), SimpleNamespace(all=lambda: [ct, vm])]
    found = _guest_in_maintenance_findings(db)
    assert [f["dedupe_key"] for f in found] == ["maintenance.guest_running:w1", "maintenance.guest_running:w2"]
    assert "container 'pyxie-test-ct'" in found[0]["title"] and "st-pve105" in found[0]["title"]
    assert "VM 'ST-Tools'" in found[1]["title"] and found[1]["severity"] == "warning"


def test_restore_rows_finds_the_run_that_turned_boot_off_even_though_it_started_before_maintenance_since():
    from datetime import datetime, timezone

    from pyxie_core.models import Operation

    since = datetime(2026, 11, 13, 21, 0, tzinfo=timezone.utc)
    node = SimpleNamespace(id="n1", maintenance_mode_since=since)
    enter = SimpleNamespace(context={"boot_disabled": ["w1", "w9"], "boot_restored": ["w9"]},
                            created_at=datetime(2026, 11, 13, 20, 59, tzinfo=timezone.utc), completed_at=datetime(2026, 11, 13, 21, 1, tzinfo=timezone.utc))
    wl = SimpleNamespace(id="w1", vmid=101, name="pyxie-test-ct", type="lxc", node_id="n1", is_missing=False)
    filters = []

    class Q:
        def filter(self, *conds):
            filters.extend(str(c) for c in conds)
            return self

        def order_by(self, *a):
            return self

        def all(self):
            return [enter]

        def one_or_none(self):
            return wl

    db = MagicMock()
    db.query.side_effect = lambda model: Q()
    rows = boot_window.restore_rows(db, node)
    assert [r["workload_id"] for r in rows] == ["w1"] and rows[0]["type"] == "lxc"
    assert any("completed_at" in f for f in filters) and not any("created_at >=" in f for f in filters)


def test_started_by_themselves_lists_running_guests_after_reboot():
    vms = [{"vmid": 11115, "name": "ST-Tools", "status": "running"}, {"vmid": 11151, "name": "ST-VBR", "status": "stopped"}]
    cts = [{"vmid": 101, "name": "pyxie-test-ct", "status": "running"}]
    assert boot_window.started_by_themselves(vms, cts) == ["ST-Tools (VM 11115)", "pyxie-test-ct (CT 101)"]
    assert boot_window.started_by_themselves([], None) == []


class _Tasks:
    def __init__(self, sequence):
        self.sequence = list(sequence)  # each item: (active_rows, archive_rows)
        self.calls = 0

    def tasks(self, node, limit=20, *, source=None, vmid=None):
        active, archive = self.sequence[min(self.calls // 2, len(self.sequence) - 1)]
        self.calls += 1
        return active if source == "active" else archive


def test_wait_for_startall_waits_for_the_boot_task_then_returns_true():
    import time

    now = time.time()
    client = _Tasks([
        ([], []),                                                           # nothing yet: pve-guests has not started
        ([{"type": "startall"}], []),                                       # running
        ([], [{"type": "startall", "starttime": now - 5}]),                 # finished since boot
    ])
    ticks = iter(range(0, 1000, 4))
    assert boot_window.wait_for_startall(client, "n1", 18, timeout=100, clock=lambda: next(ticks), sleep=lambda s: None) is True


def test_wait_for_startall_gives_up_when_it_never_shows():
    client = _Tasks([([], [{"type": "startall", "starttime": 1}])])  # an old one from a previous boot
    ticks = iter(range(0, 1000, 30))
    assert boot_window.wait_for_startall(client, "n1", 18, timeout=100, clock=lambda: next(ticks), sleep=lambda s: None) is False
