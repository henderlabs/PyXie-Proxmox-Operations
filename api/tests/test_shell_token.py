"""Dedicated shell token: what counts as valid, how a failure reads, and that the maintenance token is never used."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

import pytest

from pyxie_core import shell_token
from pyxie_core.credentials import CredentialNotConfigured
from app.routers import shell


def test_missing_console_lists_only_hosts_without_the_right():
    perms = {"a": {"Sys.Console": 1, "Sys.Audit": 1}, "b": {"Sys.Audit": 1}, "c": {}, "d": None}
    assert shell_token.missing_console(perms) == ["b", "c", "d"]
    assert shell_token.missing_console({"a": {"Sys.Console": 1}}) == []
    assert shell_token.missing_console({}) == []


def test_failure_text_names_hosts_and_the_two_places_the_right_is_needed():
    msg = shell_token.explain(["st-pve105"], 5)
    assert "st-pve105" in msg and "1 of 5" in msg and "token and its user" in msg
    many = shell_token.explain([f"h{i}" for i in range(9)], 9)
    assert "and 3 more" in many
    assert "no hosts" in shell_token.explain([], 0)


def test_shell_only_ever_asks_for_the_shell_slot(monkeypatch):
    asked = []

    def fake_load(db, target, slot):
        asked.append(slot)
        raise CredentialNotConfigured(slot)

    monkeypatch.setattr(shell, "load_pve_credentials", fake_load)
    with pytest.raises(CredentialNotConfigured):
        shell._shell_creds(None, None)
    assert asked == ["shell"]  # never falls back to the maintenance token
    assert shell._has_credential(None, None) is None


def test_status_route_is_admin_only():
    r = next(r for r in shell.router.routes if r.path == "/api/shell/status")
    assert "require_admin" in [d.call.__name__ for d in r.dependant.dependencies]
