from pyxie_core.balance_config import guest_based_mem_pct

GIB = 1 << 30


def test_sums_each_current_guests_busy_level_plus_host_overhead():
    guests = [{"busy": 8 * GIB, "live": 3 * GIB}, {"busy": 4 * GIB, "live": 4 * GIB}]
    # node: 32 GiB, uses 12 GiB right now = 7 GiB guests + 5 GiB host overhead
    pct = guest_based_mem_pct(32 * GIB, 12 * GIB, guests)
    assert round(pct, 1) == round((8 + 4 + 5) / 32 * 100, 1)


def test_a_node_that_used_to_host_more_is_not_judged_by_the_past():
    # busy level comes from the guests that are here now: 2 guests of 2 GiB busy on a 32 GiB node using 7 GiB
    pct = guest_based_mem_pct(32 * GIB, 7 * GIB, [{"busy": 2 * GIB, "live": 1 * GIB}, {"busy": 2 * GIB, "live": 1 * GIB}])
    assert round(pct, 1) == round((2 + 2 + 5) / 32 * 100, 1)  # 4 GiB busy guests + 5 GiB host overhead, not the 50%+ the node's own week of history might say


def test_overhead_is_never_negative_and_a_guest_without_history_counts_at_its_live_use():
    assert round(guest_based_mem_pct(10 * GIB, 1 * GIB, [{"busy": 2 * GIB, "live": 2 * GIB}]), 1) == 20.0  # live guests exceed the node figure: overhead floors at 0
    assert round(guest_based_mem_pct(10 * GIB, 4 * GIB, [{"busy": None, "live": 3 * GIB}]), 1) == 40.0      # 3 GiB (live, no history) + 1 GiB overhead


def test_no_total_means_no_answer():
    assert guest_based_mem_pct(0, 1, []) is None and guest_based_mem_pct(None, 1, []) is None
