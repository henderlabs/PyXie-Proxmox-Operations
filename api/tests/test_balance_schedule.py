"""How often automatic balancing checks: slots, whether a check is owed, validation."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.balance_config import CHECK_EVERY, ConfigError, check_due, latest_slot, next_slot, validate_config

UTC = timezone.utc


def t(h, m=0, day=12):
    return datetime(2026, 10, day, h, m, tzinfo=UTC)


def test_minute_steps_sit_on_the_clock():
    assert latest_slot(t(10, 23), "15m", "00:00") == t(10, 15)
    assert latest_slot(t(10, 29), "30m", "00:00") == t(10, 0)
    assert latest_slot(t(10, 3), "5m", "00:00") == t(10, 0)
    assert next_slot(t(10, 3), "5m", "00:00") == t(10, 5)


def test_hour_steps_count_from_the_chosen_time():
    assert latest_slot(t(9, 0), "4h", "02:00") == t(6, 0)
    assert latest_slot(t(1, 30), "4h", "02:00") == t(22, 0, day=11)  # before today's first slot: yesterday's last
    assert next_slot(t(9, 0), "4h", "02:00") == t(10, 0)
    assert latest_slot(t(15, 0), "1h", "00:30") == t(14, 30)


def test_daily_runs_once_at_the_chosen_time():
    assert latest_slot(t(2, 59), "daily", "03:00") == t(3, 0, day=11)
    assert latest_slot(t(3, 0), "daily", "03:00") == t(3, 0)
    assert next_slot(t(10, 0), "daily", "03:00") == t(3, 0, day=13)


def test_a_check_is_owed_only_once_per_slot():
    assert check_due(t(10, 0), None, "4h", "02:00")  # never checked: do it now
    assert check_due(t(10, 1), t(9, 59), "4h", "02:00")  # the 10:00 slot has passed since the last check
    assert not check_due(t(11, 0), t(10, 0, ), "4h", "02:00")  # already checked in this slot
    assert not check_due(t(11, 0), t(10, 1), "4h", "02:00")
    assert check_due(t(14, 0), t(10, 1), "4h", "02:00")  # next slot arrived
    assert not check_due(t(3, 30), t(3, 1), "daily", "03:00")
    assert check_due(t(3, 5, day=13), t(3, 1), "daily", "03:00")


def test_every_choice_divides_a_day_and_validation_rejects_the_rest():
    assert all(1440 % m == 0 for m in CHECK_EVERY.values())
    ok = validate_config({"mode": "recommend", "check_every": "2h", "check_at": "03:30"})
    assert ok["check_every"] == "2h" and ok["check_at"] == "03:30"
    assert validate_config({"mode": "off"})["check_every"] == "5m"
    for bad in ({"check_every": "7m"}, {"check_every": "daily", "check_at": "25:00"}, {"check_every": "daily", "check_at": "3am"}):
        with pytest.raises(ConfigError):
            validate_config({"mode": "off", **bad})
