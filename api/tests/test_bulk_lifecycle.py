"""Bulk power plan: which guests run and which are listed as skipped (pure, no database)."""

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

import pytest

from pyxie_core.bulk_lifecycle_workflow import ACTIONS, BulkLifecycleError, classify
from pyxie_core.lifecycle_workflow import blocking_reasons


def vm(name, status="running", tolerance=None, ha=None, vmid=100):
    return SimpleNamespace(id=uuid.uuid4(), node_id=uuid.uuid4(), vmid=vmid, name=name, status=status, downtime_tolerance=tolerance, ha_state=ha)


def test_shutdown_runs_on_running_guests_and_lists_the_rest_with_reasons():
    plan, skipped = classify([vm("a"), vm("b", status="stopped"), vm("c", ha="started"), vm("d", tolerance="low")], "shutdown")
    assert [p["name"] for p in plan] == ["a"]
    by = {s["name"]: s for s in skipped}
    assert set(by) == {"b", "c", "d"}
    assert "SAFE-MIGRATE-001" in by["b"]["blocking_safety_rules"]
    assert "SAFE-HA-001" in by["c"]["blocking_safety_rules"]
    assert "SAFE-DOWNTIME-001" in by["d"]["blocking_safety_rules"]


def test_start_only_runs_on_stopped_guests_and_ignores_downtime_and_ha_rules():
    plan, skipped = classify([vm("a", status="stopped", tolerance="low", ha="started"), vm("b", status="running")], "start")
    assert [p["name"] for p in plan] == ["a"]
    assert [s["name"] for s in skipped] == ["b"]


def test_force_stop_has_the_same_guards_as_shutdown():
    plan, skipped = classify([vm("a"), vm("b", ha="started")], "force_stop")
    assert [p["name"] for p in plan] == ["a"] and [s["name"] for s in skipped] == ["b"]


def test_plan_items_carry_what_the_screen_and_the_runner_need():
    w = vm("a", vmid=101)
    plan, _ = classify([w], "shutdown")
    assert plan == [{"workload_id": str(w.id), "vmid": 101, "name": "a", "node_id": str(w.node_id), "power": "running"}]


def test_unknown_action_is_refused():
    with pytest.raises(BulkLifecycleError):
        classify([vm("a")], "reboot")


def test_same_rules_as_the_single_guest_preview():
    w = vm("a", status="stopped")
    reasons, rules = blocking_reasons(w, "workload.shutdown", "running")
    assert rules == ["SAFE-MIGRATE-001"] and "expected 'running'" in reasons[0]
    assert blocking_reasons(vm("b"), "workload.shutdown", "running") == ([], [])
    assert set(ACTIONS) == {"shutdown", "start", "force_stop"}


def test_parallel_limit_defaults_and_clamps():
    from pyxie_core.bulk_lifecycle_workflow import DEFAULT_PARALLEL, MAX_PARALLEL, clamp_parallel
    assert clamp_parallel(None) == DEFAULT_PARALLEL == 3
    assert clamp_parallel("abc") == 3
    assert clamp_parallel(0) == 1 and clamp_parallel(-5) == 1
    assert clamp_parallel(4) == 4 and clamp_parallel(99) == MAX_PARALLEL == 10
