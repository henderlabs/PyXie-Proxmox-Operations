from types import SimpleNamespace

from pyxie_core.findings import snapshot_age_summary
from pyxie_core.notifications import CATEGORY_KEYS, operation_event


def test_snapshot_age_summary():
    assert snapshot_age_summary([1, 3, 6], 7, 30) is None
    assert snapshot_age_summary([2, 8], 7, 30) == ("warning", 1, 8)
    assert snapshot_age_summary([9, 12, 31], 7, 30) == ("critical", 3, 31)
    assert snapshot_age_summary([], 7, 30) is None


def op(**kw):
    base = dict(operation_type_id="workload.clone", status="failed", error="Proxmox reported the clone did not succeed", approved_at="2026-10-16", dry_run_result={"workload_name": "web01"}, context={})
    base.update(kw)
    return SimpleNamespace(**base)


def test_failed_approved_operation_is_critical():
    e = operation_event(op())
    assert e["severity"] == "critical" and e["category"] == "operation" and e["recovered"] is False
    assert e["title"].startswith("Clone of web01 failed") and "did not succeed" in e["title"]


def test_system_block_after_approval_is_a_warning_and_a_refused_preview_is_silent():
    assert operation_event(op(status="blocked", error=None))["severity"] == "warning"
    assert operation_event(op(status="blocked", approved_at=None)) is None      # a preview that was refused: nobody approved it
    assert operation_event(op(status="completed")) is None


def test_unknown_types_still_get_a_readable_label_and_category_exists():
    e = operation_event(op(operation_type_id="storage.some_new_thing"))
    assert "storage some new thing" in e["title"].lower()
    assert "operation" in CATEGORY_KEYS and "snapshot" in CATEGORY_KEYS


def test_a_failed_preview_nobody_approved_is_not_an_alert():
    from types import SimpleNamespace
    op = SimpleNamespace(operation_type_id="backup.policy_change", status="failed", error="the preview did not finish", approved_at=None, dry_run_result=None, context={})
    assert operation_event(op) is None
