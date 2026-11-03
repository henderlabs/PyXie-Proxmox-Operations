"""Host (Proxmox node) settings PyXie may change from a host's Config tab: node options, DNS, time zone and the hosts file. Pure.

Network INTERFACES are not here (a later step, staged and reloaded). Values are validated before a preview exists.
"""

import ipaddress
import re

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
DOMAIN_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
TZ_RE = re.compile(r"^[A-Za-z0-9_+/-]{1,64}$")

# key -> spec. section: which Proxmox endpoint carries it.  priv: Proxmox privilege on /nodes/<node>.
SPECS = {
    "description": dict(label="Description", section="config", type="text", max=8192, group="Node options"),
    "location": dict(label="Location", section="config", type="text", max=255, group="Node options", hint="e.g. Rack 2, U14"),
    "wakeonlan": dict(label="Wake-on-LAN MAC", section="config", type="mac", group="Node options", hint="the MAC PyXie could wake this host with"),
    "startall-onboot-delay": dict(label="Delay before 'start all on boot' (seconds)", section="config", type="int", min=0, max=300, group="Node options"),
    "ballooning-target": dict(label="Memory ballooning target (% of RAM)", section="config", type="int", min=0, max=100, group="Node options"),
    "dns1": dict(label="DNS server 1", section="dns", type="ip", group="DNS and time"),
    "dns2": dict(label="DNS server 2", section="dns", type="ip", group="DNS and time"),
    "dns3": dict(label="DNS server 3", section="dns", type="ip", group="DNS and time"),
    "search": dict(label="DNS search domain", section="dns", type="domain", required=True, group="DNS and time"),
    "timezone": dict(label="Time zone", section="time", type="tz", group="DNS and time", hint="e.g. America/Denver"),
    "hosts": dict(label="/etc/hosts", section="hosts", type="hosts", max=65536, group="Hosts file"),
}
GROUP_ORDER = ["Node options", "DNS and time", "Hosts file"]
PRIV = "Sys.Modify"


class NodeConfigError(ValueError):
    pass


def normalize(key: str, value, node_name: str | None = None) -> str | None:
    """Validate one requested value; return what Proxmox stores, or None to remove the option. Raises NodeConfigError."""
    spec = SPECS[key]
    t = spec["type"]
    if value is None or (isinstance(value, str) and value.strip() == "" and t != "hosts"):
        if spec.get("required"):
            raise NodeConfigError(f"{spec['label']} cannot be empty")
        return None
    v = str(value)
    if t == "text":
        if len(v) > spec["max"]:
            raise NodeConfigError(f"too long (limit {spec['max']} characters)")
        return v
    if t == "mac":
        if not MAC_RE.match(v.strip()):
            raise NodeConfigError("not a MAC address (aa:bb:cc:dd:ee:ff)")
        return v.strip().lower()
    if t == "int":
        try:
            n = int(v.strip())
        except ValueError:
            raise NodeConfigError("must be a whole number")
        if n < spec["min"] or n > spec["max"]:
            raise NodeConfigError(f"must be between {spec['min']} and {spec['max']}")
        return str(n)
    if t == "ip":
        try:
            return str(ipaddress.ip_address(v.strip()))
        except ValueError:
            raise NodeConfigError("not an IPv4 or IPv6 address")
    if t == "domain":
        if not DOMAIN_RE.match(v.strip()):
            raise NodeConfigError("not a valid domain name")
        return v.strip()
    if t == "tz":
        if not TZ_RE.match(v.strip()):
            raise NodeConfigError("not a valid time zone name")
        return v.strip()
    if t == "hosts":
        if "\x00" in v:
            raise NodeConfigError("contains an invalid character")
        if len(v.encode()) > spec["max"]:
            raise NodeConfigError("too long (limit 64 KB)")
        if not v.strip():
            raise NodeConfigError("the hosts file cannot be empty")
        text = v.replace("\r\n", "\n")
        return text if text.endswith("\n") else text + "\n"
    raise NodeConfigError("unsupported option")


def hosts_problem(text: str, node_name: str) -> str | None:
    """A hosts file that does not resolve the node's own name breaks the cluster: say so. Pure."""
    names = set()
    for line in text.splitlines():
        body = line.split("#", 1)[0].split()
        if len(body) >= 2:
            names.update(n.lower() for n in body[1:])
    if node_name.lower() not in names:
        return f"it no longer contains this host's own name ({node_name}); Proxmox and the cluster need it to resolve"
    if "localhost" not in names:
        return "it has no entry for localhost"
    return None


def plan_changes(current: dict, requested: dict, node_name: str) -> tuple[list[dict], dict, list[str]]:
    """(changes, errors, unchanged). current maps each key to its stored string (hosts: the whole text)."""
    changes, errors, unchanged = [], {}, []
    for key, value in requested.items():
        if key not in SPECS:
            errors[key] = "this host setting cannot be changed from the Config tab"
            continue
        spec = SPECS[key]
        try:
            new = normalize(key, value, node_name)
        except NodeConfigError as exc:
            errors[key] = f"{spec['label']}: {exc}"
            continue
        if key == "hosts":
            bad = hosts_problem(new, node_name)
            if bad:
                errors[key] = f"{spec['label']}: {bad}"
                continue
        old = current.get(key)
        old = None if old in (None, "") else str(old)
        if key == "hosts" and old is not None:
            old = old.replace("\r\n", "\n")
            old = old if old.endswith("\n") else old + "\n"
        if old == new:
            unchanged.append(key)
            continue
        changes.append({"key": key, "label": spec["label"], "section": spec["section"], "old": old, "new": new, "remove": new is None})
    return changes, errors, unchanged


def section_payloads(current: dict, changes: list[dict]) -> dict[str, dict]:
    """The Proxmox calls needed: {section: {"set": {...}, "delete": [...]}}. DNS must always carry `search` and the full server list."""
    out: dict[str, dict] = {}
    for c in changes:
        sec = out.setdefault(c["section"], {"set": {}, "delete": []})
        if c["remove"]:
            sec["delete"].append(c["key"])
        else:
            sec["set"][c["key"]] = c["new"]
    if "dns" in out:
        merged = {k: current.get(k) for k in ("dns1", "dns2", "dns3", "search") if current.get(k)}
        merged.update(out["dns"]["set"])
        for k in out["dns"]["delete"]:
            merged.pop(k, None)
        out["dns"] = {"set": merged, "delete": []}
    return out
