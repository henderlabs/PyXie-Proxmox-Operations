"""A migration must never be suggested, or allowed, when the target storage cannot
hold the guest's disks (a 256 GB guest planned onto a 141 GB local-lvm pool)."""

from types import SimpleNamespace

from pyxie_core.placement import capacity_shortfall_reason

GB = 1024 ** 3


def _storage(capacity_gb, used_gb=0, name="local-lvm"):
    return SimpleNamespace(
        name=name, capacity_bytes=None if capacity_gb is None else capacity_gb * GB, used_bytes=used_gb * GB,
    )


def test_disks_larger_than_the_pool_are_refused():
    reason = capacity_shortfall_reason(_storage(141), 256 * GB)
    assert reason and "local-lvm" in reason and "141 GB free" in reason and "256 GB" in reason


def test_free_space_not_total_size_is_what_counts():
    assert capacity_shortfall_reason(_storage(500, used_gb=400), 150 * GB)


def test_exact_fit_is_allowed():
    assert capacity_shortfall_reason(_storage(100, used_gb=40), 60 * GB) is None


def test_plenty_of_room_is_allowed():
    assert capacity_shortfall_reason(_storage(794), 256 * GB) is None


def test_unknown_size_or_no_storage_or_no_disks_is_not_refused():
    assert capacity_shortfall_reason(_storage(None), 256 * GB) is None
    assert capacity_shortfall_reason(None, 256 * GB) is None
    assert capacity_shortfall_reason(_storage(141), 0) is None
    assert capacity_shortfall_reason(_storage(141), None) is None


def test_overfull_storage_counts_as_zero_free():
    assert capacity_shortfall_reason(_storage(100, used_gb=120), GB)
