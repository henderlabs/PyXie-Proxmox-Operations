"""Job functions for the 'polling' and 'operations' queues.

Phase 0 registered jobs only for the polling workload class. This pass adds
findings/recommendation/protection evaluation to the SAME queue (still just
polling -- these are all read-only, low-cost passes over already-collected
data) rather than introducing a separate 'analysis' consumer prematurely.

Stage W0/W1 gives the 'operations' queue its first-ever consumer:
execute_operation_job, which dispatches to the per-operation-type workflow
module. This is the ONLY job function in this file that can result in a PVE
write -- everything above it stays read-only, same as before.
"""

import logging
import os
from datetime import datetime, timezone

import redis
from rq import Queue

from pyxie_core.db import SessionLocal
from pyxie_core.findings import evaluate_findings
from pyxie_core.metrics import prune_old_metrics
from pyxie_core import op_claims
from pyxie_core.op_lanes import RESUMABLE_TYPES, enqueue_operation, lane_for
from pyxie_core.models import (
    InternalJobRun,
    PveTarget,
    Provider,
    ProtectionTarget,
)
from pyxie_core.protection import sync_pbs_protection
from pyxie_core.recommendations import generate_recommendations

log = logging.getLogger(__name__)
_conn = redis.from_url(os.environ["REDIS_URL"])
operations_queue = Queue("operations", connection=_conn)


def _run_logged(job_name: str, fn):
    db = SessionLocal()
    run = InternalJobRun(job_name=job_name, started_at=datetime.now(timezone.utc), status="running")
    db.add(run)
    db.commit()
    db.refresh(run)
    result_summary = None
    try:
        result = fn(db)
        result_summary = result if isinstance(result, dict) else {"result": str(result)}
        run.status = "success"
        run.result_summary = result_summary
    except Exception as e:  # noqa: BLE001 -- always record the failure, never crash the worker
        db.rollback()
        run.status = "failed"
        run.error = str(e)
    finally:
        run.ended_at = datetime.now(timezone.utc)
        db.commit()
        db.close()
    # read into a local before the session closed and expired the instance
    return result_summary


def discover_all_targets():
    from pyxie_core.discovery import run_discovery

    def _job(db):
        targets = (
            db.query(PveTarget)
            .join(Provider, PveTarget.provider_id == Provider.id)
            .filter(Provider.enabled.is_(True))
            .all()
        )
        results = {}
        for target in targets:
            results[str(target.id)] = run_discovery(db, target, actor="worker")
        return results

    return _run_logged("discovery", _job)


def sync_protection_targets():
    def _job(db):
        targets = (
            db.query(ProtectionTarget)
            .join(Provider, ProtectionTarget.provider_id == Provider.id)
            .filter(Provider.enabled.is_(True), Provider.provider_type == "pbs")
            .all()
        )
        results = {}
        for target in targets:
            results[str(target.id)] = sync_pbs_protection(db, target, actor="worker")
        return results

    return _run_logged("protection_sync", _job)


def pbs_status_job():
    """Read each Proxmox Backup Server's status (version, load, disk, pending updates, tasks, datastore use) and store it."""
    def _job(db):
        from pyxie_core.pbs_server import refresh_status, status_is_stale
        out = {}
        for target in db.query(ProtectionTarget).all():
            if status_is_stale(target, minutes=4):
                out[str(target.id)] = bool(refresh_status(db, target).get("reachable"))
        return out

    return _run_logged("pbs_status", _job)


def stale_previews_job():
    from pyxie_core.operations_engine import fail_stale_previews
    return _run_logged("stale_previews", lambda db: {"closed": fail_stale_previews(db)})


def evaluate_findings_job():
    return _run_logged("findings_evaluation", lambda db: evaluate_findings(db))


def generate_recommendations_job():
    return _run_logged("recommendation_generation", lambda db: generate_recommendations(db))


def auto_balance_job():
    """Automatic balancing: prepares a plan when a cluster set to recommend or auto_approve is out of balance. Only auto_approve
    clusters have it approved and queued here (one move at a time); recommend clusters wait for a person."""
    from pyxie_core.auto_balance import evaluate_all

    def enqueue(op):
        enqueue_operation(_conn, op.id, op.operation_type_id, timeout=10800)

    return _run_logged("auto_balance", lambda db: {"clusters": evaluate_all(db, enqueue=enqueue)})


def auto_clear_locks_job():
    """Clears stale backup/snapshot guest locks when that policy is turned on (off by default); see pyxie_core.auto_clear_locks."""
    from pyxie_core.auto_clear_locks import evaluate

    def enqueue(op):
        enqueue_operation(_conn, op.id, op.operation_type_id, timeout=900)

    return _run_logged("auto_clear_locks", lambda db: evaluate(db, enqueue=enqueue))


def requeue_deferred_job():
    """Operations waiting on a busy guest or host (op_claims.defer_operation) whose retry time has come go back on their lane's queue."""
    def _job(db):
        out = []
        for op in op_claims.deferred_due(db):
            ctx = dict(op.context or {})
            ctx["retry_at"] = None  # enqueued: not again until it defers again
            op.context = ctx
            db.commit()
            enqueue_operation(_conn, op.id, op.operation_type_id)
            out.append(str(op.id))
        return {"requeued": out}

    return _run_logged("requeue_deferred", _job)


def reclaim_stale_operations_job():
    """A running operation whose worker stopped heartbeating (2 minutes) is taken over: its claim is cleared and it goes back on its lane's queue,
    where the workflow resumes from its saved state. Only the types that resume after a restart; at most 3 times, then it fails."""
    from pyxie_core.operations_engine import fail_operation

    def _job(db):
        out = []
        for op in op_claims.stale_claims(db):
            if op.operation_type_id not in RESUMABLE_TYPES:
                continue
            ctx = dict(op.context or {})
            n = int(ctx.get("reclaims") or 0)
            if n >= op_claims.MAX_RECLAIMS:
                op_claims.release_claim(db, op.id)
                fail_operation(db, op, error="its worker stopped three times while running it; check it in Proxmox before running it again")
                continue
            ctx["reclaims"] = n + 1
            op.context = ctx
            db.commit()
            op_claims.release_claim(db, op.id)
            enqueue_operation(_conn, op.id, op.operation_type_id)
            out.append(str(op.id))
        return {"reclaimed": out}

    return _run_logged("reclaim_stale_operations", _job)


def collect_reporting_job(actor: str = "schedule"):
    """Reporting-page inventory (disks/NICs/snapshots/guest IPs). Runs on the
    low-priority 'analysis' queue so a long pass can never delay discovery
    or an operation; also enqueued on demand by the Refresh button."""
    from pyxie_core.report_collection import collect_reporting_data

    return _run_logged("report_collection", lambda db: collect_reporting_data(db, actor=actor))


def prune_metrics_job():
    return _run_logged("metrics_pruning", lambda db: {"deleted": prune_old_metrics(db)})


def execute_operation_job(operation_id: str):
    """Dispatches one Operation row to its operation_type's workflow module.
    Resumable by construction: this can be (and, after a worker restart via
    resume_inflight_operations(), will be) called again for the same
    operation_id -- the workflow module itself decides where to pick up
    based on the row's own persisted state, never from any in-memory state
    this job might have held before a crash.
    """
    from pyxie_core.models import Operation

    def _job(db):
        op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
        if op is None:
            return {"error": f"operation {operation_id} not found"}
        if not op_claims.try_claim(db, operation_id):
            return {"skipped": "another live worker already runs this operation"}
        stop_heartbeat = op_claims.start_heartbeat(operation_id)
        try:
            return _dispatch(db, op, operation_id)
        finally:
            stop_heartbeat()
            try:
                db.rollback()
                op_claims.release_claim(db, operation_id)
            except Exception:  # noqa: BLE001
                log.exception("could not release the claim on %s", operation_id)

    def _dispatch(db, op, operation_id):
        if op.status == "revalidating":
            # the worker stopped while re-checking: re-checking changes nothing, so start that stage over instead of failing the operation
            op.status = "approved"
            db.commit()
        if op.operation_type_id == "vm.live_migrate":
            from pyxie_core.migration_workflow import execute_migration
            result = execute_migration(db, operation_id)
        elif op.operation_type_id == "node.evacuate":
            from pyxie_core.evacuation_workflow import execute_evacuation
            result = execute_evacuation(db, operation_id)
        elif op.operation_type_id in ("workload.shutdown", "workload.start", "workload.force_stop", "workload.reboot"):
            from pyxie_core.lifecycle_workflow import execute_lifecycle_action
            result = execute_lifecycle_action(db, operation_id)
        elif op.operation_type_id == "workload.bulk_lifecycle":
            from pyxie_core.bulk_lifecycle_workflow import execute_bulk_lifecycle
            result = execute_bulk_lifecycle(db, operation_id)
        elif op.operation_type_id == "workload.resize":
            from pyxie_core.resize_workflow import execute_resize
            result = execute_resize(db, operation_id)
        elif op.operation_type_id == "workload.move_storage":
            from pyxie_core.storage_move_workflow import execute_storage_move
            result = execute_storage_move(db, operation_id)
        elif op.operation_type_id in ("workload.snapshot_create", "workload.snapshot_delete", "workload.snapshot_rollback"):
            from pyxie_core.snapshot_workflow import execute_snapshot
            result = execute_snapshot(db, operation_id)
        elif op.operation_type_id == "workload.tags_change":
            from pyxie_core.tags_workflow import execute_tags_change
            result = execute_tags_change(db, operation_id)
        elif op.operation_type_id in ("pool.create", "pool.delete", "pool.members"):
            from pyxie_core.pool_workflow import execute_pool_op
            result = execute_pool_op(db, operation_id)
        elif op.operation_type_id in ("storage.create", "storage.remove"):
            from pyxie_core.storage_workflow import execute_storage_op
            result = execute_storage_op(db, operation_id)
        elif op.operation_type_id in ("workload.ct_resize", "workload.ct_nic_change"):
            from pyxie_core.ct_edit_workflow import execute_ct_edit
            result = execute_ct_edit(db, operation_id)
        elif op.operation_type_id in ("workload.ct_create", "storage.ct_template_download", "storage.ct_template_delete"):
            from pyxie_core.ct_workflow import execute_ct_op
            result = execute_ct_op(db, operation_id)
        elif op.operation_type_id == "workload.cdrom_change":
            from pyxie_core.cdrom_workflow import execute_cdrom
            result = execute_cdrom(db, operation_id)
        elif op.operation_type_id == "workload.clone":
            from pyxie_core.clone_workflow import execute_clone
            result = execute_clone(db, operation_id)
        elif op.operation_type_id == "workload.delete":
            from pyxie_core.delete_workflow import execute_delete
            result = execute_delete(db, operation_id)
        elif op.operation_type_id in ("storage.iso_download", "storage.iso_delete", "storage.iso_enable"):
            from pyxie_core.iso_workflow import execute_iso_op
            result = execute_iso_op(db, operation_id)
        elif op.operation_type_id == "workload.create":
            from pyxie_core.vm_create_workflow import execute_vm_create
            result = execute_vm_create(db, operation_id)
        elif op.operation_type_id == "backup.policy_change":
            from pyxie_core.backup_policy_workflow import execute_policy_change
            result = execute_policy_change(db, operation_id)
        elif op.operation_type_id == "workload.disk_change":
            from pyxie_core.disk_workflow import execute_disk_change
            result = execute_disk_change(db, operation_id)
        elif op.operation_type_id == "workload.nic_change":
            from pyxie_core.nic_workflow import execute_nic_change
            result = execute_nic_change(db, operation_id)
        elif op.operation_type_id == "workload.disk_grow":
            from pyxie_core.hw_edit_workflow import execute_disk_grow
            result = execute_disk_grow(db, operation_id)
        elif op.operation_type_id == "workload.hw_change":
            from pyxie_core.hw_edit_workflow import execute_hw_change
            result = execute_hw_change(db, operation_id)
        elif op.operation_type_id == "cluster.tag_colors":
            from pyxie_core.tag_colors_workflow import execute_tag_colors
            result = execute_tag_colors(db, operation_id)
        elif op.operation_type_id == "host.reboot":
            from pyxie_core.reboot_workflow import execute_reboot
            result = execute_reboot(db, operation_id)
        elif op.operation_type_id == "host.config_change":
            from pyxie_core.node_config_workflow import execute_node_config
            result = execute_node_config(db, operation_id)
        elif op.operation_type_id == "host.shutdown":
            from pyxie_core.host_power_workflow import execute_shutdown
            result = execute_shutdown(db, operation_id)
        elif op.operation_type_id == "host.network_stage":
            from pyxie_core.host_network_workflow import execute_network_stage
            result = execute_network_stage(db, operation_id)
        elif op.operation_type_id == "host.network_apply":
            from pyxie_core.host_network_workflow import execute_network_apply
            result = execute_network_apply(db, operation_id)
        elif op.operation_type_id == "host.network_discard":
            from pyxie_core.host_network_workflow import execute_network_discard
            result = execute_network_discard(db, operation_id)
        elif op.operation_type_id == "workload.config_change":
            from pyxie_core.config_change_workflow import execute_config_change
            result = execute_config_change(db, operation_id)
        elif op.operation_type_id == "host.clear_locks":
            from pyxie_core.clear_lock_workflow import execute_clear_locks
            result = execute_clear_locks(db, operation_id)
        elif op.operation_type_id == "pbs.storage_add":
            from pyxie_core.pbs_storage_workflow import execute_pbs_storage_add
            result = execute_pbs_storage_add(db, operation_id)
        elif op.operation_type_id == "pbs.datastore_change":
            from pyxie_core.pbs_datastore_workflow import execute_datastore_change
            result = execute_datastore_change(db, operation_id)
        elif op.operation_type_id == "workload.restore":
            from pyxie_core.restore_workflow import execute_restore
            result = execute_restore(db, operation_id)
        elif op.operation_type_id == "pbs.reboot":
            from pyxie_core.pbs_reboot_workflow import execute_pbs_reboot
            result = execute_pbs_reboot(db, operation_id)
        elif op.operation_type_id == "pbs.maintenance":
            from pyxie_core.pbs_maintenance_workflow import execute_pbs_maintenance
            result = execute_pbs_maintenance(db, operation_id)
        elif op.operation_type_id == "pbs.update":
            from pyxie_core.pbs_update_workflow import execute_pbs_update
            result = execute_pbs_update(db, operation_id)
        elif op.operation_type_id == "pbs.job_change":
            from pyxie_core.pbs_job_workflow import execute_job_change
            result = execute_job_change(db, operation_id)
        elif op.operation_type_id == "host.update":
            from pyxie_core.host_update_workflow import execute_host_update
            result = execute_host_update(db, operation_id)
        elif op.operation_type_id == "maintenance.run":
            from pyxie_core.maintenance_workflow import execute_maintenance
            result = execute_maintenance(db, operation_id)
        elif op.operation_type_id == "node.enter_maintenance":
            from pyxie_core.node_maintenance_workflow import execute_enter_maintenance
            result = execute_enter_maintenance(db, operation_id)
        elif op.operation_type_id == "node.exit_maintenance":
            from pyxie_core.node_maintenance_workflow import execute_exit_maintenance
            result = execute_exit_maintenance(db, operation_id)
        elif op.operation_type_id == "cluster.rebalance":
            from pyxie_core.balance_workflow import execute_balance
            result = execute_balance(db, operation_id)
        elif op.operation_type_id == "protection.backup_retention":
            from pyxie_core.retention_workflow import execute_job_retention
            result = execute_job_retention(db, operation_id)
        elif op.operation_type_id == "protection.backup_membership":
            from pyxie_core.protection_workflow import execute_backup_membership
            result = execute_backup_membership(db, operation_id)
        elif op.operation_type_id == "workload.network_vlan_change":
            from pyxie_core.network_workflow import execute_vlan_change
            result = execute_vlan_change(db, operation_id)
        else:
            return {"error": f"no workflow implementation for operation type {op.operation_type_id!r}"}
        return {"operation_id": str(result.id), "status": result.status, "stage": result.stage}

    return _run_logged(f"execute_operation:{operation_id}", _job)


def resume_inflight_operations(lanes=("quick", "bulk")):
    """Called once at worker startup: find every operation that was
    mid-flight (not waiting on human approval, not terminal) when the
    process last stopped -- crash, restart, redeploy, docker compose down --
    and re-enqueue it. This is what makes 'operations persist state in
    PostgreSQL and survive worker/API/container restarts' actually true
    rather than just a design intention.

    Each lane's worker resumes only its own lane's operations (one worker per lane, so nothing is picked up twice);
    with the quick lane switched off the one operations worker resumes both. Which types resume, and their job timeouts,
    come from pyxie_core.op_lanes.
    """
    from pyxie_core.operations_engine import get_resumable_operations

    def _job(db):
        now = datetime.now(timezone.utc)
        me = op_claims.worker_id()
        resumable = [o for o in get_resumable_operations(db) if lane_for(o.operation_type_id) in lanes
                     and not op_claims.claim_is_live(o.claimed_by, o.heartbeat_at, now, me=me)]  # one another live worker is running is left alone
        for op in resumable:
            if op.operation_type_id in RESUMABLE_TYPES:
                op_claims.release_claim(db, op.id)  # a dead predecessor's claim: cleared so the stale-claim sweep does not enqueue it a second time
                enqueue_operation(_conn, op.id, op.operation_type_id)
        return {"resumed": [str(op.id) for op in resumable], "lanes": list(lanes)}

    return _run_logged("resume_inflight_operations", _job)


def run_all():
    """The single scheduled entry point: discovery -> protection -> findings
    -> recommendations, in that dependency order, each independently logged
    and each tolerant of the others failing.
    """
    stale_previews_job()
    discover_all_targets()
    sync_protection_targets()
    pbs_status_job()
    evaluate_findings_job()
    generate_recommendations_job()
    auto_balance_job()
    auto_clear_locks_job()
    requeue_deferred_job()
    reclaim_stale_operations_job()
