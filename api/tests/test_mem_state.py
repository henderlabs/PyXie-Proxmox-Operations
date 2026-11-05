from types import SimpleNamespace as NS

from pyxie_core import mem_pressure as mp


def _w(i, **kw):
    base = dict(id=i, type="vm", status="running", mem_guest_stats=True, os_type="l26")
    base.update(kw)
    return NS(**base)


def test_only_high_running_guest_stat_vms_get_a_verdict(monkeypatch):
    summ = {1: {"days": 10, "samples": 100, "swap_in_bytes": 0, "swap_out_bytes": 0, "free_samples": 0, "low_free_samples": 0},
            4: {"days": 10, "samples": 100, "swap_in_bytes": 50 * 1024**3, "swap_out_bytes": 0, "free_samples": 0, "low_free_samples": 0}}
    monkeypatch.setattr(mp, "pressure_summaries", lambda db: summ)
    ws = [_w(1), _w(2), _w(3, status="stopped"), _w(4), _w(5, mem_guest_stats=False), _w(6, type="lxc")]
    pcts = {"1": 89, "2": 50, "3": 95, "4": 95, "5": 99, "6": 99}
    out = mp.memory_states(None, ws, pcts)
    assert set(out) == {"1", "4"}
    assert out["1"]["state"] == "no_pressure"
    assert out["4"]["state"] == "pressure"


def test_not_enough_history_says_gathering(monkeypatch):
    monkeypatch.setattr(mp, "pressure_summaries", lambda db: {})
    out = mp.memory_states(None, [_w(1)], {"1": 90})
    assert out["1"]["state"] == "gathering"
