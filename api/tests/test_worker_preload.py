"""The worker must import everything its threads and jobs use before it starts any thread: RQ forks a child per job, and a thread caught
mid-import at that moment leaves an import lock held forever in the child, so the first job after every restart hung for its 300 s timeout."""

import importlib.util
import re
import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[2] / "worker"
MAIN = WORKER_DIR / "worker" / "main.py"


def _preload_modules() -> list[str]:
    text = MAIN.read_text()
    block = re.search(r"PRELOAD_MODULES = \((.*?)\n\)", text, re.S).group(1)
    return re.findall(r'"([\w.]+)"', block)


def test_preload_runs_before_any_thread_starts():
    text = MAIN.read_text()
    main_body = text[text.index("def main():"):]
    assert "_preload()" in main_body
    assert main_body.index("_preload()") < main_body.index("threading.Thread(")


def test_every_preloaded_module_exists():
    sys.path.insert(0, str(WORKER_DIR))
    try:
        missing = [m for m in _preload_modules() if importlib.util.find_spec(m) is None]
    finally:
        sys.path.remove(str(WORKER_DIR))
    assert missing == []


def test_the_modules_the_threads_and_jobs_import_are_in_the_list():
    listed = set(_preload_modules())
    text = MAIN.read_text()
    thread_imports = set(re.findall(r"from (pyxie_core\.\w+) import", text[text.index("def live_memory_loop"):text.index("OPS_ROLE_ENV")]))
    jobs_text = (WORKER_DIR / "worker" / "jobs.py").read_text()
    # the polling-queue jobs: everything run_all and its siblings import lazily, before the operations dispatcher
    polling_part = jobs_text[:jobs_text.index("def execute_operation_job")]
    job_imports = set(re.findall(r"^\s+from (pyxie_core\.\w+) import", polling_part, re.M)) - {"pyxie_core.models"}
    # modules imported at the top of main.py or jobs.py are loaded before any thread starts anyway
    top_level = set(re.findall(r"^from (pyxie_core\.\w+) import", text, re.M)) | set(re.findall(r"^from (pyxie_core\.\w+) import", jobs_text, re.M))
    wanted = (thread_imports | job_imports) - top_level
    assert wanted <= listed, sorted(wanted - listed)
    assert "worker.jobs" in listed
