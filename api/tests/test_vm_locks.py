"""Stale VM lock classification (pure)."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.vm_locks import age_text, classify_lock

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def test_a_running_task_always_means_active_however_old_the_lock():
    assert classify_lock(NOW - timedelta(days=3), NOW, True) == "active"


def test_a_lock_with_no_task_is_recent_until_the_threshold_then_stale():
    assert classify_lock(NOW - timedelta(minutes=29), NOW, False, 30) == "recent"
    assert classify_lock(NOW - timedelta(minutes=30), NOW, False, 30) == "stale"
    assert classify_lock(None, NOW, False, 30) == "recent"


def test_the_threshold_has_a_ten_minute_floor():
    assert classify_lock(NOW - timedelta(minutes=9), NOW, False, 1) == "recent"
    assert classify_lock(NOW - timedelta(minutes=10), NOW, False, 1) == "stale"


def test_age_reads_in_plain_units():
    assert age_text(NOW - timedelta(minutes=12), NOW) == "12 min"
    assert age_text(NOW - timedelta(minutes=135), NOW) == "2 h 15 min"
    assert age_text(NOW - timedelta(hours=51), NOW) == "2 d 3 h"
    assert age_text(None, NOW) == "just now"
