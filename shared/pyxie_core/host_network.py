"""Host network interfaces PyXie may edit from a host's Config tab (bridges and bonds): validation, diffing and risk. Pure.

Two-phase model, matching Proxmox: an edit is STAGED (written to /etc/network/interfaces.new only; the running network is untouched), and a
separate RELOAD applies the staged file. Physical NICs, Open vSwitch and anything unknown are read-only. Creating or deleting interfaces is not
offered. Values are validated before a preview exists.
"""

import ipaddress
import re

EDITABLE_TYPES = {"bridge", "bond", "vlan"}
BOND_MODES = ["balance-rr", "active-backup", "balance-xor", "broadcast", "802.3ad", "balance-tlb", "balance-alb"]
HASH_POLICIES = ["layer2", "layer2+3", "layer3+4"]
IFACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,14}$")
VIDS_RE = re.compile(r"^\d{1,4}(-\d{1,4})?( \d{1,4}(-\d{1,4})?)*$")
PRIV = "Sys.Modify"

# key -> spec. types: which interface types accept it. kind: how the value is checked.
SPECS = {
    "cidr": dict(label="Address (CIDR)", kind="cidr", types={"bridge", "bond", "vlan"}, hint="e.g. 192.0.2.11/24; empty removes the address"),
    "gateway": dict(label="Gateway", kind="ip", types={"bridge", "bond", "vlan"}, hint="must sit inside the address's network"),
    "mtu": dict(label="MTU", kind="int", min=576, max=65520, types={"bridge", "bond", "vlan"}, hint="empty = default (1500)"),
    "comments": dict(label="Comment", kind="text", max=250, types={"bridge", "bond", "vlan"}),
    "autostart": dict(label="Start at boot", kind="bool", types={"bridge", "bond", "vlan"}),
    "bridge_ports": dict(label="Bridge ports", kind="ports", types={"bridge"}, hint="space-separated interface names"),
    "bridge_vlan_aware": dict(label="VLAN aware", kind="bool", types={"bridge"}),
    "bridge_vids": dict(label="Allowed VLANs", kind="vids", types={"bridge"}, hint="e.g. 2-4094 or 2 4 100-200"),
    "slaves": dict(label="Bond slaves", kind="ports", types={"bond"}, hint="space-separated interface names"),
    "bond_mode": dict(label="Bond mode", kind="enum", options=BOND_MODES, types={"bond"}),
    "bond_xmit_hash_policy": dict(label="Transmit hash policy", kind="enum", options=HASH_POLICIES, types={"bond"}),
    "bond-primary": dict(label="Primary slave", kind="iface", types={"bond"}, hint="for active-backup"),
}
# Proxmox's own key for the address is split in two, and the listing reports it as address + netmask + cidr.
KEY_ORDER = list(SPECS)


class NetworkConfigError(ValueError):
    pass


def keys_for(iface_type: str) -> list[str]:
    return [k for k in KEY_ORDER if iface_type in SPECS[k]["types"]]


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def current_value(row: dict, key: str):
    """What the listing says an interface has for one key, as the string the form shows."""
    if key == "bridge_vlan_aware" or key == "autostart":
        return "1" if _truthy(row.get(key, 0)) else "0"
    v = row.get(key)
    if v is None or v == "":
        return None
    return str(v)


def _ports_list(v) -> list[str]:
    return [p for p in re.split(r"[\s,]+", str(v or "").strip()) if p]


def normalize(key: str, value, iface_type: str, names: set[str]):
    """Validate one value; return what Proxmox stores, or None to remove the setting. Raises NetworkConfigError."""
    spec = SPECS[key]
    if iface_type not in spec["types"]:
        raise NetworkConfigError(f"{spec['label']} does not apply to a {iface_type}")
    k = spec["kind"]
    if k == "bool":
        return "1" if _truthy(value) else "0"
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    v = str(value).strip()
    if k == "cidr":
        try:
            n = ipaddress.ip_interface(v)
        except ValueError:
            raise NetworkConfigError("not an address with a prefix (192.168.1.5/24)")
        if n.version != 4 or "/" not in v:
            raise NetworkConfigError("an IPv4 address with a prefix is required (192.168.1.5/24)")
        if n.ip == n.network.network_address and n.network.prefixlen < 31:
            raise NetworkConfigError("that is the network address, not a host address")
        return str(n)
    if k == "ip":
        try:
            a = ipaddress.ip_address(v)
        except ValueError:
            raise NetworkConfigError("not an IP address")
        if a.version != 4:
            raise NetworkConfigError("an IPv4 gateway is required")
        return str(a)
    if k == "int":
        try:
            n = int(v)
        except ValueError:
            raise NetworkConfigError("must be a whole number")
        if n < spec["min"] or n > spec["max"]:
            raise NetworkConfigError(f"must be between {spec['min']} and {spec['max']}")
        return str(n)
    if k == "text":
        if len(v) > spec["max"] or "\n" in v or "\r" in v:
            raise NetworkConfigError(f"one line, up to {spec['max']} characters")
        return v
    if k == "enum":
        if v not in spec["options"]:
            raise NetworkConfigError("choose one of: " + ", ".join(spec["options"]))
        return v
    if k == "vids":
        if not VIDS_RE.match(v):
            raise NetworkConfigError("VLAN IDs like 2-4094 or 2 4 100-200")
        for tok in v.split():
            lo, _s, hi = tok.partition("-")
            for n in (lo, hi):
                if n and not 1 <= int(n) <= 4094:
                    raise NetworkConfigError("VLAN IDs run from 1 to 4094")
            if hi and int(lo) > int(hi):
                raise NetworkConfigError(f"{tok} runs backwards")
        return v
    if k == "iface":
        if v not in names:
            raise NetworkConfigError(f"{v} is not an interface on this host")
        return v
    if k == "ports":
        ports = _ports_list(v)
        if not ports:
            return None
        if len(set(ports)) != len(ports):
            raise NetworkConfigError("an interface is listed twice")
        for p in ports:
            if not IFACE_RE.match(p) or p not in names:
                raise NetworkConfigError(f"{p} is not an interface on this host")
        return " ".join(ports)
    raise NetworkConfigError("unsupported setting")


def _cross_checks(row: dict, after: dict, interfaces: list[dict]) -> dict[str, str]:
    """Problems that only show once the whole requested interface is known. {key: message}."""
    errors: dict[str, str] = {}
    cidr, gw = after.get("cidr"), after.get("gateway")
    if gw and not cidr:
        errors["gateway"] = "a gateway needs an address on the same interface"
    elif gw and cidr:
        try:
            if ipaddress.ip_address(gw) not in ipaddress.ip_interface(cidr).network:
                errors["gateway"] = f"{gw} is outside {ipaddress.ip_interface(cidr).network}"
        except ValueError:
            pass
    name, typ = row["iface"], row.get("type")
    key = "bridge_ports" if typ == "bridge" else "slaves" if typ == "bond" else None
    if key:
        members = _ports_list(after.get(key))
        if name in members:
            errors[key] = "an interface cannot be its own member"
        taken = {}
        for other in interfaces:
            if other["iface"] == name:
                continue
            for k2 in ("bridge_ports", "slaves"):
                for p in _ports_list(other.get(k2)):
                    taken[p] = other["iface"]
        for p in members:
            if p in taken and key not in errors:
                errors[key] = f"{p} is already a member of {taken[p]}"
            by = next((i for i in interfaces if i["iface"] == p), None)
            if by is not None and by.get("type") in ("bridge", "OVSBridge") and key not in errors:
                errors[key] = f"{p} is a bridge and cannot be a member"
        if key == "slaves" and 0 < len(members) < 2:
            errors[key] = "a bond needs at least two slaves"
    if typ == "bond" and after.get("bond-primary") and after["bond-primary"] not in _ports_list(after.get("slaves")):
        errors["bond-primary"] = "the primary must be one of the bond's slaves"
    if typ == "bridge" and after.get("bridge_vids") and not _truthy(after.get("bridge_vlan_aware", 0)):
        errors["bridge_vids"] = "allowed VLANs only apply to a VLAN-aware bridge"
    return errors


def plan_changes(row: dict, requested: dict, interfaces: list[dict]):
    """Diff the requested values against the interface. -> (changes, errors, unchanged).
    changes: [{key, label, old, new, remove}]; errors: {key: message}; unchanged: [key]. Unknown or inapplicable keys are errors."""
    names = {i["iface"] for i in interfaces}
    typ = row.get("type")
    errors: dict[str, str] = {}
    if typ not in EDITABLE_TYPES:
        return [], {"iface": f"{row['iface']} is a {typ or 'unknown'} interface; PyXie only edits bridges, bonds and VLAN interfaces"}, []
    changes, unchanged = [], []
    after = {k: current_value(row, k) for k in keys_for(typ)}
    for key, raw in (requested or {}).items():
        if key not in SPECS:
            errors[key] = "cannot be changed here"
            continue
        try:
            new = normalize(key, raw, typ, names)
        except NetworkConfigError as exc:
            errors[key] = str(exc)
            continue
        old = current_value(row, key)
        if (old or None) == (new or None) or (SPECS[key]["kind"] == "bool" and old == new):
            unchanged.append(key)
            continue
        after[key] = new
        changes.append({"key": key, "label": SPECS[key]["label"], "old": old, "new": new, "remove": new is None})
    if changes and not errors:
        errors.update(_cross_checks(row, after, interfaces))
    return changes, errors, unchanged


def payload(row: dict, changes: list[dict]) -> dict:
    """The Proxmox PUT for one interface: its type (required by the API), the values to set, and the settings to delete."""
    out = {"type": row["type"], "set": {}, "delete": []}
    for c in changes:
        if c["remove"]:
            out["delete"].append(c["key"])
        elif SPECS[c["key"]]["kind"] == "bool":
            out["set"][c["key"]] = int(c["new"])
        else:
            out["set"][c["key"]] = c["new"]
    return out


def staged_problems(rows: list[dict], mgmt_ip: str | None) -> list[tuple[str, str]]:
    """Checks on the STAGED listing (what the host would look like after a reload) that make a reload unsafe whoever staged it.
    -> [(rule, text)]. The address PyXie reaches the host on must still be on an interface that starts at boot."""
    if not mgmt_ip:
        return []
    holder = next((r for r in rows if str(r.get("address") or "").split("/")[0] == str(mgmt_ip)), None)
    if holder is None:
        return [("SAFE-NETWORK-012", f"after the reload no interface would hold {mgmt_ip}, the address PyXie reaches this host on. Discard the staged change or fix it at the host's console.")]
    if not _truthy(holder.get("autostart", 0)):
        return [("SAFE-NETWORK-012", f"{holder['iface']} holds {mgmt_ip} but would not start at boot.")]
    return []


def assess(row: dict, changes: list[dict], carries: list[str]) -> dict:
    """What a staged change puts at risk, given what the interface is PROVEN to carry (network_view.proofs_for_node).
    -> {critical, blocked: [(rule, text)], warnings: [text]}. A change that moves or removes the very address PyXie connects to is refused outright:
    that is done at the host's console. Other changes to a critical link are allowed, with an extra typed confirmation."""
    keys = {c["key"] for c in changes}
    mgmt = [c for c in carries if c.startswith("host management")]
    critical = bool([c for c in carries if c.startswith(("host management", "cluster"))])
    blocked, warnings = [], []
    if mgmt and keys & {"cidr", "gateway"}:
        blocked.append(("SAFE-NETWORK-010", f"{row['iface']} holds the address PyXie reaches this host on. Changing its address or gateway would cut PyXie off, and Proxmox cannot roll a network reload back: do that at the host's console."))
    if mgmt and "autostart" in keys and any(c["key"] == "autostart" and c["new"] == "0" for c in changes):
        blocked.append(("SAFE-NETWORK-011", f"{row['iface']} carries PyXie's connection; turning off start-at-boot would leave the host unreachable after its next reboot."))
    if critical:
        warnings.append("This interface carries: " + ", ".join(carries) + ". A bad value can drop PyXie, the cluster link or both until someone is at the host's console.")
    if keys & {"bridge_ports", "slaves"}:
        warnings.append("Changing the member ports briefly drops traffic on this " + str(row.get("type")) + " when it reloads.")
    if "mtu" in keys:
        warnings.append("Every device on this link, including the switch ports, has to accept the new MTU or traffic over it fails.")
    guests = [c for c in carries if c.startswith("guests") or c.startswith("guest networks")]
    if guests and keys & {"bridge_ports", "slaves", "bridge_vlan_aware", "bridge_vids", "mtu"}:
        warnings.append("Guests attached to this bridge may lose their network for a moment at reload.")
    return {"critical": critical, "blocked": blocked, "warnings": warnings}
