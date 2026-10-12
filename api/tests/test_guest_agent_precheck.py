"""Resize pre-check: a guest with the agent switched on but not answering cannot be shut down gracefully."""

import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core import resize_workflow as rw


def test_agent_enabled_reads_both_config_forms():
    assert rw.agent_enabled({"agent": "1"})
    assert rw.agent_enabled({"agent": "enabled=1,fstrim_cloned_disks=1"})
    assert not rw.agent_enabled({"agent": "0"})
    assert not rw.agent_enabled({"agent": "enabled=0"})
    assert not rw.agent_enabled({})


def test_agent_not_running_matches_proxmox_messages():
    assert rw.agent_not_running(Exception("500 QEMU guest agent is not running"))
    assert rw.agent_not_running(Exception("VM 11111 qga command 'guest-ping' failed - got timeout"))
    assert not rw.agent_not_running(Exception("403 Permission check failed"))


class FakeClient:
    def __init__(self, config, agent_error=None):
        self.config, self.agent_error = config, agent_error

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def qemu_config(self, node, vmid):
        return self.config

    def qemu_agent_interfaces(self, node, vmid):
        if self.agent_error:
            raise self.agent_error
        return []


def run(monkeypatch, client):
    monkeypatch.setattr(rw, "build_pve_client", lambda db, target: (client, None))
    return rw.check_guest_agent(None, None, NS(name="n"), NS(vmid=1))


def test_states(monkeypatch):
    assert run(monkeypatch, FakeClient({"agent": "0"}))["state"] == "disabled"
    assert run(monkeypatch, FakeClient({"agent": "1"}))["state"] == "ok"
    assert run(monkeypatch, FakeClient({"agent": "1"}, Exception("500 QEMU guest agent is not running")))["state"] == "not_responding"
    assert run(monkeypatch, FakeClient({"agent": "1"}, Exception("403 forbidden")))["state"] == "unknown"  # a permission problem must not block a resize
    monkeypatch.setattr(rw, "build_pve_client", lambda db, target: (_ for _ in ()).throw(RuntimeError("no credential")))
    assert rw.check_guest_agent(None, None, NS(name="n"), NS(vmid=1))["state"] == "unknown"
