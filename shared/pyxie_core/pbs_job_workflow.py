"""pbs.job_change: create, change, delete or run-now a Proxmox Backup Server sync, prune or verify job.

Safety Contract: preview (validate, read the job, show the exact diff) -> approve -> revalidate (job unchanged since
the preview, PBS reachable) -> lock the PBS server -> write -> read back and compare -> audit. PBS sync/prune/verify
jobs are PBS's own; PyXie edits them through PBS's API with a separate "administrative" token.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import pbs_jobs
from .audit import write_audit_event
from .host_maintenance_client import MutationsDisabledError
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Operation, ProtectionTarget
from .operations_engine import (
    OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation,
)
from .pbs_client import PbsApiError, PbsClient, PbsWriteClient
from .pbs_server import PbsNotConfigured, load_pbs_credentials
from . import rollback

now = lambda: datetime.now(timezone.utc)


class PbsJobWorkflowError(Exception):
    pass


def _read_client(db: Session, target: ProtectionTarget) -> PbsClient:
    try:
        return PbsClient(load_pbs_credentials(db, target, "administrative"))
    except PbsNotConfigured:
        return PbsClient(load_pbs_credentials(db, target, "inventory"))


def _find(jobs: list[dict], job_id: str) -> dict | None:
    return next((j for j in jobs if j.get("id") == job_id), None)


def dry_run_job_change(db: Session, target: ProtectionTarget, *, kind: str, action: str, job_id: str, params: dict, actor: str) -> Operation:
    op = create_operation(db, "pbs.job_change", context={"protection_target_id": str(target.id), "kind": kind, "action": action, "job_id": job_id},
                          created_by=actor)
    reasons: list[str] = []
    rules: list[str] = []

    def blocked(extra=None):
        return enter_stage(db, op, status="blocked", stage="dry_run", blocking_safety_rules=rules, actor=actor,
                           dry_run_result={"target": target.name, "kind": kind, "action": action, "job_id": job_id, "eligible": False,
                                           "reasons": reasons, "blocking_safety_rules": rules, **(extra or {})})

    try:
        load_pbs_credentials(db, target, "administrative")
    except PbsNotConfigured:
        reasons.append("no write (administrative) API token is saved for this PBS: add one under Access")
        rules.append("SAFE-PBSJOB-002")
        return blocked()
    try:
        with _read_client(db, target) as c:
            stores = [d["store"] for d in (c.datastores() or [])]
            remotes = [r.get("name") for r in c.remotes()]
            existing = _find(c.job_config(kind), job_id)
            running = c.tasks(running=True, limit=100)
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"could not read the PBS server: {exc}")
        rules.append("SAFE-PBSJOB-002")
        return blocked()

    clean, errors = pbs_jobs.validate_job(kind, action, job_id, params, stores, remotes)
    if errors:
        reasons.extend(errors)
        rules.append("SAFE-PBSJOB-001")
    if action == "create" and existing is not None:
        reasons.append(f"a {kind} job named {job_id} already exists")
        rules.append("SAFE-PBSJOB-001")
    if action in ("update", "delete", "run") and existing is None:
        reasons.append(f"there is no {kind} job named {job_id}")
        rules.append("SAFE-PBSJOB-001")
    if action == "run" and any(t.get("worker_type") in ("syncjob", "prunejob", "verificationjob") and job_id in str(t.get("worker_id")) for t in running):
        reasons.append("that job is already running")
        rules.append("SAFE-PBSJOB-001")
    before = pbs_jobs.normalize(existing, kind) if existing else None
    changes = pbs_jobs.diff(before, clean, kind) if action in ("create", "update") else []
    if action == "update" and not changes and not rules:
        reasons.append("nothing would change")
        rules.append("SAFE-PBSJOB-001")
    if not rules:
        if action == "run":
            reasons.append(f"Run {kind} job {job_id} now on {target.name}. It starts at once and may take hours; follow it on the server page.")
        elif action == "delete":
            reasons.append(f"Delete {kind} job {job_id} on {target.name}. Its settings were: {pbs_jobs.describe(kind, before or {})}. Backups are not deleted.")
        else:
            reasons.append(("Create" if action == "create" else "Change") + f" {kind} job {job_id} on {target.name}:")
            reasons.extend(f"  {c['field']}: {c['before'] if c['before'] is not None else '(not set)'} -> {c['after'] if c['after'] is not None else '(cleared)'}" for c in changes)
    result = {"target": target.name, "kind": kind, "action": action, "job_id": job_id, "eligible": not rules, "reasons": reasons,
              "blocking_safety_rules": rules, "before": before, "changes": changes,
              "summary": pbs_jobs.describe(kind, {**(before or {}), **{k: v for k, v in clean.items() if v is not None}}) if action != "run" else "run now"}
    if rules:
        return blocked({"before": before, "changes": changes})
    ctx = {"protection_target_id": str(target.id), "kind": kind, "action": action, "job_id": job_id, "clean": clean, "before": before}
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, context=ctx, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_job_change(db: Session, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise PbsJobWorkflowError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    ctx = op.context or {}
    target = db.query(ProtectionTarget).filter(ProtectionTarget.id == ctx["protection_target_id"]).one()
    kind, action, job_id, clean, before = ctx["kind"], ctx["action"], ctx["job_id"], ctx.get("clean") or {}, ctx.get("before")
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            try:
                with _read_client(db, target) as c:
                    now_job = _find(c.job_config(kind), job_id)
            except Exception:  # noqa: BLE001
                return block_operation(db, op, blocking_safety_rules=["SAFE-PBSJOB-002"])
            current = pbs_jobs.normalize(now_job, kind) if now_job else None
            if current != before:
                return block_operation(db, op, blocking_safety_rules=["SAFE-PLAN-CHANGED-001"])
            try:
                acquire_lock(db, resource_type="pbs_target", resource_id=target.id, operation_id=op.id, reason="pbs.job_change", ttl_seconds=900)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"])
            op = enter_stage(db, op, status="executing", stage="executing")

        if op.status == "executing" and not op.pve_task_result:
            try:
                with PbsWriteClient(load_pbs_credentials(db, target, "administrative")) as w:
                    if action == "create":
                        w.create_job(kind, {"id": job_id, **{k: v for k, v in clean.items() if v is not None}})
                        result = {"created": True}
                    elif action == "update":
                        setv, delete = pbs_jobs.split_update(clean)
                        w.update_job(kind, job_id, setv, delete)
                        result = {"updated": True}
                    elif action == "delete":
                        w.delete_job(kind, job_id)
                        result = {"deleted": True}
                    else:
                        result = {"upid": w.run_job(kind, job_id)}
            except MutationsDisabledError as exc:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=str(exc))
            except (PbsApiError, Exception) as exc:  # noqa: BLE001
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"PBS refused the change: {exc}")
            op.pve_task_result = result
            db.commit()
            op = enter_stage(db, op, status="verifying", stage="verifying")

        if op.status == "verifying":
            release_locks_for_operation(db, op.id)
            with _read_client(db, target) as c:
                after = _find(c.job_config(kind), job_id)
            problems: list[str] = []
            if action == "delete":
                if after is not None:
                    problems.append("job still exists")
            elif action == "run":
                started = (op.pve_task_result or {}).get("upid")
                if not started:
                    problems.append("PBS did not return a task id")
            else:
                if after is None:
                    problems.append("job not found after the write")
                else:
                    problems = [f"{f} does not read back as set" for f in pbs_jobs.job_matches(kind, after, clean)]
            op.verification_result = {"after": pbs_jobs.normalize(after, kind) if after else None, "problems": problems,
                                      "task": (op.pve_task_result or {}).get("upid")}
            db.commit()
            write_audit_event(db, event_category="protection", event_type=f"pbs.{kind}_job.{action}", actor=op.approved_by or "system",
                              actor_type="user", metadata={"target": target.name, "job_id": job_id, "before": before, "after": op.verification_result["after"]})
            if problems:
                return fail_operation(db, op, error="; ".join(problems))
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE)
        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error: {exc}")
