from pyxie_core.parallel_children import pick_next

dest = lambda item: item["to"]


def test_one_at_a_time_by_default_keeps_the_old_order():
    pending = [{"to": "B"}, {"to": "C"}, {"to": "B"}]
    assert pick_next(pending, {}, 0, 1, 1, 1, dest) == 0
    assert pick_next(pending, {"B": 1}, 1, 1, 1, 1, dest) is None  # one already running: wait for it


def test_two_at_once_when_the_limits_allow_it():
    pending = [{"to": "B"}, {"to": "C"}]
    assert pick_next(pending, {"B": 1}, 1, 2, 2, 1, dest) == 1  # B is full (1 in), so the next one goes to C
    assert pick_next(pending, {"B": 1, "C": 1}, 2, 2, 2, 1, dest) is None  # k reached


def test_the_source_host_limit_caps_parallel_moves_even_when_k_is_higher():
    pending = [{"to": "B"}, {"to": "C"}]
    assert pick_next(pending, {"B": 1}, 1, 3, 1, 3, dest) is None  # only one may leave the source at a time


def test_a_destination_that_is_full_is_skipped_not_waited_on():
    pending = [{"to": "B"}, {"to": "B"}, {"to": "C"}]
    assert pick_next(pending, {"B": 2}, 2, 4, 4, 2, dest) == 2


def test_nothing_pending_nothing_to_start():
    assert pick_next([], {}, 0, 3, 3, 3, dest) is None
