"""Regression: a migration targeting a per-node storage name (local-lvm, local)
must resolve to the destination node's own storage, not crash on the six rows
that share the name across the cluster ("Multiple rows were found")."""

from types import SimpleNamespace

from pyxie_core.migration_workflow import pick_destination_storage


def _row(name, node_id, scope="node-local"):
    return SimpleNamespace(name=name, node_id=node_id, scope=scope)


def test_per_node_name_picks_destination_nodes_own_row():
    rows = [_row("local-lvm", n) for n in ("n101", "n102", "n103", "n104", "n105", "n106")]
    picked = pick_destination_storage(rows, "n105")
    assert picked is rows[4]


def test_destination_own_row_beats_shared_row_of_same_name():
    own = _row("data", "n105")
    shared = _row("data", None, scope="cluster-shared")
    assert pick_destination_storage([shared, own], "n105") is own


def test_shared_storage_is_used_for_any_destination():
    shared = _row("ST-SYN01-DS01", None, scope="cluster-shared")
    assert pick_destination_storage([shared], "n105") is shared


def test_other_nodes_storage_is_ignored():
    rows = [_row("intel-ssd-101", "n101")]
    assert pick_destination_storage(rows, "n105") is None


def test_no_candidates_is_none():
    assert pick_destination_storage([], "n105") is None
