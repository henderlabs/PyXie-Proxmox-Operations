"""Host shell: pure rules (state, idle limit, framing, one-time ticket), the settings bounds, and the audit/credential wiring."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

import pytest
from pydantic import ValidationError

from app import schemas
from app.routers import providers, shell
from test_console_tickets import FakeRedis


def _state(**kw):
    base = dict(is_admin=True, enabled=True, has_credential=True, node_missing=False, node_offline=False,
                in_maintenance=False, require_maintenance=False)
    base.update(kw)
    return shell.shell_state(**base)


def test_state_ready_and_each_refusal_in_order():
    assert _state() == "ready"
    assert _state(is_admin=False, enabled=False) == "admin_only"
    assert _state(enabled=False) == "disabled"
    assert _state(node_missing=True) == "unavailable"
    assert _state(node_offline=True) == "unavailable"
    assert _state(has_credential=False) == "no_credential"
    assert _state(require_maintenance=True) == "needs_maintenance"
    assert _state(require_maintenance=True, in_maintenance=True) == "ready"


def test_idle_limit_is_clamped_and_never_zero():
    assert shell.idle_seconds(15) == 900
    assert shell.idle_seconds(1) == 300
    assert shell.idle_seconds(9999) == 3600
    assert shell.idle_seconds(None) == 900
    assert shell.idle_seconds("x") == 900


def test_only_typing_counts_as_activity():
    assert shell.frame_is_input("0:5:ls -l")
    assert not shell.frame_is_input("1:120:40:")  # resize
    assert not shell.frame_is_input("2")  # ping
    assert not shell.frame_is_input(b"0:1:a")  # bytes from the browser are not typing frames


def test_shell_ticket_is_one_time_and_separate_from_the_console_store():
    r = FakeRedis()
    shell.store_ticket(r, "t1", {"node": "st-pve101"})
    assert shell.consume_ticket(r, "t1") == {"node": "st-pve101"}
    assert shell.consume_ticket(r, "t1") is None
    shell.store_ticket(r, "t2", {"node": "n"})
    from pyxie_core import console_tickets as ct
    assert ct.consume_ticket(r, "t2") is None  # a shell ticket can never open a guest console
    assert shell.consume_ticket(r, "") is None
    assert shell.consume_ticket(r, "x" * 129) is None


def test_settings_update_bounds_the_idle_minutes():
    assert schemas.AppSettingsUpdate(shell_idle_minutes=5).shell_idle_minutes == 5
    for bad in (4, 61, 0):
        with pytest.raises(ValidationError):
            schemas.AppSettingsUpdate(shell_idle_minutes=bad)
    assert schemas.AppSettingsUpdate().model_dump(exclude_unset=True) == {}


def test_shell_defaults_keep_todays_behaviour():
    from pyxie_core.models import AppSettings
    cols = AppSettings.__table__.c
    assert cols.shell_enabled.default.arg is False
    assert cols.shell_require_maintenance.default.arg is False
    assert cols.shell_idle_minutes.default.arg == 15


def test_shell_is_an_allowed_credential_slot():
    assert "shell" in providers.CREDENTIAL_SLOT_NAMES


def test_every_shell_route_that_acts_needs_an_admin():
    paths = {r.path: r for r in shell.router.routes}
    post = paths["/api/nodes/{node_id}/shell"]
    deps = [d.call.__name__ for d in post.dependant.dependencies]
    assert "require_admin" in deps
    assert "/shell-ws/{ticket}" in paths


def test_permission_comes_after_admin_and_before_the_switch():
    assert _state(has_permission=False) == "no_permission"
    assert _state(is_admin=False, has_permission=False) == "admin_only"
    assert _state(has_permission=False, enabled=False) == "no_permission"
    assert _state() == "ready"


def test_user_shell_permission_is_off_by_default_and_updatable_by_admins_only():
    from pyxie_core.models import User
    from app.routers import auth
    assert User.__table__.c.can_use_shell.default.arg is False
    assert auth.UserUpdate(can_use_shell=True).model_dump(exclude_unset=True) == {"can_use_shell": True}
    assert "can_use_shell" not in auth.UserUpdate().model_dump(exclude_unset=True)
    deps = [d.call.__name__ for r in auth.router.routes if r.path == "/api/auth/users/{user_id}" and "PATCH" in r.methods
            for d in r.dependant.dependencies]
    assert "require_admin" in deps


class _U:
    def __init__(self, **kw):
        self.__dict__.update(dict(email="a@example.test", display_name=None, is_admin=True, can_use_shell=True,
                                  preferences=None), **kw)


def test_me_reports_the_permission_only_for_admins():
    from app.routers import auth
    assert auth._me_out(_U())["can_use_shell"] is True
    assert auth._me_out(_U(is_admin=False))["can_use_shell"] is False
    assert auth._me_out(_U(can_use_shell=False))["can_use_shell"] is False
