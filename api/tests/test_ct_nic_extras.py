"""Container network device line building with the bandwidth limit and MTU (pure)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.ct_edit_workflow import build_ct_net, ct_net_fields, parse_ct_net

OLD = parse_ct_net("name=eth0,bridge=vmbr0,hwaddr=BC:24:11:AA:10:01,ip=dhcp,tag=20,firewall=1,rate=50,mtu=1400,type=veth")


def test_without_the_flag_rate_and_mtu_are_kept_as_before():
    line = build_ct_net(OLD, name="eth0", bridge="vmbr1", tag=30, firewall=True, ip="dhcp", gw=None, mac="BC:24:11:AA:10:01")
    assert "rate=50" in line and "mtu=1400" in line and "bridge=vmbr1" in line and "type=veth" in line


def test_with_the_flag_the_form_values_replace_them_and_blank_removes():
    line = build_ct_net(OLD, name="eth0", bridge="vmbr0", tag=20, firewall=True, ip="dhcp", gw=None, mac=None, manage_extras=True, rate="100", mtu="9000")
    assert "rate=100" in line and "mtu=9000" in line and "rate=50" not in line and "type=veth" in line
    line = build_ct_net(OLD, name="eth0", bridge="vmbr0", tag=20, firewall=False, ip="dhcp", gw=None, mac=None, manage_extras=True)
    assert "rate" not in line and "mtu" not in line


def test_the_editor_fields_include_rate_and_mtu():
    f = ct_net_fields("name=eth0,bridge=vmbr0,rate=50,mtu=1400")
    assert f["rate"] == "50" and f["mtu"] == "1400"
    assert ct_net_fields("name=eth0,bridge=vmbr0")["rate"] is None
