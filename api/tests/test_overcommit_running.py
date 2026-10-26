"""A node is only memory-overcommitted when the guests actually running need more RAM than it has: stopped guests use none
(st-pve103 was flagged 37.8% over because 33 GB of stopped VMs were counted beside 10 GB running)."""

from types import SimpleNamespace

from pyxie_core.findings import running_allocated_bytes

GB = 1024 ** 3


def _w(gb, status, missing=False):
    return SimpleNamespace(memory_bytes=gb * GB if gb is not None else None, status=status, is_missing=missing)


# st-pve103 as it was: 10 GB running, 33 GB stopped, on a 31.2 GB host
PVE103 = [_w(10, "stopped"), _w(8, "stopped"), _w(5, "stopped"), _w(4, "running"), _w(4, "stopped"), _w(4, "stopped"),
          _w(4, "running"), _w(2, "running"), _w(2, "stopped")]


def test_only_running_guests_count():
    assert running_allocated_bytes(PVE103) == 10 * GB


def test_running_guests_that_really_exceed_ram_still_count():
    many = [_w(16, "running"), _w(16, "running"), _w(8, "stopped")]
    assert running_allocated_bytes(many) == 32 * GB


def test_missing_guests_and_unknown_memory_are_ignored():
    assert running_allocated_bytes([_w(4, "running", missing=True), _w(None, "running"), _w(2, "running")]) == 2 * GB


def test_no_guests_is_zero():
    assert running_allocated_bytes([]) == 0
