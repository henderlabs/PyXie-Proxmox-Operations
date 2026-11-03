"""Clearing stale VM locks: which locked guests the plan clears and which it leaves alone (pure)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.clear_lock_workflow import split_candidates

STALE = {"vmid": 11158, "lock": "backup", "config_age_seconds": 90000, "task_running": False, "stale": True}


def test_a_stale_lock_with_no_task_is_cleared():
    clear, leave = split_candidates([STALE], {11158: "stale"})
    assert [r["vmid"] for r in clear] == [11158] and leave == []


def test_a_lock_with_a_running_task_is_never_cleared_by_either_side():
    clear, leave = split_candidates([{**STALE, "task_running": True}], {11158: "stale"})
    assert clear == [] and "task is running" in leave[0]["why"]
    clear, leave = split_candidates([STALE], {11158: "active"})  # the wrapper saw nothing but PyXie sees a task
    assert clear == [] and "task is running" in leave[0]["why"]


def test_a_lock_the_wrapper_does_not_call_stale_is_left_alone():
    clear, leave = split_candidates([{**STALE, "stale": False, "config_age_seconds": 60}], {11158: None})
    assert clear == [] and "not stale yet" in leave[0]["why"]
