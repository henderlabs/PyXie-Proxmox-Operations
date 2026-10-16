"""Defaults and templates for VMs: what PyXie suggests for machine type, CPU type, disks, NIC and so on, settable per cluster, per tag, per Proxmox pool,
and as named templates picked by hand.

Layers, lowest to highest (a higher layer overrides a lower one field by field; a field a layer does not set is inherited):
built-in starting values -> all clusters -> one cluster -> the OS type -> each matching tag (by priority, then name) -> the guest's pool -> a chosen template.
`add_tags` is the exception: tags from every layer add up. PyXie stores these itself (they are PyXie settings, not Proxmox's); they prefill the edit
dialogs, flag guests that differ from them, and are what the create-VM wizard starts from.
"""

import re

from .hw_compat import valid_cpu_type, valid_machine
from .nic_workflow import BRIDGE_RE, MODELS as NIC_MODELS
from .tags_workflow import TAG_RE

SCOPE_TYPES = ("cluster", "os", "tag", "pool", "template")
_RANK = {"cluster": 1, "os": 2, "tag": 3, "pool": 4, "template": 5}

BIOS = ("seabios", "ovmf")
DISK_BUS = ("scsi", "virtio", "sata", "ide")
DISK_CACHE = ("none", "directsync", "writethrough", "writeback", "unsafe")
SCSIHW = ("virtio-scsi-single", "virtio-scsi-pci", "lsi", "lsi53c810", "megasas", "pvscsi")
OSTYPE_RE = re.compile(r"^[a-z0-9_]{2,12}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-+]{0,39}$")

# field: (label, group)
FIELDS = {
    "machine": ("Machine type", "Compute"), "cpu_type": ("CPU type", "Compute"), "bios": ("BIOS", "Compute"), "ostype": ("OS type", "Compute"),
    "cores": ("Cores", "Compute"), "sockets": ("Sockets", "Compute"), "memory_mb": ("Memory (MiB)", "Compute"), "balloon": ("Memory ballooning", "Compute"),
    "agent": ("Guest agent", "Compute"), "scsihw": ("SCSI controller", "Compute"),
    "disk_bus": ("Disk bus", "Disk"), "disk_cache": ("Disk cache", "Disk"), "disk_discard": ("Discard (TRIM)", "Disk"), "disk_ssd": ("SSD emulation", "Disk"),
    "disk_iothread": ("IO thread", "Disk"), "disk_storage": ("Disk storage", "Disk"), "disk_size_gb": ("Disk size (GiB)", "Disk"),
    "nic_model": ("NIC model", "Network"), "nic_bridge": ("Bridge", "Network"), "nic_vlan": ("VLAN tag", "Network"), "nic_firewall": ("NIC firewall", "Network"),
    "start_at_boot": ("Start at boot", "Other"), "snapshot_first": ("Safety snapshot before hardware changes", "Other"), "add_tags": ("Tags for new VMs", "Other"),
}

# Proxmox's own wizard values, with the NIC firewall flag left off (turning it on can cut a VM off when the datacenter firewall default is drop).
BUILTIN = {
    "machine": None, "cpu_type": "x86-64-v2-AES", "bios": "seabios", "ostype": "l26", "cores": 1, "sockets": 1, "memory_mb": 2048, "balloon": True,
    "agent": False, "scsihw": "virtio-scsi-single", "disk_bus": "scsi", "disk_cache": "none", "disk_discard": False, "disk_ssd": False, "disk_iothread": True,
    "disk_storage": None, "disk_size_gb": 32, "nic_model": "virtio", "nic_bridge": None, "nic_vlan": None, "nic_firewall": False,
    "start_at_boot": False, "snapshot_first": True, "add_tags": [],
}


def _bool(v):
    if isinstance(v, bool):
        return v
    raise ValueError("must be on or off")


def _int(lo, hi):
    def f(v):
        if isinstance(v, bool) or not isinstance(v, int) or not (lo <= v <= hi):
            raise ValueError(f"must be a whole number from {lo} to {hi}")
        return v
    return f


def _choice(options):
    def f(v):
        if v not in options:
            raise ValueError(f"must be one of {', '.join(options)}")
        return v
    return f


def _pattern(check, msg):
    def f(v):
        if not isinstance(v, str) or not check(v.strip()):
            raise ValueError(msg)
        return v.strip()
    return f


def _tags(v):
    if not isinstance(v, list):
        raise ValueError("must be a list of tags")
    out: list[str] = []
    for t in v:
        t = str(t).strip()
        if not t:
            continue
        if len(t) > 40 or not TAG_RE.match(t):
            raise ValueError(f"'{t}' is not a valid tag")
        if t.lower() not in [o.lower() for o in out]:
            out.append(t)
    return out


VALIDATORS = {
    "machine": _pattern(valid_machine, "not a machine type name"), "cpu_type": _pattern(valid_cpu_type, "not a CPU type name"), "bios": _choice(BIOS),
    "ostype": _pattern(lambda s: bool(OSTYPE_RE.match(s)), "not an OS type like l26, win11, win10"), "cores": _int(1, 256), "sockets": _int(1, 16),
    "memory_mb": _int(128, 4194304), "balloon": _bool, "agent": _bool, "scsihw": _choice(SCSIHW), "disk_bus": _choice(DISK_BUS), "disk_cache": _choice(DISK_CACHE),
    "disk_discard": _bool, "disk_ssd": _bool, "disk_iothread": _bool, "disk_storage": _pattern(lambda s: bool(re.match(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,39}$", s)), "not a storage name"),
    "disk_size_gb": _int(1, 65536), "nic_model": _choice(NIC_MODELS), "nic_bridge": _pattern(lambda s: bool(BRIDGE_RE.match(s)), "not a bridge name"),
    "nic_vlan": _int(1, 4094), "nic_firewall": _bool, "start_at_boot": _bool, "snapshot_first": _bool, "add_tags": _tags,
}


def validate_values(raw: dict) -> tuple[dict, list[str]]:
    """Keep only the fields a layer sets (a missing or null field means 'inherit'); return (clean values, error messages). Pure."""
    clean, errors = {}, []
    for k, v in (raw or {}).items():
        if k not in FIELDS:
            errors.append(f"unknown setting '{k}'")
            continue
        if v is None:
            continue
        try:
            clean[k] = VALIDATORS[k](v)
        except ValueError as exc:
            errors.append(f"{FIELDS[k][0]}: {exc}")
    return clean, errors


def validate_scope(scope_type: str, scope_value: str | None) -> str | None:
    """Why this scope is not valid, or None. Pure."""
    if scope_type not in SCOPE_TYPES:
        return "scope must be cluster, os, tag, pool or template"
    v = (scope_value or "").strip()
    if scope_type == "cluster":
        return None if not v else "a cluster default has no name"
    if not v:
        return f"a {scope_type} default needs a {'name' if scope_type == 'template' else scope_type}"
    if scope_type == "tag" and not TAG_RE.match(v):
        return "not a valid tag"
    if scope_type == "os" and not OSTYPE_RE.match(v):
        return "not an OS type like l26, win11, win10"
    if scope_type in ("pool", "template") and not NAME_RE.match(v):
        return "use letters, digits, spaces and - _ . + (up to 40 characters)"
    return None


def layer_order(layers: list[dict]) -> list[dict]:
    """Layers in the order they are applied, lowest first. Each layer: {scope_type, scope_value, cluster_id (None = all clusters), priority, values}. Pure."""
    return sorted(layers, key=lambda l: (_RANK.get(l["scope_type"], 9), 1 if l.get("cluster_id") else 0, int(l.get("priority") or 0), str(l.get("scope_value") or "")))


def _label(l: dict, cluster_name: str | None) -> str:
    where = f" ({cluster_name})" if l.get("cluster_id") and cluster_name and l["scope_type"] != "cluster" else ""
    if l["scope_type"] == "cluster":
        return f"cluster {cluster_name}" if l.get("cluster_id") else "all clusters"
    if l["scope_type"] == "os":
        return f"OS type {l['scope_value']}{where}"
    return f"{l['scope_type']} {l['scope_value']}{where}"


def resolve(layers: list[dict], *, cluster_id=None, tags=None, pool: str | None = None, template: str | None = None, ostype: str | None = None, cluster_names: dict | None = None) -> tuple[dict, dict]:
    """The effective values and where each came from: ({field: value}, {field: source label}). `layers` are all stored defaults; the ones that apply to this
    cluster / tag set / pool / chosen template are picked here. Pure."""
    names = cluster_names or {}
    tagset = set(tags or [])
    applies = []
    for l in layers:
        t, v, c = l["scope_type"], l.get("scope_value"), l.get("cluster_id")
        if c and str(c) != str(cluster_id):
            continue
        if t == "cluster" or (t == "os" and ostype and v == ostype) or (t == "tag" and v in tagset) or (t == "pool" and pool and v == pool) or (t == "template" and template and v == template):
            applies.append(l)
    values = {k: (list(v) if isinstance(v, list) else v) for k, v in BUILTIN.items()}
    sources = {k: "built-in" for k in BUILTIN}
    for l in layer_order(applies):
        for k, v in (l.get("values") or {}).items():
            if k == "add_tags":
                merged = list(values["add_tags"])
                merged += [t for t in v if t.lower() not in [m.lower() for m in merged]]
                values["add_tags"] = merged
                if v:
                    sources["add_tags"] = "several layers" if sources["add_tags"] not in ("built-in", _label(l, names.get(str(l.get("cluster_id"))))) else _label(l, names.get(str(l.get("cluster_id"))))
            else:
                values[k] = v
                sources[k] = _label(l, names.get(str(l.get("cluster_id"))))
    return values, sources


def machine_matches(actual: str | None, wanted: str | None) -> bool:
    """Does a VM's machine setting satisfy a default? An unversioned 'pc' / 'q35' default is met by any version of that family; no setting means i440fx. Pure."""
    a = actual or "pc"
    w = wanted or "pc"
    if w == "pc":
        return a == "pc" or a.startswith("pc-i440fx")
    if w == "q35":
        return a == "q35" or a.startswith("pc-q35")
    return a == w


def deviations(actual: dict, effective: dict, sources: dict) -> list[dict]:
    """Where a VM differs from the defaults somebody set on purpose (built-in starting values are not 'defaults': they never flag anything).
    `actual` holds the VM's own values under the same field names. Pure."""
    out = []
    for field in ("machine", "cpu_type", "bios", "agent", "scsihw", "nic_model"):
        if sources.get(field) == "built-in" or field not in actual:
            continue
        want, have = effective.get(field), actual[field]
        if field == "machine":
            same = machine_matches(have, want)
        elif field == "nic_model":
            same = all(m == want for m in have) if isinstance(have, list) and have else True
        else:
            same = have == want
        if not same:
            out.append({"field": field, "label": FIELDS[field][0], "actual": have, "default": want, "source": sources[field]})
    return out
