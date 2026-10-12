"""Host pinning: how the rules covering a guest combine."""

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.pins import compute_pin_info, rule_covers
from pyxie_core.recommendations import left_alone_reason

A, B, C = (str(uuid.uuid4()) for _ in range(3))
WL = NS(id=uuid.uuid4(), tags=["dns"], node_id=uuid.UUID(A), do_not_move=False)


def rule(nodes, *, strict=False, workload=None, tag=None):
    return NS(id=uuid.uuid4(), scope_type="workload" if workload is not None else "tag_group", workload_id=workload.id if workload else None,
              tag=tag, node_ids=nodes, strict=strict)


def test_rules_cover_by_guest_or_by_tag():
    assert rule_covers(rule([A], workload=WL), WL)
    assert rule_covers(rule([A], tag="dns"), WL)
    assert not rule_covers(rule([A], tag="web"), WL)
    assert not rule_covers(rule([A], workload=NS(id=uuid.uuid4())), WL)


def test_soft_pin_prefers_every_listed_host_and_blocks_none():
    info = compute_pin_info([rule([A, B], workload=WL)], WL)
    assert info.soft_nodes == {A, B} and info.hard_nodes is None and info.pinned
    assert info.on_pin(A) and info.on_pin(B) and not info.on_pin(C)


def test_hard_pins_intersect_and_a_soft_rule_does_not_loosen_them():
    info = compute_pin_info([rule([A, B], strict=True, workload=WL), rule([B, C], strict=True, tag="dns"), rule([C], workload=WL)], WL)
    assert info.hard_nodes == {B}  # only what every hard rule allows
    assert info.soft_nodes == {A, B, C}
    assert compute_pin_info([], WL).hard_nodes is None and not compute_pin_info([], WL).pinned


def test_balance_leaves_a_guest_alone_on_any_pinned_host():
    rules = [rule([A, B], workload=WL)]
    assert left_alone_reason(WL, rules) == "pinned"
    moved = NS(**{**WL.__dict__, "node_id": uuid.UUID(C)})
    assert left_alone_reason(moved, rules) is None  # not on a pinned host: balancing may bring it back
    assert left_alone_reason(WL, []) is None
    assert left_alone_reason(NS(**{**WL.__dict__, "do_not_move": True}), []) == "do_not_move"
