from pyxie_core.capacity import host_failure_check

GIB = 1 << 30


def node(i, total=32, online=True):
    return {"id": i, "name": f"n{i}", "online": online, "mem_total": total * GIB}


def guest(n, gib, status="running", ha=None):
    return {"node_id": n, "status": status, "memory_bytes": gib * GIB, "ha_state": ha}


def test_a_host_whose_guests_fit_in_the_spare_memory_elsewhere_fits():
    r = host_failure_check([node(1), node(2), node(3)], [guest(1, 10), guest(2, 4), guest(3, 4)])
    assert all(x["fits"] for x in r)
    first = r[0]
    assert first["needs_bytes"] == 10 * GIB and first["spare_bytes"] == 2 * (int(32 * GIB * 0.9) - 4 * GIB)


def test_overcommitted_cluster_reports_the_shortfall():
    r = host_failure_check([node(1), node(2)], [guest(1, 30), guest(2, 20)])
    n1 = r[0]
    assert not n1["fits"] and n1["short_bytes"] == 30 * GIB - (int(32 * GIB * 0.9) - 20 * GIB)


def test_stopped_guests_need_no_memory_and_offline_hosts_give_none():
    r = host_failure_check([node(1), node(2), node(3, online=False)], [guest(1, 8), guest(1, 50, status="stopped"), guest(3, 8)])
    n1 = r[0]
    assert n1["needs_bytes"] == 8 * GIB and n1["spare_bytes"] == int(32 * GIB * 0.9) - 0


def test_ha_guests_are_counted():
    r = host_failure_check([node(1), node(2)], [guest(1, 2, ha="started"), guest(1, 2, ha="none"), guest(1, 2)])
    assert r[0]["running_guests"] == 3 and r[0]["ha_guests"] == 1
