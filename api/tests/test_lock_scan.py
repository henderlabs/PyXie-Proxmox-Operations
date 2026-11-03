"""The wrapper's lock scan (1.2.1) and the plain-language explanation PyXie shows for it."""
import builtins
import os
import re
import time
from pathlib import Path

from pyxie_core.clear_lock_workflow import STUCK_BACKUP_SECONDS, explain_row, stop_candidates, stuck_backup

KIT = Path(__file__).resolve().parents[1] / "host_maintenance_kit" / "pyxie-maint"


def _scan_namespace(active_text: str) -> dict:
    code = re.search(r"LOCK_SCAN_PY='(.*?)\n'\n", KIT.read_text(), re.S).group(1)
    ns: dict = {}
    real_open = builtins.open

    def fake_open(path, *a, **k):
        if path == "/var/log/pve/tasks/active":
            import io
            return io.StringIO(active_text)
        return real_open(path, *a, **k)

    ns["open"] = fake_open
    exec(compile(code, "lock-scan", "exec"), ns)
    return ns


def _upid(pid: int, pstart: int, start: int, kind="vzdump", vmid=11152) -> str:
    return f"UPID:st-pve105:{pid:08X}:{pstart:08X}:{start:08X}:{kind}:{vmid}:root@pam:"


def _my_pstart() -> int:
    return int(open(f"/proc/{os.getpid()}/stat").read().rsplit(")", 1)[1].split()[19])


def test_a_finished_task_that_is_still_listed_is_not_running():
    now = int(time.time())
    ns = _scan_namespace(_upid(4242, 1, now - 40000) + f" {now - 39000:X} job errors\n")
    t = ns["active_tasks"]()[11152][0]
    assert t["state"] == "finished" and t["status"] == "job errors"


def test_a_task_whose_process_is_gone_is_dead():
    ns = _scan_namespace(_upid(999999, 123, int(time.time()) - 100) + "\n")
    assert ns["active_tasks"]()[11152][0]["state"] == "dead"


def test_a_task_with_a_live_process_and_matching_start_time_is_running():
    ns = _scan_namespace(_upid(os.getpid(), _my_pstart(), int(time.time()) - 100) + "\n")
    assert ns["active_tasks"]()[11152][0]["state"] == "running"


def test_a_reused_process_id_with_a_different_start_time_is_not_running():
    ns = _scan_namespace(_upid(os.getpid(), _my_pstart() + 5, int(time.time()) - 100) + "\n")
    assert ns["active_tasks"]()[11152][0]["state"] == "dead"


def test_garbage_lines_are_ignored():
    ns = _scan_namespace("not a task\nUPID:short\nUPID:n:zz:zz:zz:vzdump:abc:root@pam:\n")
    assert ns["active_tasks"]() == {}


def _row(**kw):
    return {"vmid": 11152, "lock": "backup", "task_running": False, "stale": True, "task_state": "none", "task": None, **kw}


def test_explanations_are_plain_sentences():
    fin = explain_row(_row(task_state="finished", task={"type": "vzdump", "age_seconds": 3600, "status": "job errors"}), "1.2.1")
    assert "job errors" in fin and "left over" in fin
    ok = explain_row(_row(task_state="finished", task={"type": "qmstart", "age_seconds": 68866, "status": "6AE7F101 OK"}), "1.2.1")
    assert "'OK'" in ok and "6AE7F101" not in ok and "19 h" in ok
    assert "process is gone" in explain_row(_row(task_state="dead", task={"type": "vzdump", "age_seconds": 60}), "1.2.1")
    assert "No task" in explain_row(_row(), "1.2.1")


def test_an_old_wrapper_is_told_to_update_not_to_run_commands():
    text = explain_row({"vmid": 1, "lock": "backup", "task_running": True, "stale": False}, "1.2.0")
    assert "Update the maintenance wrapper" in text and "pvesh" not in text and "ps -ef" not in text


def test_only_a_long_running_real_backup_counts_as_hung():
    hung = _row(task_running=True, stale=False, task_state="running", task={"type": "vzdump", "age_seconds": STUCK_BACKUP_SECONDS + 60, "upid": "UPID:x"})
    fresh = _row(task_running=True, stale=False, task_state="running", task={"type": "vzdump", "age_seconds": 600, "upid": "UPID:y"})
    migrating = _row(task_running=True, lock="migrate", stale=False, task_state="running", task={"type": "qmigrate", "age_seconds": 99999, "upid": "UPID:z"})
    assert stuck_backup(hung) and not stuck_backup(fresh) and not stuck_backup(migrating)
    assert stop_candidates([hung, fresh, migrating]) == [hung]
    assert "hung" in explain_row(hung, "1.2.1") and "stops that backup" in explain_row(hung, "1.2.1")
