"""Balance Load reviews affinity rules and pins: which guests would have to move for each rule to be met."""

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.rule_review import review_rules_pure

A, B, C = "node-a", "node-b", "node-c"
NAMES = {A: "pve-a", B: "pve-b", C: "pve-c"}


def vm(name, node, mem=1, tags=(), dnm=False):
    return NS(id=uuid.uuid4(), name=name, vmid=1, node_id=node, memory_bytes=mem, tags=list(tags), do_not_move=dnm)


def aff(kind, a, b=None, tag=None, strict=True):
    return NS(id=uuid.uuid4(), rule_type=kind, scope_type="tag_group" if tag else "workload_pair", tag=tag, description=None,
              workload_ids=[str(a.id), str(b.id)] if b else None, strict=strict)


def pin(w, *nodes, strict=False):
    return NS(id=uuid.uuid4(), scope_type="workload", workload_id=w.id, tag=None, node_ids=list(nodes), strict=strict)


def test_everything_met_is_clean():
    x, y = vm("x", A), vm("y", B)
    rv = review_rules_pure([aff("keep_apart", x, y)], [pin(x, A)], [x, y], NAMES)
    assert rv.ok and not rv.violators and "all met" in rv.sentence()


def test_keep_apart_on_one_node_moves_the_smaller_guest():
    big, small = vm("big", A, mem=8), vm("small", A, mem=2)
    rv = review_rules_pure([aff("keep_apart", big, small)], [], [big, small], NAMES)
    assert not rv.ok and list(rv.violators) == [str(small.id)]


def test_keep_together_moves_guests_to_where_most_of_them_are():
    a1, a2, b1 = vm("a1", A), vm("a2", A), vm("b1", B)
    rv = review_rules_pure([aff("keep_together", None, tag="app")], [], [vm("x", C), a1, a2, b1], NAMES)  # no tagged guests: nothing to review
    assert rv.ok
    t = [vm("t1", A, tags=["app"]), vm("t2", A, tags=["app"]), vm("t3", B, tags=["app"])]
    rv = review_rules_pure([aff("keep_together", None, tag="app")], [], t, NAMES)
    assert list(rv.violators) == [str(t[2].id)]


def test_pin_off_its_hosts_names_the_guest_and_do_not_move_is_left_alone():
    x = vm("x", C)
    rv = review_rules_pure([], [pin(x, A, B)], [x], NAMES)
    assert list(rv.violators) == [str(x.id)] and "pin" in rv.issues[0] and "pve-a or pve-b" in rv.issues[0]
    y = vm("y", C, dnm=True)
    rv = review_rules_pure([], [pin(y, A)], [y], NAMES)
    assert rv.issues and not rv.violators  # reported, but PyXie will not move a Do not move guest


def test_auto_approve_never_migrates_the_vm_pyxie_runs_on():
    from pyxie_core.auto_balance import auto_approve_blockers
    plan = [{"vmid": 11166, "transport": "live"}]
    assert any("PyXie itself" in r for r in auto_approve_blockers(plan, [], writes_enabled=True, busy_nodes=False, self_vmid="11166"))
    assert not any("PyXie itself" in r for r in auto_approve_blockers(plan, [], writes_enabled=True, busy_nodes=False, self_vmid="1"))


def test_rule_fixes_are_planned_before_load_balancing():
    from pyxie_core.recommendations import rules_first
    big, mid, small = NS(id=1, mem=8), NS(id=2, mem=4), NS(id=3, mem=1)
    ordered = rules_first([big, mid, small], {3})
    assert [w.id for w in ordered] == [3, 1, 2]  # the rule-fix guest first, the rest keep their biggest-first order
    assert rules_first([big, mid, small], set()) == [big, mid, small]
    assert rules_first([big, mid, small], None) == [big, mid, small]
