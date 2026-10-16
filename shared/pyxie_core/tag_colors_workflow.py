"""Tag colors (cluster.tag_colors): set or clear tag colors in Proxmox's datacenter `tag-style` option.

The option is shared by the whole Proxmox datacenter (every guest carrying the tag, and Proxmox's own web UI, show the new color), so this
is a preview-and-approve change like the others: dry-run (which tags change, old -> new) -> approve -> revalidate -> lock -> write -> read the option
back -> verify -> audit. The write re-reads the option and applies only the requested tags on top of it, so colors someone set in
Proxmox in the meantime are kept.
"""

from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Operation, PveTarget
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_permissions import friendly_error, missing_privileges, permission_reason
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient
from .tag_colors import TagColorError, _find, apply_changes, build_tag_style, norm_hex, parse_tag_style
from .tags_workflow import clean_tags
from . import rollback


class TagColorsError(Exception):
    pass


def _target(db, cluster: Cluster) -> PveTarget:
    return db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()


def read_style(db, target) -> tuple[dict, object]:
    client, _cred = build_pve_client(db, target)
    with client:
        raw = (client._get("/cluster/options") or {}).get("tag-style")
    return parse_tag_style(raw), raw


def _clean_changes(changes: dict) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for tag, value in (changes or {}).items():
        cleaned = clean_tags([tag])
        if not cleaned:
            continue  # a blank tag name
        t = cleaned[0]
        if value in (None, ""):
            out[t] = None
        elif norm_hex(value):
            out[t] = norm_hex(value)
        else:
            raise TagColorsError(f"'{value}' is not a color: use six hex digits such as 3366CC")
    if not out:
        raise TagColorsError("no tag color to change")
    return out


def dry_run_tag_colors(db, cluster: Cluster, *, changes: dict, actor: str, correlation_id=None) -> Operation:
    try:
        wanted = _clean_changes(changes)
    except Exception as exc:  # noqa: BLE001 -- TagsError from clean_tags too
        raise TagColorsError(str(exc))
    target = _target(db, cluster)
    op = create_operation(db, "cluster.tag_colors", cluster_id=cluster.id, correlation_id=correlation_id,
                          context={"cluster": cluster.name, "changes": wanted}, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    current: dict = {}
    try:
        style, _raw = read_style(db, target)
        current = style["colors"]
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"could not read the tag style from Proxmox: {str(exc)[:140]}")
        blocking.append("SAFE-STORAGE-002")
        style = {"colors": {}}
    lacking = missing_privileges(db, target, 0, ["Sys.Modify"], path="/")
    if lacking:
        reasons.append(permission_reason(lacking, "change the datacenter's tag colors"))
        blocking.append("SAFE-PERMISSION-001")
    rows = []
    for tag, new in wanted.items():
        old = current.get(_find(current, tag) or tag, (None, None))[0]
        if (old or None) == (new or None):
            continue
        rows.append({"tag": tag, "old": old, "new": new})
        reasons.append(f"{tag}: " + (f"#{old}" if old else "automatic color") + " -> " + (f"#{new}" if new else "automatic color"))
    if not blocking and not rows:
        reasons.append("those tags already have exactly these colors")
        blocking.append("SAFE-TAGS-001")
    if rows:
        reasons.append(f"this is a datacenter setting: guests carrying these tags show the new color in Proxmox's own web UI as well, on every node of cluster {cluster.name}")
    dry_run_result = {"cluster": cluster.name, "changes": rows, "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": blocking,
                      "workload_name": None, "node": None}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=dry_run_result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=dry_run_result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_tag_colors(db, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise TagColorsError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    cluster = db.query(Cluster).filter(Cluster.id == op.cluster_id).one()
    target = _target(db, cluster)
    wanted = dict((op.context or {}).get("changes") or {})
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            try:
                acquire_lock(db, resource_type="cluster", resource_id=cluster.id, operation_id=op.id, reason="cluster.tag_colors")
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")
        if op.status == "executing":
            style, _raw = read_style(db, target)  # fresh: keep whatever else changed in Proxmox since the preview
            new_style = apply_changes(style, wanted)
            text = build_tag_style(new_style)
            with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as write_client:
                write_client.set_tag_style(text)
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            style, raw = read_style(db, target)
            bad = [t for t, v in wanted.items() if (style["colors"].get(_find(style["colors"], t) or t, (None,))[0]) != v]
            op.verification_result = {"tag_style": raw if isinstance(raw, (str, dict)) else str(raw), "colors": {t: c[0] for t, c in style["colors"].items()}}
            db.commit()
            release_locks_for_operation(db, op.id)
            if bad:
                return fail_operation(db, op, error=f"read-back from Proxmox did not match for: {', '.join(bad)}")
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE)
        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")
    except (MutationsDisabledError, TagColorError) as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=friendly_error(exc) if "Permission check failed" in str(exc) else f"unexpected error: {exc}")
