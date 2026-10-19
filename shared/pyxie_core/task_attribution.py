"""Which PyXie operation, if any, explains a Proxmox task. Pure: the task page matches by object and time window.

A Proxmox task is only ever shown as run by the PyXie service account, so "who in PyXie asked for this" has to come from the Operation behind it:
the operation that touched the same guest (or, for a host-level task with no guest, a host-level operation on the same host) and whose own
running window contains the task's start. An operation that never ran (a preview nobody approved, a blocked or cancelled one) explains nothing,
and a guest operation never takes credit for a host task."""

from datetime import datetime, timedelta, timezone

NEVER_RAN = ("awaiting_approval", "blocked", "cancelled", "rejected")
FINISHED = ("completed", "failed")
SLACK = timedelta(minutes=1)


def match_operation(task_started_at, task_node_id, workload_id, operations, now=None):
    """operations: objects with workload_id, node_id, status, created_at, completed_at. Returns the best match or None."""
    if not task_started_at:
        return None
    now = now or datetime.now(timezone.utc)
    best, best_delta = None, None
    for op in operations:
        if op.status in NEVER_RAN:
            continue
        if workload_id:
            if op.workload_id != workload_id:
                continue
        elif op.workload_id or not task_node_id or op.node_id != task_node_id:
            continue
        end = op.completed_at or (now if op.status not in FINISHED else op.created_at + timedelta(minutes=30))
        if op.created_at - SLACK <= task_started_at <= end + SLACK:
            delta = abs((task_started_at - op.created_at).total_seconds())
            if best is None or delta < best_delta:
                best, best_delta = op, delta
    return best
