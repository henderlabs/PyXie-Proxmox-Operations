from pyxie_core.vlan_view import build_vlan_view, parse_vids, tag_allowed


def test_parse_and_allowed():
    assert parse_vids(None) is None
    assert tag_allowed(None, 50)
    assert tag_allowed("10 20 30-40", 35) and not tag_allowed("10 20 30-40", 25)
    assert tag_allowed("2-4094", 4094) and not tag_allowed("2-4094", 1)


def _topo():
    return [
        {"cluster_id": "c", "node": "a", "interfaces": [{"iface": "vmbr0", "vlan_aware": True, "allowed_vlans": "2-4094"}]},
        {"cluster_id": "c", "node": "b", "interfaces": [{"iface": "vmbr0", "vlan_aware": False}]},
        {"cluster_id": "c", "node": "d", "interfaces": []},
    ]


def _wl(vmid, node, tag):
    return {"cluster_id": "c", "cluster_name": "C", "workload_id": str(vmid), "vmid": vmid, "name": f"g{vmid}",
            "type": "vm", "node": node, "status": "running",
            "nics": [{"net_id": "net0", "bridge": "vmbr0", "vlan_tag": tag}]}


def test_segments_blocked_hosts_and_problems():
    segs = build_vlan_view(_topo(), [_wl(1, "a", 20), _wl(2, "a", 20), _wl(3, "b", 20), _wl(4, "a", None)])
    by = {s["tag"]: s for s in segs}
    assert len(by[20]["guests"]) == 3
    assert {h["node"]: h["reason"] for h in by[20]["blocked_hosts"]} == {
        "b": "bridge is not VLAN-aware", "d": "bridge missing"}
    assert by[20]["problems"] == ["g3 on b: bridge is not VLAN-aware"]
    assert [h["node"] for h in by[None]["blocked_hosts"]] == ["d"]
    assert segs[0]["tag"] is None
