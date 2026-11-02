"""Remove ("forget") a host from PyXie once PVE no longer lists it (decommissioned, `pvecm delnode` run).

App-side only: it never touches the host or the cluster. Nothing is deleted: the node row is marked forgotten so history,
operations and the audit log keep their links. Discovery clears the mark if PVE lists the host again.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import Node, Storage


def forget_block_reason(node: Node) -> str | None:
    """Why this host cannot be removed, or None. A host PVE still lists is never removable here. Pure."""
    if node.forgotten_at is not None:
        return "this host was already removed from PyXie"
    if not node.is_missing:
        return ("PVE still lists this host as a cluster member. Remove it from the cluster first (run `pvecm delnode "
                f"{node.name}` on a remaining node once it is powered off and the cluster has quorum), then come back.")
    return None


def forget_node(db: Session, node: Node) -> dict:
    """Mark the node forgotten and drop the app-side records tied to it. Caller audits and commits. Returns what was dropped."""
    fingerprint = node.ssh_host_key_fingerprint
    had_wrapper = node.wrapper_version
    node.forgotten_at = datetime.now(timezone.utc)
    node.ssh_host_key_type = None
    node.ssh_host_key_base64 = None
    node.ssh_host_key_fingerprint = None
    node.ssh_host_key_pinned_at = None
    node.ssh_host_key_pinned_by = None
    node.wrapper_version = None
    node.wrapper_checked_at = None
    stores = db.query(Storage).filter(Storage.node_id == node.id, Storage.scope == "node-local", Storage.is_missing.is_(False)).all()
    for st in stores:
        st.is_missing = True
    return {"forgotten_ssh_fingerprint": fingerprint, "wrapper_version": had_wrapper, "local_storage_dropped": [st.name for st in stores]}
