"""Host network interfaces: validation, diffing, payloads and the guard on links that carry PyXie or the cluster (pure)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.host_network import NetworkConfigError, assess, normalize, payload, plan_changes, staged_problems
from pyxie_core.host_network_workflow import touched_in_diff

ROWS = [
    {"iface": "nic0", "type": "eth", "active": 1},
    {"iface": "nic1", "type": "eth", "active": 1},
    {"iface": "nic2", "type": "eth", "active": 1},
    {"iface": "vmbr0", "type": "bridge", "cidr": "192.0.2.11/24", "address": "192.0.2.11", "gateway": "192.0.2.1", "bridge_ports": "nic0", "autostart": 1, "active": 1},
    {"iface": "vmbr1", "type": "bridge", "bridge_ports": "nic1", "autostart": 1, "bridge_vlan_aware": 1, "bridge_vids": "2-4094", "mtu": 1500, "active": 1},
    {"iface": "bond0", "type": "bond", "slaves": "nic2 nic3", "bond_mode": "active-backup", "autostart": 1, "active": 1},
]
NAMES = {r["iface"] for r in ROWS} | {"nic3"}
VMBR0, VMBR1, BOND0 = ROWS[3], ROWS[4], ROWS[5]


def test_values_are_validated_before_a_preview_exists():
    assert normalize("cidr", " 10.0.0.5/24 ", "bridge", NAMES) == "10.0.0.5/24"
    for bad in ("10.0.0.5", "10.0.0.0/24", "fe80::1/64", "nope"):
        with pytest.raises(NetworkConfigError):
            normalize("cidr", bad, "bridge", NAMES)
    assert normalize("mtu", "9000", "bridge", NAMES) == "9000" and normalize("mtu", "", "bridge", NAMES) is None
    with pytest.raises(NetworkConfigError):
        normalize("mtu", "100000", "bridge", NAMES)
    with pytest.raises(NetworkConfigError):
        normalize("bridge_ports", "nic0 ghost9", "bridge", NAMES)
    with pytest.raises(NetworkConfigError):
        normalize("bridge_vids", "2-5000", "bridge", NAMES)
    with pytest.raises(NetworkConfigError):
        normalize("bond_mode", "nonsense", "bond", NAMES)
    with pytest.raises(NetworkConfigError):
        normalize("slaves", "nic2", "bridge", NAMES)  # a bridge has no slaves


def test_physical_nics_and_unknown_settings_are_not_editable():
    _c, errors, _u = plan_changes(ROWS[0], {"mtu": "9000"}, ROWS)
    assert "only edits bridges, bonds" in errors["iface"]
    _c, errors, _u = plan_changes(VMBR1, {"bogus": "x"}, ROWS)
    assert "cannot be changed" in errors["bogus"]


def test_only_real_changes_are_planned():
    changes, errors, unchanged = plan_changes(VMBR1, {"mtu": "9000", "bridge_vlan_aware": "1", "comments": ""}, ROWS)
    assert not errors and [c["key"] for c in changes] == ["mtu"] and sorted(unchanged) == ["bridge_vlan_aware", "comments"]


def test_a_gateway_must_sit_inside_the_address_network():
    _c, errors, _u = plan_changes(VMBR1, {"cidr": "10.0.0.5/24", "gateway": "192.168.5.1"}, ROWS)
    assert "outside" in errors["gateway"]
    _c, errors, _u = plan_changes(VMBR1, {"gateway": "10.0.0.1"}, ROWS)
    assert "needs an address" in errors["gateway"]


def test_members_must_exist_be_unused_and_a_bond_needs_two():
    _c, errors, _u = plan_changes(VMBR1, {"bridge_ports": "nic0"}, ROWS)
    assert "already a member of vmbr0" in errors["bridge_ports"]
    _c, errors, _u = plan_changes(VMBR1, {"bridge_ports": "vmbr0"}, ROWS)
    assert "bridge" in errors["bridge_ports"]
    _c, errors, _u = plan_changes(BOND0, {"slaves": "nic2"}, ROWS)
    assert "at least two" in errors["slaves"]
    _c, errors, _u = plan_changes(BOND0, {"bond-primary": "nic9"}, ROWS)
    assert "is not an interface" in errors["bond-primary"]
    _c, errors, _u = plan_changes(VMBR1, {"bridge_vlan_aware": "0"}, ROWS)
    assert "VLAN-aware" in errors["bridge_vids"]  # the allowed list only means something on a VLAN-aware bridge


def test_the_proxmox_payload_carries_the_type_sets_and_deletes():
    changes, _e, _u = plan_changes(VMBR1, {"mtu": "9000", "bridge_vids": "", "bridge_vlan_aware": "0"}, ROWS)
    pay = payload(VMBR1, changes)
    assert pay["type"] == "bridge" and pay["set"] == {"mtu": "9000", "bridge_vlan_aware": 0} and pay["delete"] == ["bridge_vids"]


def test_changing_the_address_pyxie_connects_on_is_refused_outright():
    carries = ["host management address", "guests attached"]
    changes, _e, _u = plan_changes(VMBR0, {"cidr": "192.0.2.50/24"}, ROWS)
    risk = assess(VMBR0, changes, carries)
    assert risk["critical"] and risk["blocked"][0][0] == "SAFE-NETWORK-010"
    changes, _e, _u = plan_changes(VMBR0, {"autostart": "0"}, ROWS)
    assert assess(VMBR0, changes, carries)["blocked"][0][0] == "SAFE-NETWORK-011"


def test_other_edits_to_a_critical_link_are_allowed_with_warnings_and_a_plain_link_is_not_critical():
    changes, _e, _u = plan_changes(VMBR0, {"mtu": "9000"}, ROWS)
    risk = assess(VMBR0, changes, ["host management address", "cluster (corosync) link"])
    assert risk["critical"] and not risk["blocked"] and any("MTU" in w for w in risk["warnings"])
    changes, _e, _u = plan_changes(VMBR1, {"mtu": "9000"}, ROWS)
    risk = assess(VMBR1, changes, ["guests attached"])
    assert not risk["critical"] and not risk["blocked"] and any("Guests" in w for w in risk["warnings"])


def test_a_staged_file_must_still_hold_the_management_address_and_start_at_boot():
    assert staged_problems(ROWS, "192.0.2.11") == []
    assert staged_problems(ROWS, None) == []
    assert staged_problems(ROWS, "10.9.9.9")[0][0] == "SAFE-NETWORK-012"
    moved = [dict(r, autostart=0) if r["iface"] == "vmbr0" else r for r in ROWS]
    assert "start at boot" in staged_problems(moved, "192.0.2.11")[0][1]


def test_the_staged_diff_flags_every_interface_it_mentions():
    diff = "--- /etc/network/interfaces\n+++ /etc/network/interfaces.new\n@@ -10,7 +10,7 @@\n iface vmbr1 inet manual\n-\tmtu 1500\n+\tmtu 9000\n"
    assert touched_in_diff(diff, ["nic1", "vmbr0", "vmbr1"]) == ["vmbr1"]
