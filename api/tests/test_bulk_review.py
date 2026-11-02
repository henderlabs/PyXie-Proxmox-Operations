"""Bulk Migrate review step: per-VM storage picks are validated against the destination's options."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.balance_workflow import BalanceWorkflowError, _apply_bulk_choices


def item():
    return {
        "workload_id": "w1", "name": "st-jellyfin", "destination_node_id": "n1", "destination_node": "st-pve105",
        "destination_storage_id": "s-local", "currently_on_shared": False, "candidates": [{"node_id": "n1", "node_name": "st-pve105"}],
        "storage_options": [{"id": "s-local", "name": "intel-ssd-105", "scope": "node-local"}, {"id": "s-nas", "name": "NAS", "scope": "cluster-shared"}],
    }


def test_a_storage_offered_by_the_destination_is_applied_and_marked_manual():
    plan = [item()]
    _apply_bulk_choices(None, plan, {"w1": {"destination_storage_id": "s-nas"}})
    assert plan[0]["destination_storage_id"] == "s-nas" and plan[0]["manually_set"] is True


def test_a_storage_the_destination_does_not_offer_is_refused_not_ignored():
    plan = [item()]
    with pytest.raises(BalanceWorkflowError):
        _apply_bulk_choices(None, plan, {"w1": {"destination_storage_id": "s-elsewhere"}})
    assert plan[0]["destination_storage_id"] == "s-local"


def test_choices_for_vms_not_in_the_plan_are_ignored_and_empty_choices_change_nothing():
    plan = [item()]
    _apply_bulk_choices(None, plan, {"gone": {"destination_storage_id": "s-nas"}, "w1": {}})
    assert plan[0]["destination_storage_id"] == "s-local" and "manually_set" not in plan[0]


def test_a_destination_that_was_never_offered_is_refused():
    plan = [item()]
    with pytest.raises(BalanceWorkflowError):
        _apply_bulk_choices(None, plan, {"w1": {"destination_node_id": "n9"}})
