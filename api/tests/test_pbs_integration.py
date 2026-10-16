from pyxie_core import pbs_jobs as j
from pyxie_core.pbs_server import collect_status

STORES = ["2-NAS", "3-OFFSITE"]
REMOTES = ["localhost"]


def test_create_prune_needs_a_keep_setting():
    _, errs = j.validate_job("prune", "create", "prune-x", {"store": "2-NAS", "schedule": "05:00"}, STORES, REMOTES)
    assert any("keep" in e for e in errs)
    clean, errs = j.validate_job("prune", "create", "prune-x", {"store": "2-NAS", "keep-daily": "21"}, STORES, REMOTES)
    assert not errs and clean["keep-daily"] == 21


def test_unknown_store_and_remote_and_bad_id():
    _, errs = j.validate_job("sync", "create", "s", {"store": "nope", "remote-store": "abc", "remote": "zzz"}, STORES, REMOTES)
    assert len(errs) == 3


def test_update_clears_field_with_empty_value():
    clean, errs = j.validate_job("prune", "update", "prune-x", {"keep-daily": "", "keep-weekly": 8}, STORES, REMOTES)
    assert not errs and clean == {"keep-daily": None, "keep-weekly": 8}
    setv, delete = j.split_update(clean)
    assert setv == {"keep-weekly": 8} and delete == ["keep-daily"]


def test_diff_and_readback():
    before = {"store": "2-NAS", "keep-daily": 21}
    clean = {"keep-daily": 14, "keep-weekly": 4}
    d = j.diff(before, clean, "prune")
    assert {x["field"] for x in d} == {"keep-daily", "keep-weekly"}
    assert j.job_matches("prune", {"keep-daily": 14, "keep-weekly": 4}, clean) == []
    assert j.job_matches("prune", {"keep-daily": 21}, clean) == ["keep-daily", "keep-weekly"]
    assert j.job_matches("prune", {"keep-daily": 14}, {"keep-weekly": None}) == []


def test_rejects_settings_pyxie_does_not_manage():
    _, errs = j.validate_job("verify", "update", "verify-a", {"owner": "root@pam"}, STORES, REMOTES)
    assert errs


def test_delete_and_run_only_need_an_id():
    assert j.validate_job("sync", "delete", "monthly-x", {}, STORES, REMOTES)[1] == []
    assert j.validate_job("sync", "run", "x", {}, STORES, REMOTES)[1]


class Fake:
    def version(self): return {"version": "4.2", "release": "1"}
    def node_status(self): return {"uptime": 100, "memory": {"used": 1, "total": 2}, "root": {"used": 1, "total": 4}, "loadavg": [0.1, 0.2, 0.3]}
    def apt_updates(self): return [{"Package": "a", "OldVersion": "1", "Version": "2"}]
    def subscription(self): return {"status": "notfound"}
    def tasks(self, **kw):
        if kw.get("errors"):
            return [{"worker_type": "backup", "worker_id": "vm/1", "endtime": 5, "status": "ERROR: x"}, {"worker_type": "gc", "status": "OK"}]
        return []
    def datastore_usage(self): return [{"store": "a", "used": 90, "total": 100}]


def test_collect_status_summarises_and_survives_missing_permissions():
    st = collect_status(Fake())
    assert st["reachable"] and st["updates"]["count"] == 1 and len(st["tasks_failed_24h"]) == 1
    class Denied(Fake):
        def apt_updates(self): raise PermissionError("403")
    assert collect_status(Denied())["updates"] is None


def test_reboot_needed_matches_the_wrapper_rule():
    from pyxie_core.pbs_server import reboot_needed
    pk = lambda v: {"Package": f"proxmox-kernel-{v}-pve-signed", "Version": v}
    assert reboot_needed("7.0.0-3-pve", [pk("7.0.0-3"), pk("7.0.14-24")]) is True
    assert reboot_needed("7.0.14-24-pve", [pk("7.0.0-3"), pk("7.0.14-24")]) is False
    assert reboot_needed("7.0.0-3-pve", [{"Package": "bash", "Version": "5"}]) is None
    assert reboot_needed(None, [pk("1.0-1")]) is None
