"""pbs.update: install pending package updates on a Proxmox Backup Server, over the same pinned-key SSH wrapper
(pyxie-maint) PyXie uses for Proxmox VE hosts.

Safety Contract: preview (wrapper version, refresh, plan, and that PBS is idle: no backup, sync, prune, verify or
garbage collection running) -> approve -> revalidate (plan unchanged, PBS still idle) -> lock -> refresh and re-diff
once more -> apt dist-upgrade through the wrapper -> verify against the live package list -> audit.
A reboot, if the new kernel needs one, is reported but never done by this operation.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import rollback
from .audit import write_audit_event
from .host_maintenance_client import (
    HostMaintenanceClient, HostMaintenanceConnectionError, HostMaintenanceProtocolError, MutationsDisabledError,
    SUPPORTED_CONTRACT_VERSION, sanitize_captured_output,
)
from .host_update_workflow import MIN_FREE_DISK_BYTES, _package_key, _packages_materially_differ
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import HostMaintenanceCredential, Operation, ProtectionTarget
from .operation_log import HostLogStreamer, log_stage
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pbs_server import PbsNotConfigured, busy_reasons, load_pbs_ssh_credentials

now = lambda: datetime.now(timezone.utc)


class PbsUpdateError(Exception):
    pass


def dry_run_pbs_update(db: Session, target: ProtectionTarget, *, actor: str, check_only: bool = False, parent_operation_id=None) -> Operation:
    op = create_operation(db, "pbs.update", parent_operation_id=parent_operation_id, context={"protection_target_id": str(target.id), "target": target.name, "node": target.name}, created_by=actor)
    reasons: list[str] = []
    rules: list[str] = []

    def blocked(extra=None):
        return enter_stage(db, op, status="blocked", stage="dry_run", blocking_safety_rules=rules, actor=actor,
                           dry_run_result={"target": target.name, "node": target.name, "eligible": False, "reasons": reasons, "blocking_safety_rules": rules, **(extra or {})})

    cred = db.query(HostMaintenanceCredential).filter(HostMaintenanceCredential.protection_target_id == target.id).one_or_none()
    if cred is None:
        reasons.append("no SSH access is set up for this PBS: see Access, step 2")
        rules.append("SAFE-HOSTCRED-001")
        return blocked()
    if not target.ssh_host_key_base64:
        reasons.append("the PBS server's SSH host key is not pinned yet: see Access, step 3")
        rules.append("SAFE-HOSTCRED-001")
        return blocked()
    try:
        hm = load_pbs_ssh_credentials(db, target)
        with HostMaintenanceClient(hm) as client:
            version = client.version()
            if version.get("contract_version") != SUPPORTED_CONTRACT_VERSION:
                reasons.append(f"wrapper contract {version.get('contract_version')} does not match PyXie's {SUPPORTED_CONTRACT_VERSION}: re-run the host kit on the PBS")
                rules.append("SAFE-HOSTCONTRACT-001")
                return blocked()
            status = client.status()
            client.refresh()
            plan = client.plan()
    except (HostMaintenanceConnectionError, HostMaintenanceProtocolError, PbsNotConfigured) as exc:
        reasons.append(f"could not reach the PBS host wrapper: {exc}")
        rules.append("SAFE-HOSTCONN-001")
        return blocked()
    cred.status = "valid"
    cred.last_validated_at = now()
    cred.last_seen_wrapper_version = version.get("wrapper_version")
    cred.last_seen_contract_version = version.get("contract_version")
    target.wrapper_version = version.get("wrapper_version")
    target.wrapper_checked_at = now()
    db.commit()

    packages = plan.get("packages", [])
    free = status.get("disk_free_bytes")
    if free is not None and free < MIN_FREE_DISK_BYTES:
        reasons.append(f"only {free} bytes free on /: refusing to plan an update below the {MIN_FREE_DISK_BYTES} byte floor")
        rules.append("SAFE-DISK-001")
    try:
        busy = busy_reasons(db, target)
    except Exception as exc:  # noqa: BLE001
        busy = [f"could not check what PBS and the Proxmox hosts are doing right now: {exc}"]
    if busy:
        reasons.extend(busy)
        rules.append("SAFE-PBS-001")
    if not packages:
        reasons.append("no pending package updates right now")
    else:
        names = ", ".join(str(p.get("package")) for p in packages[:12]) + (f" and {len(packages) - 12} more" if len(packages) > 12 else "")
        reasons.append(f"Install {len(packages)} package(s) on {target.name}: {names}.")
        if status.get("reboot_required"):
            reasons.append("The server already needs a reboot; PyXie will not reboot it. Do that yourself when no backups are running.")
    result = {"target": target.name, "node": target.name, "wrapper_version": version.get("wrapper_version"), "kernel_version": status.get("kernel_version"),
              "product_version": status.get("pve_version"), "disk_free_bytes": free, "reboot_required_hint": status.get("reboot_required"),
              "planned_packages": packages, "planned_package_count": len(packages), "plan_computed_at": plan.get("computed_at") or now().isoformat(),
              "eligible": not rules, "reasons": reasons, "blocking_safety_rules": rules}
    ctx = {"protection_target_id": str(target.id), "target": target.name, "node": target.name, "planned_packages": packages, "before_status": status}
    if rules and not (check_only and not packages and rules == ["SAFE-PBS-001"]):
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=rules, context=ctx, actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, context=ctx, actor=actor)
    if not packages:
        return enter_stage(db, op, status="completed", stage="audit", actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_pbs_update(db: Session, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise PbsUpdateError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    ctx = op.context or {}
    target = db.query(ProtectionTarget).filter(ProtectionTarget.id == ctx["protection_target_id"]).one()
    approved = ctx.get("planned_packages", [])
    try:
        hm = load_pbs_ssh_credentials(db, target)
    except Exception as exc:  # noqa: BLE001
        return fail_operation(db, op, error=f"could not load the SSH credential: {exc}")
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            log_stage(op.id, f"Approved. Re-checking {target.name}: PBS idle and the package plan unchanged.")
            try:
                if busy_reasons(db, target):
                    return block_operation(db, op, blocking_safety_rules=["SAFE-PBS-001"])
            except Exception:  # noqa: BLE001
                return block_operation(db, op, blocking_safety_rules=["SAFE-PBS-001"])
            try:
                with HostMaintenanceClient(hm) as client:
                    client.refresh()
                    fresh = client.plan()
            except (HostMaintenanceConnectionError, HostMaintenanceProtocolError):
                return block_operation(db, op, blocking_safety_rules=["SAFE-HOSTCONN-001"])
            if _packages_materially_differ(approved, fresh.get("packages", [])):
                return block_operation(db, op, blocking_safety_rules=["SAFE-PLAN-CHANGED-001"])
            try:
                acquire_lock(db, resource_type="pbs_target", resource_id=target.id, operation_id=op.id, reason="pbs.update", ttl_seconds=3600)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"])
            op = enter_stage(db, op, status="executing", stage="executing")

        if op.status == "executing" and not op.pve_task_result:
            try:
                with HostMaintenanceClient(hm) as client:
                    client.refresh()
                    final = client.plan()
                    if _packages_materially_differ(approved, final.get("packages", [])):
                        release_locks_for_operation(db, op.id)
                        return block_operation(db, op, blocking_safety_rules=["SAFE-PLAN-CHANGED-001"])
                    before = client.status()
                    log_stage(op.id, f"Plan unchanged ({len(approved)} package(s)). Starting apt dist-upgrade on {target.name}.")
                    streamer = None
                    try:
                        caps = (client.version() or {}).get("capabilities") or []
                    except Exception:  # noqa: BLE001
                        caps = []
                    if "log" in caps:
                        try:
                            baseline = client.log()
                        except Exception:  # noqa: BLE001
                            baseline = None
                        streamer = HostLogStreamer(op.id, HostMaintenanceClient, hm, baseline).start()
                    try:
                        applied = client.apply()
                    finally:
                        if streamer is not None:
                            streamer.stop()
                    log_stage(op.id, f"apt finished (exit {applied.get('exit_status')}). Verifying against the live package list.")
                    if "log_tail" in applied:
                        applied["log_tail"] = sanitize_captured_output(applied["log_tail"])
            except MutationsDisabledError as exc:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=str(exc))
            except (HostMaintenanceConnectionError, HostMaintenanceProtocolError) as exc:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"apply failed: {exc}")
            op.pve_task_result = {"apply_result": applied, "before_status": before}
            db.commit()
            op = enter_stage(db, op, status="verifying", stage="verifying")

        if op.status == "verifying":
            try:
                with HostMaintenanceClient(hm) as client:
                    after_status = client.verify()
                    after_plan = client.plan()
            except (HostMaintenanceConnectionError, HostMaintenanceProtocolError) as exc:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"post-apply verification unreachable: {exc}")
            tr = op.pve_task_result or {}
            applied, before = tr.get("apply_result", {}), tr.get("before_status", {})
            still = sorted({_package_key(p) for p in approved} & {_package_key(p) for p in after_plan.get("packages", [])})
            op.verification_result = {
                "before_kernel_version": before.get("kernel_version"), "after_kernel_version": after_status.get("kernel_version"),
                "before_upgradable_count": before.get("upgradable_count"), "after_upgradable_count": after_status.get("upgradable_count"),
                "applied_packages": approved, "apply_exit_status": applied.get("exit_status"), "apply_succeeded": bool(applied.get("success")),
                "reboot_required": bool(after_status.get("reboot_required")),
                "still_pending_after_apply": [{"package": p, "new_version": v} for p, v in still],
            }
            from .pbs_server import _set_reboot_flag
            _set_reboot_flag(db, target, bool(after_status.get("reboot_required")))
            db.commit()
            release_locks_for_operation(db, op.id)
            write_audit_event(db, event_category="protection", event_type="pbs.update", actor=op.approved_by or "system", actor_type="user",
                              metadata={"target": target.name, "packages": len(approved), "reboot_required": op.verification_result["reboot_required"]})
            if not applied.get("success"):
                return fail_operation(db, op, error=f"apply did not report success: {applied}")
            if still:
                return fail_operation(db, op, error=f"{len(still)} approved package(s) still show as upgradable after re-checking: {still}")
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE)
        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error: {exc}")
