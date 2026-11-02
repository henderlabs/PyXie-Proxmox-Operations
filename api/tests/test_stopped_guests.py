"""Stopped guests on shared storage get a real opt-in relocation row (config-only move), flagged when they start on boot."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

import pyxie_core.stopped_guests as sg


class Client:
    def __init__(self, cfg):
        self.cfg = cfg

    def qemu_config(self, node, vmid):
        return self.cfg


def cand(node_id, name, blocked=False):
    return SimpleNamespace(node_id=node_id, node_name=name, score=60.0, projected_pct=40.0, blocked=blocked, blocking_reasons=[], reasons=[])


def run(monkeypatch, cfg):
    ranked = [cand("n2", "st-pve105"), cand("n3", "st-pve106", blocked=True)]
    monkeypatch.setattr(sg, "recommend_destinations", lambda *a, **k: ranked)
    monkeypatch.setattr(sg, "rank_with_simulated_load", lambda c, *a, **k: c)
    wl = SimpleNamespace(id="w1", vmid=101, type="vm", name="st-jellyfin")
    node = SimpleNamespace(name="st-pve101")
    nodes_by_id = {"n2": SimpleNamespace(name="st-pve105"), "n3": SimpleNamespace(name="st-pve106")}
    return sg.stopped_shared_item(None, Client(cfg), node, wl, [], nodes_by_id, {}, "stopped, shared")


def test_the_row_is_opt_in_offline_and_copies_nothing(monkeypatch):
    item = run(monkeypatch, {})
    assert item["included"] is False and item["transport"] == "offline"
    assert item["destination_node"] == "st-pve105" and item["destination_storage_id"] is None
    assert item["currently_on_shared"] is True and len(item["candidates"]) == 2
    assert "starts_on_boot" not in item


def test_a_guest_that_starts_on_boot_is_flagged(monkeypatch):
    item = run(monkeypatch, {"onboot": 1})
    assert item["starts_on_boot"] is True
