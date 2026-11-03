"""Newest finished backup / verify / garbage collection from a PBS task list (pure)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.pbs_server import last_runs


def test_picks_the_newest_finished_task_of_each_kind():
    tasks = [
        {"worker_type": "backup", "endtime": 100, "status": "OK"},
        {"worker_type": "backup", "endtime": 300, "status": "unexpected error"},
        {"worker_type": "backup", "endtime": 200, "status": "OK"},
        {"worker_type": "verificationjob", "endtime": 150, "status": "WARNINGS: 1"},
        {"worker_type": "garbage_collection", "endtime": 90, "status": "OK"},
        {"worker_type": "prune", "endtime": 400, "status": "OK"},
        {"worker_type": "backup", "starttime": 500},
    ]
    r = last_runs(tasks)
    assert r["backup"] == {"end": 300, "ok": False}
    assert r["verify"] == {"end": 150, "ok": True}
    assert r["gc"] == {"end": 90, "ok": True}
    assert set(r) == {"backup", "verify", "gc"}


def test_unreadable_list_is_none_and_empty_is_empty():
    assert last_runs(None) is None
    assert last_runs([]) == {}
