"""pbs.reboot: restart a Proxmox Backup Server through its own API (the same call Proxmox VE uses for a node).

Safety Contract: preview (PBS idle: no PBS task running and no Proxmox backup running; the administrative token may
reboot it) -> approve -> revalidate (still idle) -> lock -> send the reboot -> wait for the server to answer again with
a smaller uptime -> verify (answers, the kernel now running is the newest installed, datastores listed) -> audit.
Backups that would start while it is down fail and run again at their next schedule: pick a quiet moment.
"""

import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import rollback
from .audit import write_audit_event
from .host_maintenance_client import MutationsDisabledError
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Operation, ProtectionTarget
from .operation_log import log_stage
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pbs_client import PbsApiError, PbsClient, PbsWriteClient
from .pbs_server import PbsNotConfigured, busy_reasons, load_pbs_credentials, refresh_status

now = lambda: datetime.now(timezone.utc)
WAIT_SECONDS = 1200
POLL_SECONDS = 10


class PbsRebootError(Exception):
    pass


def _read_client(db: Session, target: ProtectionTarget) -> PbsClient:
    try:
        return PbsClient(load_pbs_credentials(db, target, "administrative"))
    except PbsNotConfigured:
        return PbsClient(load_pbs_credentials(db, target, "inventory"))


def dry_run_pbs_reboot(db: Session, target: ProtectionTarget, *, actor: str, parent_operation_id=None) -> Operation:
    op = create_operation(db, "pbs.reboot", parent_operation_id=parent_operation_id, created_by=actor,
                          context={"protection_target_id": str(target.id), "target": target.name, "node": target.name})
    reasons: list[str] = []
    rules: list[str] = []
    info: dict = {}
    try:
        load_pbs_credentials(db, target, "administrative")
    except PbsNotConfigured:
        reasons.append("no write (administrative) API token is saved for this PBS: add one under Credentials")
        rules.append("SAFE-PBSJOB-002")
    if not rules:
        try:
            with _read_client(db, target) as c:
                st = c.node_status() or {}
            info = {"uptime": st.get("uptime"), "kernel": (st.get("current-kernel") or {}).get("release") if isinstance(st.get("current-kernel"), dict) else None}
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"could not read the PBS server: {exc}")
            rules.append("SAFE-PBSJOB-002")
    if not rules:
        try:
            busy = busy_reasons(db, target)
        except Exception as exc:  # noqa: BLE001
            busy = [f"could not check what PBS and the Proxmox hosts are doing: {exc}"]
        if busy:
            reasons.extend(busy)
            rules.append("SAFE-PBS-001")
    if not rules:
        reasons.append(f"Reboot {target.name} now. It is offline for about 2 to 3 minutes; backups that start in that time fail and run again at their next schedule.")
    result = {"target": target.name, "node": target.name, "eligible": not rules, "reasons": reasons, "blocking_safety_rules": rules, **info}
    ctx = {"protection_target_id": str(target.id), "target": target.name, "node": target.name}
    if rules:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=rules, context=ctx, actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, context=ctx, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_pbs_reboot(db: Session, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise PbsRebootError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    ctx = op.context or {}
    target = db.query(ProtectionTarget).filter(ProtectionTarget.id == ctx["protection_target_id"]).one()
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            log_stage(op.id, f"Approved. Re-checking {target.name}: nothing running on it or backing up to it.")
            try:
                if busy_reasons(db, target):
                    return block_operation(db, op, blocking_safety_rules=["SAFE-PBS-001"])
            except Exception:  # noqa: BLE001
                return block_operation(db, op, blocking_safety_rules=["SAFE-PBS-001"])
            try:
                acquire_lock(db, resource_type="pbs_target", resource_id=target.id, operation_id=op.id, reason="pbs.reboot", ttl_seconds=2400)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"])
            op = enter_stage(db, op, status="executing", stage="executing")

        if op.status == "executing":
            result = dict(op.pve_task_result or {})
            if "sent_at" not in result:
                try:
                    with PbsWriteClient(load_pbs_credentials(db, target, "administrative")) as w:
                        result["uptime_before"] = (w.node_status() or {}).get("uptime")
                        log_stage(op.id, f"Sending the reboot to {target.name}.")
                        try:
                            w.reboot()
                        except Exception as exc:  # noqa: BLE001 -- the server may drop the connection as it goes down
                            from .pve_client import PveConnectionError
                            if not isinstance(exc, PveConnectionError):
                                raise
                except MutationsDisabledError as exc:
                    release_locks_for_operation(db, op.id)
                    return fail_operation(db, op, error=str(exc))
                except (PbsApiError, Exception) as exc:  # noqa: BLE001
                    release_locks_for_operation(db, op.id)
                    return fail_operation(db, op, error=f"PBS refused the reboot: {exc}")
                result["sent_at"] = now().isoformat()
                op.pve_task_result = result
                db.commit()
            before = result.get("uptime_before") or 10 ** 9
            sent = datetime.fromisoformat(result["sent_at"])
            log_stage(op.id, "Waiting for the server to come back.")
            deadline = time.time() + WAIT_SECONDS
            back = False
            time.sleep(POLL_SECONDS)
            while time.time() < deadline:
                try:
                    with _read_client(db, target) as c:
                        up = (c.node_status() or {}).get("uptime")
                    # a smaller uptime than before the reboot (or than the time since we sent it) means it restarted
                    if up is not None and (up < before or up < (now() - sent).total_seconds()):
                        back = True
                        break
                except Exception:  # noqa: BLE001 -- unreachable while it restarts
                    pass
                time.sleep(POLL_SECONDS)
            if not back:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"{target.name} did not answer again within {WAIT_SECONDS // 60} minutes after the reboot was sent")
            op = enter_stage(db, op, status="verifying", stage="verifying")

        if op.status == "verifying":
            release_locks_for_operation(db, op.id)
            st = refresh_status(db, target)
            problems = []
            if not st.get("reachable"):
                problems.append("server not reachable after the reboot")
            if st.get("reboot_required"):
                problems.append("it still reports that a reboot is needed (the running kernel is not the newest installed)")
            if not st.get("datastores"):
                problems.append("no datastores listed after the reboot")
            op.verification_result = {"version": st.get("version"), "kernel": st.get("kernel"), "reboot_required": st.get("reboot_required"),
                                      "datastores": len(st.get("datastores") or []), "problems": problems}
            db.commit()
            write_audit_event(db, event_category="protection", event_type="pbs.reboot", actor=op.approved_by or "system", actor_type="user",
                              metadata={"target": target.name, "kernel": st.get("kernel"), "problems": problems})
            if problems:
                return fail_operation(db, op, error="; ".join(problems))
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.IRREVERSIBLE)
        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error: {exc}")
