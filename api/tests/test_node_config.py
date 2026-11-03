"""Host Config tab: validation, diffing and the Proxmox calls a set of changes turns into (pure)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.node_config import NodeConfigError, hosts_problem, normalize, plan_changes, section_payloads

HOSTS = "127.0.0.1 localhost\n192.0.2.11 st-pve101.example.test st-pve101\n"
CURRENT = {"description": "Rack 2", "wakeonlan": None, "dns1": "192.0.2.1", "dns2": "1.1.1.1", "search": "example.test", "timezone": "America/Denver", "hosts": HOSTS}


def test_values_are_validated_before_a_preview_exists():
    assert normalize("wakeonlan", "00:1B:21:AA:BB:CC") == "00:1b:21:aa:bb:cc"
    with pytest.raises(NodeConfigError):
        normalize("wakeonlan", "not-a-mac")
    assert normalize("dns1", " 192.168.1.1 ") == "192.168.1.1" and normalize("dns2", "2606:4700:4700::1111") == "2606:4700:4700::1111"
    with pytest.raises(NodeConfigError):
        normalize("dns1", "dns.example.com")
    with pytest.raises(NodeConfigError):
        normalize("startall-onboot-delay", "999")
    assert normalize("ballooning-target", "80") == "80"
    assert normalize("description", "") is None
    with pytest.raises(NodeConfigError):
        normalize("search", "")  # Proxmox requires a search domain


def test_only_real_changes_are_planned_and_unknown_options_refused():
    changes, errors, unchanged = plan_changes(CURRENT, {"description": "Rack 3", "timezone": "America/Denver", "dns1": "9.9.9.9", "bogus": "x"}, "st-pve101")
    assert {c["key"] for c in changes} == {"description", "dns1"} and unchanged == ["timezone"] and "cannot be changed" in errors["bogus"]


def test_the_hosts_file_must_still_resolve_the_hosts_own_name():
    assert hosts_problem(HOSTS, "st-pve101") is None
    assert "own name" in hosts_problem("127.0.0.1 localhost\n", "st-pve101")
    assert "localhost" in hosts_problem("192.0.2.11 st-pve101\n", "st-pve101")
    changes, errors, _u = plan_changes(CURRENT, {"hosts": "127.0.0.1 localhost\n"}, "st-pve101")
    assert not changes and "own name" in errors["hosts"]
    changes, errors, _u = plan_changes(CURRENT, {"hosts": HOSTS + "10.0.0.5 nas\r\n"}, "st-pve101")
    assert not errors and changes[0]["new"].endswith("10.0.0.5 nas\n")


def test_dns_writes_always_carry_the_full_server_list_and_the_search_domain():
    changes, _e, _u = plan_changes(CURRENT, {"dns1": "9.9.9.9"}, "st-pve101")
    pay = section_payloads(CURRENT, changes)
    assert pay["dns"]["set"] == {"dns1": "9.9.9.9", "dns2": "1.1.1.1", "search": "example.test"} and pay["dns"]["delete"] == []
    changes, _e, _u = plan_changes(CURRENT, {"dns2": ""}, "st-pve101")
    assert "dns2" not in section_payloads(CURRENT, changes)["dns"]["set"]


def test_removed_node_options_become_deletes_and_each_option_lands_in_its_own_section():
    changes, _e, _u = plan_changes(CURRENT, {"description": "", "timezone": "UTC", "wakeonlan": "00:1B:21:AA:BB:CC"}, "st-pve101")
    pay = section_payloads(CURRENT, changes)
    assert pay["config"] == {"set": {"wakeonlan": "00:1b:21:aa:bb:cc"}, "delete": ["description"]} and pay["time"]["set"] == {"timezone": "UTC"}
