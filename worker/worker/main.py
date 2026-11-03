import logging
import os
import subprocess
import sys
import threading
import time

import redis
from rq import Queue, Worker
from sqlalchemy.exc import OperationalError

from pyxie_core import op_lanes
from pyxie_core.db import SessionLocal
from pyxie_core.models import AppSettings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pyxie-worker")

REDIS_URL = os.environ["REDIS_URL"]
conn = redis.from_url(REDIS_URL)

# Phase 0 queue topology: polling is used today; analysis is listened-to but
# has no jobs yet. Stage W0/W1 gives 'operations' its first producer (the
# /api/operations/{id}/approve endpoint) and consumer (this worker, below) --
# it is now the only queue whose jobs can reach a PVE write, and only via
# execute_operation_job -> a per-operation-type workflow module.
POLLING_QUEUE = "polling"
ANALYSIS_QUEUE = "analysis"
OPERATIONS_QUEUE = op_lanes.BULK_QUEUE
QUICK_QUEUE = op_lanes.QUICK_QUEUE

polling_q = Queue(POLLING_QUEUE, connection=conn)
analysis_q = Queue(ANALYSIS_QUEUE, connection=conn)


def _wait_for_db():
    while True:
        try:
            db = SessionLocal()
            db.query(AppSettings).count()
            db.close()
            return
        except OperationalError:
            log.info("database not ready yet, retrying in 3s...")
            time.sleep(3)


def _get_interval_seconds() -> int:
    db = SessionLocal()
    try:
        row = db.query(AppSettings).filter(AppSettings.id == 1).one_or_none()
        return row.inventory_refresh_interval_seconds if row else 300
    finally:
        db.close()


PRUNE_EVERY_N_CYCLES = 288  # once a day at the default 5-minute interval
REPORT_COLLECT_EVERY_N_CYCLES = 6  # reporting inventory: every 30 min at the default interval (first pass on startup)


def scheduler_loop():
    _wait_for_db()
    log.info("scheduler started")
    cycle = 0
    while True:
        try:
            interval = _get_interval_seconds()
        except Exception:
            interval = 300
        log.info("enqueuing run_all (discovery -> protection -> findings -> recommendations), next run in %ss", interval)
        polling_q.enqueue("worker.jobs.run_all", job_timeout=300)
        if cycle % REPORT_COLLECT_EVERY_N_CYCLES == 0:
            analysis_q.enqueue("worker.jobs.collect_reporting_job", job_timeout=1800)
        cycle += 1
        if cycle % PRUNE_EVERY_N_CYCLES == 0:
            polling_q.enqueue("worker.jobs.prune_metrics_job", job_timeout=120)
        time.sleep(interval)


def live_memory_loop():
    """Keeps the RAM meter within ~30s of PVE's own summary screen. Separate from the
    heavy 5-minute sync so it is never held up by it; a failure only means the meter
    falls back to the last sync's value (readers ignore rows older than 3 minutes)."""
    from pyxie_core.live_memory import INTERVAL_SECONDS, refresh_live_memory

    _wait_for_db()
    log.info("live memory refresher started (every %ss)", INTERVAL_SECONDS)
    failures = 0
    while True:
        db = SessionLocal()
        try:
            refresh_live_memory(db)
            failures = 0
        except Exception:
            db.rollback()
            failures += 1
            if failures in (1, 10) or failures % 120 == 0:  # don't flood the log if PVE is down
                log.exception("live memory refresh failed (%s in a row)", failures)
        finally:
            db.close()
        time.sleep(INTERVAL_SECONDS)


def liveness_loop():
    """Probes every running VM's QEMU about once a minute so a hung VM is noticed within minutes,
    not when a live migration hangs. Its own thread and table, like the live-memory refresher; a
    failure only means results go stale (findings and readers ignore stale rows)."""
    from pyxie_core.liveness_watch import INTERVAL_SECONDS, refresh_liveness

    _wait_for_db()
    log.info("liveness watcher started (every %ss)", INTERVAL_SECONDS)
    failures = 0
    while True:
        started = time.monotonic()
        db = SessionLocal()
        try:
            result = refresh_liveness(db)
            failures = 0
            if result.get("bad"):
                log.warning("liveness: %s of %s running VMs not answering normally", result["bad"], result["probed"])
        except Exception:
            db.rollback()
            failures += 1
            if failures in (1, 10) or failures % 60 == 0:
                log.exception("liveness refresh failed (%s in a row)", failures)
        finally:
            db.close()
        time.sleep(max(5.0, INTERVAL_SECONDS - (time.monotonic() - started)))


# Everything the background threads and the polling-queue jobs import, at the top of a function or lazily.
PRELOAD_MODULES = (
    "worker.jobs",
    "pyxie_core.live_memory",
    "pyxie_core.liveness_watch",
    "pyxie_core.discovery",
    "pyxie_core.pbs_server",
    "pyxie_core.operations_engine",
    "pyxie_core.auto_balance",
    "pyxie_core.auto_clear_locks",
    "pyxie_core.op_lanes",
    "pyxie_core.op_claims",
    "pyxie_core.slots",
    "pyxie_core.parallel_children",
    "pyxie_core.report_collection",
)


def _preload():
    """Import every module the threads and the queue jobs use, before any thread starts.

    RQ runs each job in a forked child. A thread that is part-way through an import when the fork happens leaves that module's
    import lock held in the child, with no thread left to release it, so the child's own import of the same module waits until
    the 300 s job timeout. The first discovery pass after every restart hit this: inventory and findings stood still for five
    minutes. Importing here, while only one thread exists, leaves the threads and the jobs nothing left to import."""
    import importlib

    for name in PRELOAD_MODULES:
        importlib.import_module(name)
    # httpx imports httpcore (and builds its TLS context) inside Client(...), not at import time: that is the import the first
    # discovery pass was still waiting on (py-spy: HTTPTransport.__init__ -> _ModuleLock.acquire in the forked job). Build one of
    # each kind of client now so nothing is left to import lazily.
    import httpx
    for verify in (True, False):
        httpx.Client(verify=verify).close()


OPS_ROLE_ENV = "PYXIE_WORKER_ROLE"
QUICK_LANE_ENV = "PYXIE_QUICK_LANE"  # "1" when this install runs a quick-lane worker (Platform > Settings > Workers)


def _bulk_worker_count() -> int:
    """How many long-job workers: 1 unless the Large profile is chosen (limits.bulk_workers, 2 to 4)."""
    try:
        from pyxie_core.models import Policy
        db = SessionLocal()
        try:
            prof = db.query(Policy).filter(Policy.key == "workers.profile", Policy.scope_type == "organization").one_or_none()
            if not (prof and isinstance(prof.value, dict) and prof.value.get("profile") == "large"):
                return 1
            n = db.query(Policy).filter(Policy.key == "limits.bulk_workers", Policy.scope_type == "organization").one_or_none()
            return max(1, min(4, int(n.value.get("count")))) if n and isinstance(n.value, dict) else 1
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        log.exception("could not read limits.bulk_workers; running one long-job worker")
        return 1


def _quick_lane_wanted() -> bool:
    """Whether the operator chose "Standard: quick + bulk" (policy workers.profile). Read once at container start; a change applies at the
    next worker restart. Any trouble reading it means the single worker PyXie has always had."""
    try:
        from pyxie_core.models import Policy
        db = SessionLocal()
        try:
            p = db.query(Policy).filter(Policy.key == "workers.profile", Policy.scope_type == "organization").one_or_none()
            return bool(p and isinstance(p.value, dict) and p.value.get("profile") in ("standard", "large"))
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        log.exception("could not read workers.profile; running a single operations worker")
        return False


def _supervise_operations_worker(quick_lane: bool, slot: int = 1):
    """Runs the operations queue in its own process, restarted if it ever exits. Approved operations (a rebalance, a
    migration, a reboot) used to wait behind whatever the one worker was doing -- the 5-minute inventory poll, which on a
    large cluster takes a minute or more (6 minutes right after a restart) -- and while a long operation ran, polling
    stopped. Separate processes, same container: nothing changes for installs, compose or the updater."""
    env = dict(os.environ, **{OPS_ROLE_ENV: "operations", QUICK_LANE_ENV: "1" if quick_lane else "0", "PYXIE_WORKER_ID": f"bulk-{slot}"})
    _supervise([sys.executable, "-m", "worker.main"], env, "operations worker")


def _supervise_quick_worker():
    """The quick lane: its own process for short actions, so a shutdown never waits behind a disk copy."""
    env = dict(os.environ, **{OPS_ROLE_ENV: "operations-quick", QUICK_LANE_ENV: "1", "PYXIE_WORKER_ID": "quick-1"})
    _supervise([sys.executable, "-m", "worker.main"], env, "quick-lane worker")


def _supervise(cmd, env, label):
    while True:
        proc = subprocess.Popen(cmd, env=env)
        code = proc.wait()
        log.warning("%s exited (code %s); restarting in 5s", label, code)
        time.sleep(5)


def run_operations_worker():
    """The bulk (or only) operations process: resumes anything of its lanes that was mid-flight when the container last stopped,
    then works the operations queue and nothing else. With the quick lane off it also drains the quick queue, so nothing queued
    while the lane was on is ever stranded."""
    _wait_for_db()
    quick_lane = os.environ.get(QUICK_LANE_ENV) == "1"
    lanes = ("bulk",) if quick_lane else ("quick", "bulk")
    try:
        from worker.jobs import resume_inflight_operations
        # one long-job worker resumes what a restart interrupted; the others would only race it (a claim stops double runs anyway)
        result = resume_inflight_operations(lanes) if os.environ.get("PYXIE_WORKER_ID", "bulk-1") == "bulk-1" else {"skipped": "bulk-1 resumes"}
        log.info("resume_inflight_operations on startup: %s", result)
    except Exception:
        log.exception("resume_inflight_operations failed on startup -- any mid-flight operations "
                      "will stay in their current status until manually re-enqueued or the next restart")
    queues = [OPERATIONS_QUEUE] + ([] if quick_lane else [QUICK_QUEUE])
    log.info("operations worker listening on: %s", ", ".join(queues))
    Worker(queues, connection=conn).work(with_scheduler=False)


def _quick_heartbeat():
    """While this process lives, the key that tells the API and the other workers to route quick actions here stays set."""
    while True:
        try:
            conn.set(op_lanes.QUICK_ALIVE_KEY, "1", ex=op_lanes.QUICK_ALIVE_SECONDS)
        except Exception:  # noqa: BLE001
            log.warning("quick-lane heartbeat could not be written")
        time.sleep(15)


def run_quick_worker():
    _wait_for_db()
    try:
        from worker.jobs import resume_inflight_operations
        log.info("resume_inflight_operations (quick lane) on startup: %s", resume_inflight_operations(("quick",)))
    except Exception:
        log.exception("resume_inflight_operations failed on startup (quick lane)")
    threading.Thread(target=_quick_heartbeat, daemon=True).start()
    log.info("quick-lane worker listening on: %s", QUICK_QUEUE)
    Worker([QUICK_QUEUE], connection=conn).work(with_scheduler=False)


def main():
    role = os.environ.get(OPS_ROLE_ENV)
    if role == "operations":
        run_operations_worker()
        return
    if role == "operations-quick":
        run_quick_worker()
        return

    _preload()  # before any thread starts: see _preload
    threading.Thread(target=scheduler_loop, daemon=True).start()
    threading.Thread(target=live_memory_loop, daemon=True).start()
    threading.Thread(target=liveness_loop, daemon=True).start()
    _wait_for_db()
    quick_lane = _quick_lane_wanted()
    for slot in range(1, _bulk_worker_count() + 1):
        threading.Thread(target=_supervise_operations_worker, args=(quick_lane, slot), daemon=True).start()
    if quick_lane:
        threading.Thread(target=_supervise_quick_worker, daemon=True).start()

    log.info("rq worker listening on: %s, %s (operations run in their own process%s)", POLLING_QUEUE, ANALYSIS_QUEUE, " plus a quick lane" if quick_lane else "")
    Worker([POLLING_QUEUE, ANALYSIS_QUEUE], connection=conn).work(with_scheduler=False)


if __name__ == "__main__":
    main()
