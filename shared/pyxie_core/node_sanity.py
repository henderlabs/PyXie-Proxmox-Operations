"""Does a node's live telemetry look real? An online Proxmox host always uses some memory (the host itself) and a little CPU: a reading
of exactly 0, or no reading at all while the node is online, means the numbers are not arriving (a stuck status daemon, a broken poll),
not that the host is idle. Balancing on such a number would send load to a node that may be anything but empty, so checks that act on
node load look here first, and Health shows a finding."""

from sqlalchemy.orm import Session

from .models import MetricPoint, Node

ZERO_SAMPLES = 3  # this many consecutive exact-zero samples (about as many minutes) before the stored history counts as proof


def live_problem(node) -> str | None:
    """From the node's latest values only. Pure."""
    if node.status != "online":
        return None
    if node.cpu_usage_pct is None or node.mem_usage_pct is None:
        return "online but reporting no CPU or memory reading"
    if float(node.mem_usage_pct) == 0.0:
        return "online but its memory reads exactly 0%"
    # An exact 0% CPU in a single live reading is NOT proof: Proxmox's status report occasionally returns 0 for a busy host for one poll
    # (seen live: st-pve104 flickering 0 -> 5 while its per-minute history stayed at 4-6%). Discovery ignores such a reading; only
    # a zero that persists in the stored history (node_problem below) counts.
    return None


def _all_zero(db: Session, node_id, metric: str) -> bool:
    rows = (
        db.query(MetricPoint.value).filter(MetricPoint.object_type == "node", MetricPoint.object_id == node_id, MetricPoint.metric == metric)
        .order_by(MetricPoint.sampled_at.desc()).limit(ZERO_SAMPLES).all()
    )
    return len(rows) == ZERO_SAMPLES and all(v is not None and float(v) == 0.0 for (v,) in rows)


def node_problem(db: Session, node) -> str | None:
    p = live_problem(node)
    if p:
        return p
    if node.status == "online" and (_all_zero(db, node.id, "cpu_pct") or _all_zero(db, node.id, "mem_pct")):
        return f"online but its stored CPU or memory history is exactly 0% for the last {ZERO_SAMPLES} samples"
    return None


def suspect_nodes(db: Session, nodes) -> list[dict]:
    out = []
    for n in nodes:
        p = node_problem(db, n)
        if p:
            out.append({"name": n.name, "problem": p})
    return out
