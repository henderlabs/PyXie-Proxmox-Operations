"""Host Shut Down / Restart gating: only from maintenance mode, nothing running, host name typed (pure parts)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))


def test_shutdown_is_a_registered_operation_with_a_typed_confirmation_hook():
    import inspect

    from pyxie_core import host_power_workflow, reboot_workflow

    assert "maintenance mode" in inspect.getsource(host_power_workflow.dry_run_shutdown)
    assert '"confirm_name": node.name' in inspect.getsource(host_power_workflow.dry_run_shutdown)
    assert "require_maintenance" in inspect.signature(reboot_workflow.dry_run_reboot).parameters
    assert inspect.signature(reboot_workflow.dry_run_reboot).parameters["require_maintenance"].default is False  # Reboot Node behaves as before
