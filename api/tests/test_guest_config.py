"""Config tab: option validation, diffing, startup and agent handling (pure)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.guest_config import (
    CT, VM, ConfigValueError, agent_enabled, agent_with_enabled, comparable, normalize, parse_startup, pending_keys, plan_changes,
)


def test_values_are_checked_against_proxmox_limits_before_any_preview():
    assert normalize(VM["cpulimit"], "2.5") == "2.5" and normalize(VM["cpulimit"], "4.0") == "4"
    with pytest.raises(ConfigValueError):
        normalize(VM["cpulimit"], "500")
    with pytest.raises(ConfigValueError):
        normalize(VM["cpuunits"], "0")
    assert normalize(CT["cpuunits"], "0") == "0"  # containers allow 0
    with pytest.raises(ConfigValueError):
        normalize(VM["name"], "bad name!")
    assert normalize(VM["name"], "st-jellyfin") == "st-jellyfin"
    with pytest.raises(ConfigValueError):
        normalize(VM["boot"], "order=scsi0;rm -rf /")
    assert normalize(VM["boot"], "order=scsi0;net0") == "order=scsi0;net0"
    assert normalize(VM["ostype"], "l26") == "l26"
    with pytest.raises(ConfigValueError):
        normalize(VM["ostype"], "plan9")


def test_bools_accept_the_usual_spellings_and_empty_means_remove():
    assert normalize(VM["onboot"], True) == "1" and normalize(VM["onboot"], "no") == "0" and normalize(VM["onboot"], "on") == "1"
    assert normalize(VM["description"], "") is None and normalize(VM["onboot"], None) is None
    with pytest.raises(ConfigValueError):
        normalize(VM["onboot"], "maybe")


def test_startup_is_built_from_its_parts_and_parsed_back():
    assert normalize(VM["startup"], {"order": 2, "up": 30, "down": ""}) == "order=2,up=30"
    assert parse_startup("order=2,up=30,down=60") == {"order": 2, "up": 30, "down": 60}
    assert normalize(VM["startup"], {}) is None
    with pytest.raises(ConfigValueError):
        normalize(VM["startup"], {"up": -1})


def test_only_real_changes_are_planned_and_unknown_or_other_panel_options_are_refused():
    current = {"onboot": 1, "description": "old", "cpulimit": 0, "startup": "order=2", "digest": "abc"}
    changes, errors, unchanged = plan_changes("vm", current, {"onboot": "1", "description": "new", "cpulimit": "0", "memory": 4096, "bogus": "x"})
    assert [c["key"] for c in changes] == ["description"] and changes[0]["old"] == "old" and changes[0]["new"] == "new" and changes[0]["apply"] == "live"
    assert set(unchanged) == {"onboot", "cpulimit"}
    assert "Resize" in errors["memory"] and "cannot be changed" in errors["bogus"]


def test_clearing_a_value_is_a_removal_and_restart_options_say_so():
    changes, _e, _u = plan_changes("vm", {"description": "old", "vga": "std"}, {"description": "", "vga": "virtio"})
    by = {c["key"]: c for c in changes}
    assert by["description"]["remove"] is True and by["vga"]["apply"] == "restart" and by["vga"]["priv"] == "VM.Config.HWType"


def test_the_agent_toggle_keeps_its_other_sub_options():
    assert agent_enabled("enabled=1,fstrim_cloned_disks=1") == "1" and agent_enabled("1") == "1" and agent_enabled(None) == "0"
    assert agent_with_enabled("enabled=1,fstrim_cloned_disks=1", False) == "enabled=0,fstrim_cloned_disks=1"
    changes, _e, _u = plan_changes("vm", {"agent": "enabled=1,type=virtio"}, {"agent": False})
    assert changes[0]["value"] == "enabled=0,type=virtio" and changes[0]["old"] == "1"
    _c, _e, unchanged = plan_changes("vm", {"agent": "1"}, {"agent": True})
    assert unchanged == ["agent"]


def test_a_stored_value_compares_equal_to_its_normalized_form():
    assert comparable(VM["onboot"], 1) == "1" and comparable(VM["cpulimit"], "2.0") == "2" and comparable(VM["description"], "") is None
    assert comparable(VM["startup"], "down=60,order=2") == "order=2,down=60"


def test_pending_changes_are_picked_out_of_the_proxmox_listing():
    rows = [{"key": "memory", "value": 2048}, {"key": "bios", "value": "seabios", "pending": "ovmf"}, {"key": "tablet", "value": 1, "delete": 1}]
    assert [p["key"] for p in pending_keys(rows)] == ["bios", "tablet"]
    assert pending_keys(rows)[1]["delete"] is True
