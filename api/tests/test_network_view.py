from pyxie_core.network_view import layout_groups, proofs_for_node
from pyxie_core.network_workflow import _parse_migration
from pyxie_core.vlan_view import build_vlan_view


def eth(n, **kw):
    return {"iface": n, "type": "eth", "active": True, **kw}


def host(name, ip, *, drop=None):
    last = ip.split(".")[-1]
    ifaces = [eth("enmgmt0"), eth("enmgmt1"), {"iface": "bond0", "type": "bond", "ports": "enmgmt0 enmgmt1", "active": True},
              {"iface": "vmbr0", "type": "bridge", "ports": "bond0", "active": True, "address": ip},
              eth("enmigr0"), eth("enmigr1"), {"iface": "bond1", "type": "bond", "ports": "enmigr0 enmigr1", "active": True, "address": f"10.9.{last}.1"},
              eth("endata0"), eth("endata1"), {"iface": "bond4", "type": "bond", "ports": "endata0 endata1", "active": True},
              {"iface": "brdata", "type": "bridge", "ports": "bond4", "active": True, "vlan_aware": True},
              eth("enstg0"), eth("enstg1"), {"iface": "bond2", "type": "bond", "ports": "enstg0 enstg1", "active": True},
              eth("encsync0", address=f"10.8.{last}.1")]
    if drop:
        ifaces = [i for i in ifaces if i["iface"] != drop]
    return {"node": name, "node_id": name, "cluster_id": "c1", "cluster_name": "SLC", "interfaces": ifaces}


FACTS = {"migration_cidr": "10.9.0.0/16", "corosync": {f"n{n}": [f"10.8.{n}.1"] for n in range(1, 4)}, "mgmt": {f"n{n}": f"10.1.6.{n}" for n in range(1, 4)}}
SDN = {"readable": True, "zones": [{"zone": "crezone", "type": "vlan", "bridge": "brdata", "nodes": []}], "vnets": [{"vnet": "pita1353", "alias": "PAW IT Admin", "zone": "crezone", "tag": 1353}, {"vnet": "voip5", "alias": "VOIP", "zone": "crezone", "tag": 5}]}


def test_carries_only_what_can_be_proven_and_links_inherit_from_their_bridge_or_bond():
    h = host("n1", "10.1.6.1")
    p = proofs_for_node(h["interfaces"], mgmt_ip="10.1.6.1", migration_cidr="10.9.0.0/16", corosync_addrs=["10.8.1.1"], zone_bridges={"brdata": "crezone"}, guests_per_bridge={})
    assert p["vmbr0"] == ["host management address"]
    assert p["bond0"] == p["enmgmt0"] == ["host management address"]
    assert p["bond1"] == ["cluster migration network 10.9.0.0/16"] and p["enmigr1"] == p["bond1"]
    assert p["brdata"] == ["guest networks (SDN zone crezone)"] and p["endata0"] == p["brdata"]
    assert p["encsync0"] == ["cluster (corosync) link"]
    assert p["bond2"] == [] and p["enstg0"] == []   # storage is not proven by anything: left blank


def test_a_plain_bridge_with_guests_says_so():
    p = proofs_for_node([{"iface": "vmbr9", "type": "bridge"}], mgmt_ip=None, migration_cidr=None, corosync_addrs=[], zone_bridges={}, guests_per_bridge={"vmbr9": 3})
    assert p["vmbr9"] == ["guests attached"]


def test_identical_hosts_form_one_group_and_the_odd_host_is_separate():
    topo = [host("n1", "10.1.6.1"), host("n2", "10.1.6.2"), host("n3", "10.1.6.3", drop="enmgmt1")]
    groups = layout_groups(topo, FACTS, [], SDN)
    assert [g["nodes"] for g in groups] == [["n1", "n2"], ["n3"]]
    assert groups[0]["is_main"] and not groups[1]["is_main"]
    assert any(i["iface"] == "enmgmt1" for i in groups[0]["interfaces"]) and not any(i["iface"] == "enmgmt1" for i in groups[1]["interfaces"])


def test_a_host_that_could_not_be_read_is_reported_alone():
    groups = layout_groups([host("n1", "10.1.6.1"), {"node": "n2", "cluster_id": "c1", "error": "timeout", "interfaces": []}], FACTS, [], SDN)
    assert groups[-1]["nodes"] == ["n2"] and groups[-1]["error"] == "timeout"


def test_migration_network_parsing():
    assert _parse_migration({"migration": "type=secure,network=10.9.0.0/16"}) == "10.9.0.0/16"
    assert _parse_migration({"migration": {"network": "10.9.0.0/16", "type": "secure"}}) == "10.9.0.0/16"
    assert _parse_migration({}) is None


def vlan_inputs():
    topo = [{"node": n, "node_id": n, "cluster_id": "c1", "cluster_name": "SLC", "interfaces": [{"iface": "brdata", "type": "bridge", "vlan_aware": True, "allowed_vlans": None}]} for n in ("n1", "n2")]
    nics = [{"workload_id": "w1", "vmid": 1, "name": "PAW-1", "type": "vm", "node": "n1", "status": "running", "cluster_id": "c1", "cluster_name": "SLC", "nics": [{"net_id": "net0", "bridge": "pita1353", "vlan_tag": None}]}]
    return topo, nics


def test_sdn_network_is_not_reported_as_a_missing_bridge_and_shows_its_vlan_and_unused_networks():
    topo, nics = vlan_inputs()
    segs = build_vlan_view(topo, nics, {"c1": SDN})
    by = {s["bridge"]: s for s in segs}
    assert by["pita1353"]["sdn"] == {"alias": "PAW IT Admin", "zone": "crezone", "tag": 1353}
    assert by["pita1353"]["blocked_hosts"] == [] and by["pita1353"]["problems"] == []
    assert by["voip5"]["unused"] is True and by["voip5"]["guests"] == []
    assert [s["bridge"] for s in segs] == ["voip5", "pita1353"]   # sorted by the network's VLAN: 5 then 1353


def test_unreadable_sdn_says_so_instead_of_bridge_missing_everywhere():
    topo, nics = vlan_inputs()
    segs = build_vlan_view(topo, nics, {"c1": {"readable": True, "zones": [], "vnets": []}})
    seg = segs[0]
    assert seg["bridge"] == "pita1353" and seg["blocked_hosts"] == [] and seg["problems"] == []
    assert "SDN.Audit" in seg["note"]


def test_plain_bridges_still_work_without_any_sdn():
    topo, _ = vlan_inputs()
    nics = [{"workload_id": "w1", "vmid": 1, "name": "A", "type": "vm", "node": "n1", "status": "running", "cluster_id": "c1", "cluster_name": "SLC", "nics": [{"net_id": "net0", "bridge": "brdata", "vlan_tag": 10}]}]
    seg = build_vlan_view(topo, nics)[0]
    assert seg["tag"] == 10 and seg["sdn"] is None and seg["blocked_hosts"] == []


def test_a_host_with_no_guests_still_groups_with_hosts_that_have_some():
    topo = [host("n1", "10.1.6.1"), host("n2", "10.1.6.2")]
    nics = [{"node": "n1", "nics": [{"bridge": "vmbr0"}]}]
    groups = layout_groups(topo, FACTS, nics, SDN)
    assert len(groups) == 1 and groups[0]["nodes"] == ["n1", "n2"]
