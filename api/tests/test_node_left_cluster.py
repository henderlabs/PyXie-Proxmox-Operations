"""A host that left the cluster cannot stay in maintenance mode: discovery clears the flag once, PyXie-only."""

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.discovery import clear_maintenance_for_missing


def node(**kw):
    return SimpleNamespace(**{
        "name": "st-pve104", "maintenance_mode": True,
        "maintenance_mode_since": datetime(2026, 10, 29, 22, 5, tzinfo=timezone.utc),
        "maintenance_reason": "re-rack", "maintenance_mode_by": "admin@example.test", **kw,
    })


def test_the_flag_is_cleared_and_the_note_says_who_and_when():
    n = node()
    note = clear_maintenance_for_missing(n)
    assert n.maintenance_mode is False
    assert n.maintenance_mode_since is None and n.maintenance_reason is None and n.maintenance_mode_by is None
    assert "st-pve104 left the cluster" in note and "2026-10-29" in note and "admin@example.test" in note


def test_a_host_not_in_maintenance_is_left_alone_and_nothing_is_audited():
    n = node(maintenance_mode=False)
    assert clear_maintenance_for_missing(n) is None


def test_clearing_twice_audits_once():
    n = node()
    assert clear_maintenance_for_missing(n)
    assert clear_maintenance_for_missing(n) is None
