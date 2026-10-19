from types import SimpleNamespace as NS

from pyxie_core.mem_pressure import memory_pressure_verdict
from pyxie_core.recommendations import _rightsizing_recommendations
from pyxie_core.rightsizing import _compute_assessment

GIB = 1 << 30


def summary(days=14, swap_in=0, free=1000, low=0):
    return {"days": days, "samples": free, "swap_in_bytes": swap_in, "swap_out_bytes": 0, "free_samples": free, "low_free_samples": low}


def test_windows_swap_in_counter_alone_is_not_pressure_but_low_free_memory_is():
    busy_counter = summary(swap_in=200 * GIB, low=0)
    assert memory_pressure_verdict(busy_counter, windows=False)["state"] == "pressure"      # the old rule, still right for Linux
    assert memory_pressure_verdict(busy_counter, windows=True)["state"] == "no_pressure"    # the same numbers on Windows mean nothing
    short = memory_pressure_verdict(summary(low=800), windows=True)
    assert short["state"] == "pressure" and "under 10%" in short["detail"]


def test_windows_with_no_free_memory_readings_is_still_gathering():
    assert memory_pressure_verdict(summary(free=0), windows=True)["state"] == "gathering"
    assert memory_pressure_verdict(summary(days=3), windows=True)["state"] == "gathering"


def wl(cores=8, os="l26", mem=16 * GIB):
    return NS(id="w1", cluster_id="c1", node_id="n1", vmid=1, name="g", cpu_cores=cores, memory_bytes=mem, os_type=os, status="running", type="vm", mem_guest_stats=True)


def stats(p95, p99, mx):
    return {"p95": p95, "p99": p99, "max": mx, "avg": 5, "earliest": None, "latest": None, "samples": 1000}


def test_cpu_is_never_cut_by_more_than_half_in_one_step():
    a = _compute_assessment(wl(cores=8), stats(5, 8, 70), stats(30, 40, 50), 75, 75, history_days=100)
    cpu = a["cpu_suggestion"]
    assert cpu["direction"] == "decrease" and cpu["suggested"] == 4 and "at most half" in cpu["reason"]


def test_a_small_cut_is_left_alone():
    a = _compute_assessment(wl(cores=8), stats(30, 40, 70), stats(30, 40, 50), 75, 75, history_days=100)
    assert a["cpu_suggestion"] is None or a["cpu_suggestion"]["suggested"] >= 4


def test_a_windows_guest_with_busy_swap_counter_but_healthy_free_memory_gets_no_memory_increase():
    m = stats(99, 100, 100)
    a = _compute_assessment(wl(os="win11"), stats(10, 20, 40), m, 75, 75, pressure=summary(swap_in=500 * GIB, low=0), history_days=100)
    assert a["memory_suggestion"] is None and "Windows guest" in a["memory_note"]


def test_a_decrease_only_recommendation_is_info_even_on_the_scarce_resource_and_an_increase_stays_critical():
    base = {"workload_id": "w1", "cluster_id": "c1", "name": "g", "vmid": 1, "cpu": {}, "memory": {}, "confidence": "high", "observation_days": 100}
    dec = dict(base, cpu_suggestion={"current": 8, "suggested": 4, "direction": "decrease", "reason": ""}, memory_suggestion=None)
    inc = dict(base, workload_id="w2", cpu_suggestion=None, memory_suggestion={"current_bytes": 8 * GIB, "suggested_bytes": 10 * GIB, "direction": "increase", "reason": ""})
    recs = _rightsizing_recommendations([dec, inc], [{"cluster_id": "c1", "most_constrained_resource": "cpu"}, ])
    by = {r["object_id"]: r["severity"] for r in recs}
    assert by["w1"] == "info"
    cap = [{"cluster_id": "c1", "most_constrained_resource": "memory"}]
    assert {r["object_id"]: r["severity"] for r in _rightsizing_recommendations([inc], cap)}["w2"] == "critical"
