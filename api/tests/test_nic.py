import pytest

from pyxie_core.nic_workflow import build_net, check_bridge, net_fields, next_free_slot, parse_net, valid_mac

TOPO = {
    "n1": {"vmbr0": {"vlan_aware": True, "allowed_vlans": None}, "vmbr1": {"vlan_aware": False}},
    "n2": {"vmbr0": {"vlan_aware": True, "allowed_vlans": "10-30"}},
    "n3": {"vmbr0": {"vlan_aware": False}},
}


def test_parse_and_fields():
    n = parse_net("virtio=BC:24:11:4A:20:1C,bridge=vmbr0,tag=20,firewall=1,rate=50")
    assert n["model"] == "virtio" and n["mac"] == "BC:24:11:4A:20:1C" and list(n["params"]) == ["bridge", "tag", "firewall", "rate"]
    f = net_fields("e1000=AA:BB:CC:DD:EE:FF,bridge=vmbr1,link_down=1")
    assert f == {"model": "e1000", "mac": "AA:BB:CC:DD:EE:FF", "bridge": "vmbr1", "tag": None, "firewall": False, "link_down": True}
    assert net_fields("virtio,bridge=vmbr0")["mac"] is None


def test_build_keeps_unmanaged_settings_and_order():
    old = parse_net("virtio=BC:24:11:4A:20:1C,bridge=vmbr0,firewall=1,rate=50,mtu=1400")
    line = build_net(old, model="virtio", bridge="vmbr1", tag=30, firewall=False, mac="BC:24:11:4A:20:1C", link_down=False)
    assert line == "virtio=BC:24:11:4A:20:1C,bridge=vmbr1,tag=30,rate=50,mtu=1400"
    assert build_net(None, model="e1000", bridge="vmbr0", tag=None, firewall=True, mac=None, link_down=True) == "e1000,bridge=vmbr0,firewall=1,link_down=1"


def test_next_free_slot():
    assert next_free_slot({}) == "net0"
    assert next_free_slot({"net0": "x", "net2": "y"}) == "net1"
    assert next_free_slot({f"net{i}": "x" for i in range(32)}) is None


def test_mac_validation():
    assert valid_mac("BC:24:11:4A:20:1C") is None
    assert valid_mac("bc:24:11:4a:20:1c") is None
    assert valid_mac("nonsense") is not None
    assert "multicast" in valid_mac("01:00:5E:00:00:01")


def test_bridge_checks_per_host():
    names = ["n1", "n2", "n3"]
    blocks, warns = check_bridge("vmbr0", 20, "n1", TOPO, names)
    assert blocks == [] and len(warns) == 1 and "n3" in warns[0] and "not VLAN-aware" in warns[0]
    blocks, warns = check_bridge("vmbr0", 200, "n1", TOPO, names)  # n2 only allows 10-30
    assert blocks == [] and any("n2" in w and "allowed list" in w for w in warns)
    blocks, warns = check_bridge("vmbr1", None, "n1", TOPO, names)  # only n1 has vmbr1
    assert blocks == [] and any("n2, n3" in w and "no bridge" in w for w in warns)
    blocks, _ = check_bridge("vmbr1", None, "n2", TOPO, names)  # the VM's own host lacks it
    assert blocks and "n2 (where the VM lives)" in blocks[0]
    blocks, _ = check_bridge("vmbr1", 5, "n1", TOPO, names)  # own host's bridge is not VLAN-aware
    assert blocks and "not VLAN-aware" in blocks[0]
    _, warns = check_bridge("vmbr0", None, "n1", {"n1": TOPO["n1"]}, names)  # unreadable hosts are unverified
    assert any("unverified" in w for w in warns)


def test_sdn_vnets_are_attachable_and_use_their_zone_path():
    from pyxie_core.nic_workflow import check_bridge, sdn_path
    topo = {"a": {"vmbr0": {"vlan_aware": False}, "vnet1": {"vlan_aware": True, "allowed_vlans": None, "sdn_zone": "zone1"}}, "b": {"vmbr0": {"vlan_aware": False}, "vnet1": {"vlan_aware": True, "allowed_vlans": None, "sdn_zone": "zone1"}}}
    assert check_bridge("vnet1", 20, "a", topo, ["a", "b"]) == ([], [])
    assert sdn_path(topo, "a", "vnet1") == "/sdn/zones/zone1/vnet1"
    assert sdn_path(topo, "a", "vmbr0") == "/sdn/zones/localnetwork/vmbr0"
