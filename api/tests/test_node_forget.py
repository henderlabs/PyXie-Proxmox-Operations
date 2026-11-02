"""Removing a host from PyXie: only a host PVE no longer lists, and only app-side."""

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.node_forget import forget_block_reason


def node(**kw):
    return SimpleNamespace(**{"name": "st-pve104", "is_missing": True, "forgotten_at": None, **kw})


def test_a_host_pve_no_longer_lists_can_be_removed():
    assert forget_block_reason(node()) is None


def test_a_live_cluster_member_can_never_be_removed_and_the_reason_says_how():
    why = forget_block_reason(node(is_missing=False))
    assert "still lists" in why and "pvecm delnode st-pve104" in why


def test_removing_twice_is_refused():
    assert "already removed" in forget_block_reason(node(forgotten_at=datetime.now(timezone.utc)))
