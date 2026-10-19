"""Read-only Network page logic, pure so it is unit tested: what each host link is used for (only where it can be PROVEN), and hosts with identical
layouts grouped so a 8-host cluster reads as one table."""
from __future__ import annotations

import ipaddress


def _ip(value):
    try:
        return ipaddress.ip_address(str(value).split("/")[0])
    except ValueError:
        return None


def _in(network: str | None, address) -> bool:
    try:
        return bool(network) and address is not None and address in ipaddress.ip_network(str(network), strict=False)
    except ValueError:
        return False


def _ports(i: dict) -> list[str]:
    p = i.get("ports")
    if isinstance(p, (list, tuple)):
        return [str(x) for x in p]
    return [x for x in str(p or "").replace(",", " ").split() if x]


def proofs_for_node(interfaces: list[dict], *, mgmt_ip: str | None, migration_cidr: str | None, corosync_addrs: list[str], zone_bridges: dict[str, str],
                    guests_per_bridge: dict[str, int]) -> dict[str, list[str]]:
    """{interface name: [what it carries]} for one host. A statement is only made when something proves it:
      - the host's management address sits on the interface (or on a bridge or bond built on it),
      - the datacenter migration network contains the interface's address,
      - the interface holds one of the cluster's corosync link addresses,
      - guests are attached to it as a bridge, or it is the bridge of an SDN zone.
    A link built into a bridge or bond inherits what that bridge or bond carries. Nothing is inferred from a name. Pure."""
    by_name = {i["iface"]: i for i in interfaces}
    own: dict[str, list[str]] = {}
    mgmt = _ip(mgmt_ip)
    cs = {str(_ip(a)) for a in corosync_addrs if _ip(a) is not None}
    for i in interfaces:
        name, addr, found = i["iface"], _ip(i.get("address")), []
        if addr is not None:
            if mgmt is not None and addr == mgmt:
                found.append("host management address")
            if _in(migration_cidr, addr):
                found.append(f"cluster migration network {migration_cidr}")
            if str(addr) in cs:
                found.append("cluster (corosync) link")
        if name in zone_bridges:
            found.append(f"guest networks (SDN zone {zone_bridges[name]})")
        elif guests_per_bridge.get(name):
            found.append("guests attached")
        own[name] = found
    out: dict[str, list[str]] = {}

    def resolve(name: str, seen: set) -> list[str]:
        if name in out:
            return out[name]
        res = list(own.get(name, []))
        out[name] = res  # cycle guard
        for parent in by_name.values():
            if name in _ports(parent) and parent["iface"] not in seen:
                for p in resolve(parent["iface"], seen | {name}):
                    if p not in res:
                        res.append(p)
        return res

    for n in by_name:
        resolve(n, set())
    return out


def layout_groups(topology: list[dict], facts: dict | None = None, nics: list[dict] | None = None, sdn: dict | None = None) -> list[dict]:
    """Group a cluster's hosts whose network layout (links, bonds, bridges and what each is proven to carry) is identical. Returns, for one cluster's
    topology: [{nodes, interfaces: [{iface, type, ports, active, vlan_aware, carries}], is_main}], largest group first; a host that could not be read
    is its own group with `error`. `facts`: {migration_cidr, corosync: {node: [addr]}, mgmt: {node: ip}}. `nics`: guest NICs ({node, nics: [{bridge}]}). `sdn`: read_sdn(). Pure."""
    facts = facts or {}
    zone_bridges = {z["bridge"]: z["zone"] for z in (sdn or {}).get("zones", []) if z.get("bridge")}
    per_bridge: dict[str, dict[str, int]] = {}
    for w in nics or []:
        for n in w.get("nics", []):
            if n.get("bridge"):
                per_bridge.setdefault(w["node"], {}).setdefault(n["bridge"], 0)
                per_bridge[w["node"]][n["bridge"]] += 1
    groups: dict[tuple, dict] = {}
    errors: list[dict] = []
    for t in topology:
        if t.get("error"):
            errors.append({"nodes": [t["node"]], "error": t["error"], "interfaces": [], "is_main": False})
            continue
        carries = proofs_for_node(t["interfaces"], mgmt_ip=(facts.get("mgmt") or {}).get(t["node"]), migration_cidr=facts.get("migration_cidr"),
                                  corosync_addrs=(facts.get("corosync") or {}).get(t["node"], []), zone_bridges=zone_bridges, guests_per_bridge=per_bridge.get(t["node"], {}))
        ifaces = [{"iface": i["iface"], "type": i.get("type"), "ports": _ports(i), "active": bool(i.get("active", True)), "vlan_aware": bool(i.get("vlan_aware")),
                   "carries": carries.get(i["iface"], [])} for i in sorted(t["interfaces"], key=lambda x: x["iface"])]
        # guest counts differ per host and say nothing about the layout: leave them out of the signature
        sig = tuple((i["iface"], i["type"], tuple(i["ports"]), i["active"], tuple(c for c in i["carries"] if c != "guests attached")) for i in ifaces)
        g = groups.setdefault(sig, {"nodes": [], "interfaces": ifaces})
        g["nodes"].append(t["node"])
    out = sorted(groups.values(), key=lambda g: (-len(g["nodes"]), g["nodes"][0]))
    for n, g in enumerate(out):
        g["nodes"].sort()
        g["is_main"] = n == 0
    return out + errors
