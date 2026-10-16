from pyxie_core.ct_edit_workflow import build_ct_net, ct_net_fields, next_free_net, parse_ct_net, valid_ip


def test_parse_and_fields():
    n = parse_ct_net("name=eth0,bridge=vmbr0,hwaddr=BC:24:11:AA:BB:CC,ip=192.168.1.50/24,gw=192.168.1.1,tag=20,firewall=1,type=veth,rate=10")
    assert n["name"] == "eth0" and n["type"] == "veth" and list(n)[-1] == "rate"
    f = ct_net_fields("name=eth1,bridge=vmbr1,ip=dhcp")
    assert f == {"name": "eth1", "bridge": "vmbr1", "tag": None, "firewall": False, "ip": "dhcp", "gw": None, "mac": None}


def test_build_keeps_unmanaged_settings():
    old = parse_ct_net("name=eth0,bridge=vmbr0,hwaddr=BC:24:11:AA:BB:CC,ip=dhcp,type=veth,rate=10,mtu=1400")
    line = build_ct_net(old, name="eth0", bridge="vmbr1", tag=30, firewall=True, ip="10.0.0.5/24", gw="10.0.0.1", mac="BC:24:11:AA:BB:CC")
    assert line == "name=eth0,bridge=vmbr1,hwaddr=BC:24:11:AA:BB:CC,ip=10.0.0.5/24,gw=10.0.0.1,tag=30,firewall=1,type=veth,rate=10,mtu=1400"
    assert build_ct_net(None, name="eth2", bridge="vmbr0", tag=None, firewall=False, ip="dhcp", gw=None, mac=None) == "name=eth2,bridge=vmbr0,ip=dhcp"


def test_slots_and_ip():
    assert next_free_net({}) == ("net0", "eth0") and next_free_net({"net0": "x", "net2": "y"}) == ("net1", "eth1") and next_free_net({f"net{i}": "x" for i in range(32)}) is None
    assert valid_ip("dhcp") is None and valid_ip("manual") is None and valid_ip(None) is None and valid_ip("192.168.1.5/24") is None
    assert valid_ip("192.168.1.5") and valid_ip("fe80::1/64") and valid_ip("nonsense")
