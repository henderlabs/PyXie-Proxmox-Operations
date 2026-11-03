"""Plan rows for STOPPED guests whose disks are on shared storage.

They never need to move (a stopped guest costs the host nothing, and its disks are reachable from every node), but a host that
is leaving the cluster still has them registered. They used to be listed as "no action needed" with no control. Now every one
is an opt-in relocation row with a destination: an offline migration that only re-registers the config (nothing is copied,
so no storage is chosen). `included` stays False, i.e. today's behavior, unless the operator ticks it.
"""

from sqlalchemy.orm import Session

from .maintenance import _qemu_config, flag_onboot
from .models import Node
from .placement import rank_with_simulated_load, recommend_destinations


def stopped_shared_item(db: Session, client, node: Node, wl, candidates_all, nodes_by_id, simulated_added_bytes, reason: str) -> dict:
    ranked = recommend_destinations(db, client, wl, candidates_all, simulated_added_bytes=simulated_added_bytes)
    ranked = rank_with_simulated_load(ranked, nodes_by_id, simulated_added_bytes)
    top = next((c for c in ranked if not c.blocked), None) or (ranked[0] if ranked else None)
    item = {
        "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
        "reasons": [reason], "included": False, "currently_running": False,
        "currently_on_shared": True, "transport": "offline",
    }
    if top is not None:
        dest = nodes_by_id.get(top.node_id)
        item.update({
            "destination_node_id": str(top.node_id), "destination_node": dest.name if dest else top.node_name,
            "destination_storage_id": None,  # shared disks stay where they are: config-only move
            "candidates": [
                {
                    "node_id": str(c.node_id), "node_name": c.node_name, "score": c.score, "projected_mem_pct": c.projected_pct,
                    "blocked": c.blocked, "blocking_reasons": c.blocking_reasons, "reasons": c.reasons,
                }
                for c in ranked
            ],
        })
    return flag_onboot(item, _qemu_config(client, node.name, wl))
