"""Is a host still a sensible place to put a new guest? Used again just before a create or clone runs, because approval can come hours after the preview."""

from .models import Node


def node_not_ready(db, cluster_id, node_name: str) -> str | None:
    """Why this host cannot take a new guest right now, or None. Reads PyXie's latest inventory (refreshed every minute)."""
    n = db.query(Node).filter(Node.cluster_id == cluster_id, Node.name == node_name).one_or_none()
    if n is None or n.is_missing:
        return f"{node_name} is no longer part of the cluster"
    if n.status != "online":
        return f"{node_name} is {n.status}"
    if n.maintenance_mode:
        return f"{node_name} is in maintenance mode"
    return None
