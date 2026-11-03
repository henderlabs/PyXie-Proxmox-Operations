from datetime import datetime, timedelta, timezone

from pyxie_core import locks
from pyxie_core.op_claims import (DEFER_MAX_SECONDS, STALE_SECONDS, claim_is_live, next_retry, should_defer)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
S = lambda n: NOW - timedelta(seconds=n)


def test_a_fresh_claim_by_another_worker_is_live():
    assert claim_is_live("bulk-2", S(30), NOW, me="bulk-1")


def test_a_stale_claim_is_not_live():
    assert not claim_is_live("bulk-2", S(STALE_SECONDS + 5), NOW, me="bulk-1")
    assert not claim_is_live("bulk-2", None, NOW, me="bulk-1")


def test_my_own_old_claim_is_a_dead_predecessor_never_live():
    assert not claim_is_live("bulk-1", S(5), NOW, me="bulk-1")  # same slot id after a restart


def test_no_claim_is_not_live():
    assert not claim_is_live(None, None, NOW)


def test_only_a_persons_top_level_lock_stop_waits():
    rule = ["SAFE-LOCK-001"]
    assert should_defer("revalidating", "phil@example.com", None, rule, None, NOW)
    assert not should_defer("revalidating", "PyXie (automatic)", None, rule, None, NOW)  # automatic work skips and tries next pass
    assert not should_defer("revalidating", "phil@example.com", "parent-id", rule, None, NOW)  # a step inside a bigger run
    assert not should_defer("revalidating", "phil@example.com", None, ["SAFE-RAM-001"], None, NOW)  # real safety stops stay stops
    assert not should_defer("revalidating", "phil@example.com", None, rule + ["SAFE-RAM-001"], None, NOW)


def test_waiting_gives_up_after_six_hours():
    assert should_defer("approved", "phil@example.com", None, ["SAFE-LOCK-001"], NOW - timedelta(seconds=DEFER_MAX_SECONDS - 60), NOW)
    assert not should_defer("approved", "phil@example.com", None, ["SAFE-LOCK-001"], NOW - timedelta(seconds=DEFER_MAX_SECONDS + 60), NOW)


def test_retry_backs_off_and_levels_out():
    gaps = [(next_retry(n, NOW) - NOW).total_seconds() for n in range(7)]
    assert gaps == [60, 120, 240, 300, 300, 300, 300]


def test_locks_are_short_only_where_a_heartbeat_renews_them():
    assert locks._lease_active is False  # the API and other callers keep their full lifetimes
    assert locks.LEASE_SECONDS == 600
