"""Bulk editor field list and the two-guest compare table (pure)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.guest_config import bulk_fields, compare_rows


def test_bulk_offers_only_single_value_settings_not_unique_to_a_guest():
    keys = {f["key"] for f in bulk_fields("vm")}
    assert {"onboot", "protection", "cpulimit", "cpuunits", "ostype", "description"} <= keys
    assert "name" not in keys and "startup" not in keys
    ct = {f["key"] for f in bulk_fields("ct")}
    assert "hostname" not in ct and "onboot" in ct
    assert all(f["type"] in ("bool", "int", "float", "enum", "text") for f in bulk_fields("vm"))


def test_compare_marks_only_real_differences_and_treats_missing_like_empty():
    a = {"onboot": 1, "cpuunits": 1024, "description": "web", "agent": "1"}
    b = {"onboot": "1", "cpuunits": 2048, "agent": "0"}
    rows = {r["key"]: r for r in compare_rows("vm", a, b)}
    assert not rows["onboot"]["differs"]  # 1 vs "1" is the same setting
    assert rows["cpuunits"]["differs"] and rows["cpuunits"]["a"] == "1024" and rows["cpuunits"]["b"] == "2048"
    assert rows["description"]["differs"] and rows["description"]["b"] is None
    assert rows["agent"]["differs"]
    assert not rows["protection"]["differs"]
