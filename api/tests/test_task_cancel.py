"""When a running migration can be cancelled safely (pure)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.task_cancel import migration_cancel_state


def test_copy_phases_are_safe_and_named():
    assert migration_cancel_state({"phase": "storage", "raw_line": "drive-scsi0: transferred 4 GiB"}) == (True, "copying disks")
    assert migration_cancel_state({"phase": "vm", "raw_line": "migration active, transferred 1 GiB"}) == (True, "copying memory")
    assert migration_cancel_state(None) == (True, "starting")


def test_the_final_switchover_is_held():
    for line in ("migration status: completed", "stopping NBD storage migration server on target", "migration finished successfully (duration 00:00:12)"):
        safe, phase = migration_cancel_state({"phase": "vm", "raw_line": line})
        assert not safe and "final switchover" in phase


def test_a_cancelled_operation_carries_the_note():
    import inspect

    from pyxie_core.operations_engine import cancel_operation

    assert "note" in inspect.signature(cancel_operation).parameters


def test_storage_move_cancel_is_held_only_for_the_final_switch():
    from pyxie_core.task_cancel import storage_move_cancel_state
    assert storage_move_cancel_state(None) == (True, "starting")
    assert storage_move_cancel_state({"raw_line": "drive-scsi0: transferred 4.1 GiB of 32 GiB (12.81%) in 40s"})[0] is True
    assert storage_move_cancel_state({"raw_line": "drive-scsi0: Completing block job_id..."})[0] is False
    assert storage_move_cancel_state({"raw_line": "drive mirror is ready"})[0] is False
