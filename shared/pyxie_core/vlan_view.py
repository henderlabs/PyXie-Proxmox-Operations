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


def build_vlan_view(topology: list[dict], nics: list[dict], sdn: dict[str, dict] | None = None) -> list[dict]:
    """One segment per (cluster, bridge, tag). Each lists its guests and the
    cluster hosts that could NOT carry it (guests there could not migrate)."""
    sdn = sdn or {}
    ifaces: dict[tuple, dict] = {}
    nodes_by_cluster: dict[str, list[str]] = {}
    for t in topology:
        cid = t["cluster_id"]
        nodes_by_cluster.setdefault(cid, []).append(t["node"])
        for i in t.get("interfaces", []):
            ifaces[(cid, t["node"], i["iface"])] = i
    # SDN networks exist on every host their zone covers (a zone with no node list covers every host); they set the VLAN themselves
    vnets: dict[tuple, dict] = {}
    for cid, info in sdn.items():
        zone_nodes = {z["zone"]: z.get("nodes") or [] for z in info.get("zones", [])}
        for v in info.get("vnets", []):
            vnets[(cid, v["vnet"])] = v
            for node in nodes_by_cluster.get(cid, []):
                zn = zone_nodes.get(v.get("zone")) or []
                if not zn or node in zn:
                    ifaces[(cid, node, v["vnet"])] = {"iface": v["vnet"], "type": "sdn", "vlan_aware": True, "allowed_vlans": None}
    segs: dict[tuple, dict] = {}
    for wl in nics:
        cid = wl["cluster_id"]
        for n in wl.get("nics", []):
            key = (cid, n.get("bridge") or "?", n.get("vlan_tag"))
            seg = segs.setdefault(key, {
                "cluster_id": cid, "cluster_name": wl.get("cluster_name"),
                "bridge": key[1], "tag": key[2], "guests": [], "problems": [], "blocked_hosts": [],
                "sdn": None, "unused": False, "note": None,
            })
            seg["guests"].append({"workload_id": wl["workload_id"], "vmid": wl["vmid"], "name": wl.get("name"),
                                  "type": wl["type"], "node": wl["node"], "net_id": n["net_id"], "status": wl["status"]})
    # networks nobody uses yet
    for (cid, name), v in vnets.items():
        segs.setdefault((cid, name, None), {"cluster_id": cid, "cluster_name": next((t.get("cluster_name") for t in topology if t["cluster_id"] == cid), None),
                                            "bridge": name, "tag": None, "guests": [], "problems": [], "blocked_hosts": [], "sdn": None, "unused": True, "note": None})
    for (cid, bridge, tag), seg in segs.items():
        v = vnets.get((cid, bridge))
        if v:
            seg["sdn"] = {"alias": v.get("alias"), "zone": v.get("zone"), "tag": v.get("tag")}
        elif seg["guests"] and not any((cid, n, bridge) in ifaces for n in nodes_by_cluster.get(cid, [])):
            # not a host bridge and not a network PyXie can see: say what that probably means instead of "bridge missing" on every host
            seg["note"] = ("This name is not a bridge on any host and PyXie cannot see it as an SDN network. If it is an SDN network, the read-only Proxmox account "
                           "probably lacks SDN.Audit (the built-in PVEAuditor role has it).")
            continue
        for node in nodes_by_cluster.get(cid, []):
            why = node_supports(ifaces.get((cid, node, bridge)), tag)
            if why:
                seg["blocked_hosts"].append({"node": node, "reason": why})
        for g in seg["guests"]:
            why = node_supports(ifaces.get((cid, g["node"], bridge)), tag)
            if why:
                label = g['name'] or g['vmid']
                seg['problems'].append(f"{label} on {g['node']}: {why}")
    def effective(s):
        return (s["sdn"] or {}).get("tag") if s["sdn"] else s["tag"]
    return sorted(segs.values(), key=lambda s: (s["cluster_name"] or "", effective(s) is not None, effective(s) or 0, s["bridge"]))
