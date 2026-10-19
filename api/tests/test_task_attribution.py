from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from pyxie_core.task_attribution import match_operation

T0 = datetime(2026, 10, 17, 5, 34, tzinfo=timezone.utc)
NOW = T0 + timedelta(days=2)


def op(status="completed", wid=None, node="n104", created=T0 - timedelta(minutes=1), done=T0 + timedelta(minutes=2)):
    return NS(status=status, workload_id=wid, node_id=node, created_at=created, completed_at=done)


def test_guest_task_matches_the_guest_operation_in_its_window():
    o = op(wid="g1")
    assert match_operation(T0, "n104", "g1", [o], NOW) is o


def test_host_task_is_not_credited_to_a_guest_operation_on_the_same_host():
    assert match_operation(T0, "n104", None, [op(wid="g1")], NOW) is None


def test_host_task_matches_a_host_level_operation():
    o = op(wid=None)
    assert match_operation(T0, "n104", None, [o], NOW) is o


def test_preview_nobody_approved_explains_nothing_even_though_its_window_is_open_ended():
    never_done = op(status="awaiting_approval", wid=None, done=None)
    assert match_operation(T0, "n104", None, [never_done], NOW) is None


def test_cancelled_and_blocked_operations_explain_nothing():
    assert match_operation(T0, "n104", "g1", [op(status="cancelled", wid="g1"), op(status="blocked", wid="g1")], NOW) is None


def test_a_task_outside_the_window_is_not_matched():
    late = T0 + timedelta(hours=5)
    assert match_operation(late, "n104", "g1", [op(wid="g1")], NOW) is None


def test_a_finished_operation_without_an_end_time_only_covers_thirty_minutes():
    o = op(status="failed", wid="g1", done=None)
    assert match_operation(T0 + timedelta(minutes=20), "n104", "g1", [o], NOW) is o
    assert match_operation(T0 + timedelta(hours=3), "n104", "g1", [o], NOW) is None


def test_the_operation_that_started_closest_to_the_task_wins():
    near, far = op(wid="g1", created=T0 - timedelta(seconds=5)), op(wid="g1", created=T0 - timedelta(minutes=1))
    assert match_operation(T0, "n104", "g1", [far, near], NOW) is near


def test_no_start_time_means_no_match():
    assert match_operation(None, "n104", "g1", [op(wid="g1")], NOW) is None
