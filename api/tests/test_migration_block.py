"""An open console window must not block a migration, and when the final check does block one the reason is kept where the
page shows it (the operation's reasons list), not just the rule ID."""

from pyxie_core.migration_workflow import with_revalidation_block
from pyxie_core.preconditions import tasks_that_conflict


def _task(kind, upid="UPID:x"):
    return {"type": kind, "upid": upid, "status": "RUNNING"}


def test_console_sessions_do_not_conflict():
    tasks = [_task("vncproxy"), _task("vncshell"), _task("spiceproxy"), _task("termproxy")]
    assert tasks_that_conflict(tasks) == []


def test_real_tasks_still_conflict_even_beside_a_console():
    real = _task("qmigrate", "UPID:migrate")
    assert tasks_that_conflict([_task("vncproxy"), real, _task("qmsnapshot")]) == [real, _task("qmsnapshot")]


def test_no_tasks_or_none_is_no_conflict():
    assert tasks_that_conflict([]) == []
    assert tasks_that_conflict(None) == []


def test_block_reason_goes_into_the_reasons_the_page_prints():
    preview = {"vmid": 11166, "eligible": True, "reasons": ["disk(s) on node-local storage will be relocated to 'local-lvm'"]}
    got = with_revalidation_block(preview, ["VM 11166 already has an active PVE task in progress: UPID:abc"])
    assert got["reasons"][0] == "Blocked at the final check, just before the move: VM 11166 already has an active PVE task in progress: UPID:abc"
    assert got["reasons"][1] == "disk(s) on node-local storage will be relocated to 'local-lvm'"
    assert got["revalidation_blocking_reasons"] == ["VM 11166 already has an active PVE task in progress: UPID:abc"]
    assert got["vmid"] == 11166 and got["eligible"] is True
    assert preview["reasons"] == ["disk(s) on node-local storage will be relocated to 'local-lvm'"]  # the input is not changed


def test_block_with_no_preview_result():
    got = with_revalidation_block(None, ["destination st-pve106 no longer has sufficient headroom (11.9 GB free, needs 12.0 GB)"])
    assert got["reasons"] == ["Blocked at the final check, just before the move: destination st-pve106 no longer has sufficient headroom (11.9 GB free, needs 12.0 GB)"]
