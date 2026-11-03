from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from pyxie_core.auto_clear_locks import candidates_by_node, plan_is_safe
from pyxie_core.findings import missed_backup_state

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
H = lambda n: NOW - timedelta(hours=n)


def test_recent_backup_is_fine():
    assert missed_backup_state(H(20), H(500), NOW, 30, 72) is None


def test_warning_then_critical():
    assert missed_backup_state(H(31), H(500), NOW, 30, 72)[:1] == ("warning",)
    sev, age, never = missed_backup_state(H(80), H(500), NOW, 30, 72)
    assert sev == "critical" and never is False and round(age) == 80


def test_never_backed_up_waits_a_day():
    assert missed_backup_state(None, H(20), NOW, 12, 72) is None  # young guest, even with a 12 h threshold
    sev, _age, never = missed_backup_state(None, H(40), NOW, 30, 72)
    assert sev == "warning" and never is True


def test_zero_turns_it_off():
    assert missed_backup_state(H(500), H(900), NOW, 0, 72) is None


def test_critical_off_when_zero():
    assert missed_backup_state(H(500), H(900), NOW, 30, 0)[0] == "warning"


def _g(vmid, lock, state, node="n1"):
    return {"workload": SimpleNamespace(vmid=vmid), "lock": lock, "state": state, "node": node}


def test_only_stale_backup_and_snapshot_locks_are_candidates():
    rows = [_g(1, "backup", "stale"), _g(2, "migrate", "stale"), _g(3, "snapshot", "stale", "n2"), _g(4, "backup", "recent"), _g(5, "backup", "active")]
    assert candidates_by_node(rows) == {"n1": [1], "n2": [3]}


def test_plan_must_match_exactly_what_was_wanted():
    assert plan_is_safe([{"vmid": 1, "lock": "backup"}], {1, 2})
    assert not plan_is_safe([{"vmid": 1, "lock": "migrate"}], {1})  # wrapper would clear a migrate lock
    assert not plan_is_safe([{"vmid": 9, "lock": "backup"}], {1})  # a guest we did not ask for
    assert not plan_is_safe([], {1})


def test_certificate_alarm_scales_with_lifetime():
    from pyxie_core.edge_probe import renewal_overdue
    assert not renewal_overdue(11, 12)  # Caddy default 12 h leaf, freshly issued: fine
    assert renewal_overdue(1.0, 12)
    assert not renewal_overdue(24, 72)  # 72 h leaf, a third left: Caddy renews about now
    assert renewal_overdue(8, 72)  # under 15% of 72 h
    assert renewal_overdue(3, 168) and not renewal_overdue(30, 168)
