"""Tags (workload.tags_change): set the Proxmox tags on a VM or container.

Tags are the labels PyXie's affinity rules, pins, reports and tag-based defaults key on, and Proxmox shows them too. A tag change is a
synchronous config write (no Proxmox task) that takes effect at once, also on a running guest. Same Safety Contract as the other
operations: dry-run (what is added and removed) -> approve -> revalidate -> lock -> execute -> read the tags back -> verify -> audit.
"""

import re

from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .labels import vm_label
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Node, Operation, PveTarget, Workload
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient
from . import rollback
from .pve_permissions import friendly_error, missing_privileges, permission_reason

TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-+.]*$")  # Proxmox tag syntax
MAX_TAG_LEN = 40


class TagsError(Exception):
    pass


def split_tags(raw) -> list[str]:
    """Proxmox stores tags as one string separated by ; (older versions , or space). Their spelling is kept: Proxmox treats 'AI' and 'ai'
    as the same tag but shows (and colors) the spelling it was given. Pure."""
    return [t.strip() for t in re.split(r"[;, ]", str(raw or "")) if t.strip()]


def clean_tags(tags: list[str]) -> list[str]:
    """De-duplicated (case-insensitively, first spelling wins), order kept. Raises TagsError on a tag Proxmox would refuse. Pure."""
    out: list[str] = []
    for t in tags or []:
        t = str(t).strip()
        if not t:
            continue
        if len(t) > MAX_TAG_LEN or not TAG_RE.match(t):
            raise TagsError(f"'{t}' is not a valid tag: use letters, digits, - _ + . and at most {MAX_TAG_LEN} characters, no spaces or semicolons")
        if t.lower() not in [o.lower() for o in out]:
            out.append(t)
    return out


def diff_tags(old: list[str], new: list[str]) -> tuple[list[str], list[str]]:
    """(added, removed), comparing case-insensitively. Pure."""
    lo, ln = {t.lower() for t in old}, {t.lower() for t in new}
    return [t for t in new if t.lower() not in lo], [t for t in old if t.lower() not in ln]


def _load_context(db, workload: Workload):
    node = db.query(Node).filter(Node.id == workload.node_id).one()
    cluster = db.query(Cluster).filter(Cluster.id == workload.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    return node, cluster, target


def _read_config(client, node_name: str, workload: Workload) -> dict:
    return (client.qemu_config(node_name, workload.vmid) if workload.type == "vm" else client.lxc_config(node_name, workload.vmid)) or {}


def dry_run_tags(db, workload: Workload, *, tags: list[str], actor: str, correlation_id=None) -> Operation:
    wanted = clean_tags(tags)
    node, cluster, target = _load_context(db, workload)
    op = create_operation(db, "workload.tags_change", cluster_id=cluster.id, node_id=node.id, workload_id=workload.id, correlation_id=correlation_id,
                          context={"node_id": str(node.id), "node": node.name, "vmid": workload.vmid, "tags": wanted}, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    current: list[str] = []
    try:
        client, _cred = build_pve_client(db, target)
        with client:
            config = _read_config(client, node.name, workload)
        current = split_tags(config.get("tags"))
        if config.get("lock"):
            reasons.append(f"Proxmox has the guest locked ('{config.get('lock')}'): another task is working on it")
            blocking.append("SAFE-LOCK-001")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"could not read {vm_label(workload)} from Proxmox: {str(exc)[:140]}")
        blocking.append("SAFE-STORAGE-002")
    lacking = missing_privileges(db, target, workload.vmid, ["VM.Config.Options"])
    if lacking:
        reasons.append(permission_reason(lacking, "change tags"))
        blocking.append("SAFE-PERMISSION-001")
    added, removed = diff_tags(current, wanted)
    if not blocking and not added and not removed:
        reasons.append("the tags are already exactly this")
        blocking.append("SAFE-TAGS-001")
    if added:
        reasons.append("added: " + ", ".join(added))
    if removed:
        reasons.append("removed: " + ", ".join(removed))
    if removed or added:
        reasons.append("PyXie's affinity rules, pins and reports that use a tag follow the change at the next check")
    dry_run_result = {"vmid": workload.vmid, "workload_name": workload.name, "node": node.name, "current": current, "wanted": wanted,
                      "added": added, "removed": removed, "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": blocking}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=dry_run_result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=dry_run_result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_tags_change(db, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise TagsError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    workload = db.query(Workload).filter(Workload.id == op.workload_id).one()
    node, cluster, target = _load_context(db, workload)
    wanted = list((op.context or {}).get("tags") or [])
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            read_client, _cred = build_pve_client(db, target)
            with read_client:
                config = _read_config(read_client, node.name, workload)
            if config.get("lock"):
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"revalidation failed: the guest is locked by Proxmox ('{config.get('lock')}')")
            seen = list((op.dry_run_result or {}).get("current") or [])
            live_tags = split_tags(config.get("tags"))
            if sorted(t.lower() for t in live_tags) != sorted(t.lower() for t in seen):
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"revalidation failed: the tags changed in Proxmox since the preview (now: {', '.join(live_tags) or 'none'}); preview again so you can see what would change")
            try:
                acquire_lock(db, resource_type="workload", resource_id=workload.id, operation_id=op.id, reason="workload.tags_change")
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")
        if op.status == "executing":
            with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as write_client:
                write_client.set_vm_tags(node.name, workload.vmid, wanted, guest_type=workload.type)
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            read_client, _cred = build_pve_client(db, target)
            with read_client:
                now = split_tags(_read_config(read_client, node.name, workload).get("tags"))
            op.verification_result = {"tags": now}
            db.commit()
            release_locks_for_operation(db, op.id)
            if sorted(t.lower() for t in now) != sorted(t.lower() for t in wanted):
                return fail_operation(db, op, error=f"read-back after the change did not match: Proxmox has [{', '.join(now)}], expected [{', '.join(wanted)}]")
            workload.tags = now
            db.commit()
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE)
        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")
    except MutationsDisabledError as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=friendly_error(exc) if "Permission check failed" in str(exc) else f"unexpected error: {exc}")
