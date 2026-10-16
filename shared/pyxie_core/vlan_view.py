"""Read-only VLAN view: groups guest NICs by (bridge, VLAN tag) and flags
what would go wrong. Pure functions so they are unit tested; the router
feeds them the topology and NIC lists the network page already loads."""
from __future__ import annotations


def parse_vids(spec: str | None) -> list[tuple[int, int]] | None:
    """"2-4094" or "10 20 30-40" -> ranges. None means no allow-list (any)."""
    if not spec:
        return None
    out = []
    for part in str(spec).replace(",", " ").split():
        lo, _, hi = part.partition("-")
        if lo.isdigit():
            out.append((int(lo), int(hi) if hi.isdigit() else int(lo)))
    return out or None


def tag_allowed(spec: str | None, tag: int) -> bool:
    ranges = parse_vids(spec)
    return True if ranges is None else any(lo <= tag <= hi for lo, hi in ranges)


def node_supports(iface: dict | None, tag: int | None) -> str | None:
    """None if this host bridge can carry the tag, else the reason."""
    if iface is None:
        return "bridge missing"
    if tag is None:
        return None
    if not iface.get("vlan_aware"):
        return "bridge is not VLAN-aware"
    if not tag_allowed(iface.get("allowed_vlans"), tag):
        return "VLAN not allowed on bridge"
    return None


def build_vlan_view(topology: list[dict], nics: list[dict]) -> list[dict]:
    """One segment per (cluster, bridge, tag). Each lists its guests and the
    cluster hosts that could NOT carry it (guests there could not migrate)."""
    ifaces: dict[tuple, dict] = {}
    nodes_by_cluster: dict[str, list[str]] = {}
    for t in topology:
        cid = t["cluster_id"]
        nodes_by_cluster.setdefault(cid, []).append(t["node"])
        for i in t.get("interfaces", []):
            ifaces[(cid, t["node"], i["iface"])] = i
    segs: dict[tuple, dict] = {}
    for wl in nics:
        cid = wl["cluster_id"]
        for n in wl.get("nics", []):
            key = (cid, n.get("bridge") or "?", n.get("vlan_tag"))
            seg = segs.setdefault(key, {
                "cluster_id": cid, "cluster_name": wl.get("cluster_name"),
                "bridge": key[1], "tag": key[2], "guests": [], "problems": [], "blocked_hosts": [],
            })
            seg["guests"].append({"workload_id": wl["workload_id"], "vmid": wl["vmid"], "name": wl.get("name"),
                                  "type": wl["type"], "node": wl["node"], "net_id": n["net_id"], "status": wl["status"]})
    for (cid, bridge, tag), seg in segs.items():
        for node in nodes_by_cluster.get(cid, []):
            why = node_supports(ifaces.get((cid, node, bridge)), tag)
            if why:
                seg["blocked_hosts"].append({"node": node, "reason": why})
        for g in seg["guests"]:
            why = node_supports(ifaces.get((cid, g["node"], bridge)), tag)
            if why:
                label = g['name'] or g['vmid']
                seg['problems'].append(f"{label} on {g['node']}: {why}")
    return sorted(segs.values(), key=lambda s: (s["cluster_name"] or "", s["tag"] is not None, s["tag"] or 0, s["bridge"]))
