"""A guest sitting on a pinned host (or marked Do not move) is never considered by Balance Load. Pure logic."""

import uuid
from types import SimpleNamespace

from pyxie_core.recommendations import left_alone_reason


def wl(node, dnm=False):
    return SimpleNamespace(id=uuid.UUID(int=1), node_id=node, do_not_move=dnm, tags=[])


def pin(*nodes):
    return [SimpleNamespace(id=uuid.uuid4(), scope_type="workload", workload_id=uuid.UUID(int=1), tag=None, node_ids=list(nodes), strict=False)]


def test_guest_on_its_pin_is_left_alone():
    assert left_alone_reason(wl("a"), pin("a")) == "pinned"
    assert left_alone_reason(wl("b"), pin("a", "b")) == "pinned"  # any one of several pinned hosts


def test_guest_away_from_its_pin_may_move_toward_it():
    assert left_alone_reason(wl("b"), pin("a")) is None


def test_unpinned_guest_is_free_to_move():
    assert left_alone_reason(wl("a"), []) is None
    assert left_alone_reason(wl("a")) is None


def test_do_not_move_wins_and_is_reported_as_such():
    assert left_alone_reason(wl("a", dnm=True), pin("a")) == "do_not_move"
    assert left_alone_reason(wl("a", dnm=True)) == "do_not_move"
