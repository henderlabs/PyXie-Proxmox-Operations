"""VM network device line building: the form can now manage bandwidth limit and MTU, and older callers still keep them."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.nic_workflow import build_net, net_fields, parse_net

OLD = "virtio=BC:24:11:AA:10:01,bridge=vmbr0,tag=20,firewall=1,rate=50,mtu=1400,queues=4"


def test_without_the_extras_flag_rate_mtu_and_queues_are_kept_exactly_as_before():
    line = build_net(parse_net(OLD), model="virtio", bridge="vmbr1", tag=30, firewall=True, mac="BC:24:11:AA:10:01", link_down=False)
    assert "rate=50" in line and "mtu=1400" in line and "queues=4" in line and "bridge=vmbr1" in line and "tag=30" in line


def test_with_the_extras_flag_the_form_values_replace_rate_and_mtu_and_queues_still_survive():
    line = build_net(parse_net(OLD), model="virtio", bridge="vmbr0", tag=20, firewall=True, mac="BC:24:11:AA:10:01", link_down=False,
                     manage_extras=True, rate="100", mtu="9000")
    assert "rate=100" in line and "mtu=9000" in line and "rate=50" not in line and "mtu=1400" not in line and "queues=4" in line


def test_a_blank_rate_or_mtu_removes_the_option_when_managed():
    line = build_net(parse_net(OLD), model="virtio", bridge="vmbr0", tag=20, firewall=False, mac=None, link_down=True,
                     manage_extras=True, rate=None, mtu=None)
    assert "rate" not in line and "mtu" not in line and "link_down=1" in line and "firewall" not in line


def test_the_fields_the_editor_shows_include_rate_and_mtu():
    f = net_fields(OLD)
    assert f["rate"] == "50" and f["mtu"] == "1400" and f["tag"] == 20 and f["firewall"] is True
    assert net_fields("e1000=AA:BB:CC:DD:EE:FF,bridge=vmbr0")["rate"] is None
