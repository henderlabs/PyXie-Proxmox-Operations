"""An online host never really reads exactly 0% memory or CPU: that means the numbers are not arriving."""

import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.node_sanity import live_problem


def node(cpu, mem, status="online"):
    return NS(status=status, cpu_usage_pct=cpu, mem_usage_pct=mem)


def test_normal_and_very_low_readings_are_fine():
    assert live_problem(node(3.7, 13.2)) is None
    assert live_problem(node(0.4, 13.2)) is None  # a quiet host can read well under 1%; only an exact 0 is suspicious


def test_exact_zero_or_missing_on_an_online_node_is_flagged():
    assert "CPU reads exactly 0%" in live_problem(node(0.0, 13.2))
    assert "memory reads exactly 0%" in live_problem(node(5.0, 0.0))
    assert "no CPU or memory reading" in live_problem(node(None, 13.2))
    assert "no CPU or memory reading" in live_problem(node(5.0, None))


def test_an_offline_node_is_not_judged():
    assert live_problem(node(0.0, 0.0, status="offline")) is None
    assert live_problem(node(None, None, status="unknown")) is None
