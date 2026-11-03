"""The plain-language state of a host's maintenance wrapper (pure)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.wrapper_state import missing_wrapper_hint, wrapper_state

BASE = {"credential_configured": True, "management_ip_known": True, "host_key_pinned": True, "provisioned": True, "reachable": True,
        "wrapper_version": "1.2.0", "kit_wrapper_version": "1.2.0", "wrapper_outdated": False}


def test_each_state_is_named_with_a_next_step():
    assert wrapper_state({**BASE, "credential_configured": False})["state"] == "no_credential"
    assert wrapper_state({**BASE, "host_key_pinned": False, "provisioned": False, "reachable": False})["state"] == "not_set_up"
    assert wrapper_state({**BASE, "management_ip_known": False})["state"] == "not_set_up"
    down = wrapper_state({**BASE, "reachable": False, "error": "timed out"})
    assert down["state"] == "not_answering" and "timed out" in down["next"]
    old = wrapper_state({**BASE, "wrapper_version": "1.1.0", "wrapper_outdated": True})
    assert old["state"] == "outdated" and "1.1.0" in old["headline"] and "1.2.0" in old["headline"]
    assert wrapper_state(BASE) == {"state": "ready", "headline": "Wrapper 1.2.0, host fingerprint trusted.", "next": ""}


def test_blocked_operations_point_at_the_host_page():
    assert "st-pve106" in missing_wrapper_hint("st-pve106") and "Add the wrapper" in missing_wrapper_hint("st-pve106")
