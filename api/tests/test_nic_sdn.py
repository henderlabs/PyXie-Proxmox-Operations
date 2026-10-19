from pyxie_core.nic_workflow import check_bridge

TOPO = {n: {"brdata": {"iface": "brdata", "type": "bridge", "vlan_aware": True, "allowed_vlans": None},
            "dev1356": {"iface": "dev1356", "type": "sdn", "vlan_aware": True, "allowed_vlans": None, "alias": "Dev API Test", "tag": 1356}} for n in ("n1", "n2")}


def test_a_tag_on_an_sdn_network_is_refused_because_the_network_sets_the_vlan():
    blocks, _warns = check_bridge("dev1356", 1356, "n1", TOPO, ["n1", "n2"])
    assert blocks and "sets the VLAN itself (1356)" in blocks[0] and "leave the VLAN tag empty" in blocks[0]


def test_an_sdn_network_without_a_tag_is_fine():
    blocks, _warns = check_bridge("dev1356", None, "n1", TOPO, ["n1", "n2"])
    assert blocks == []


def test_a_plain_vlan_aware_bridge_still_takes_a_tag():
    blocks, _warns = check_bridge("brdata", 10, "n1", TOPO, ["n1", "n2"])
    assert blocks == []
