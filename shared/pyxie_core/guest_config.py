"""Guest (VM / container) configuration options PyXie may change from the Config tab, with validation and diffing. Pure.

Every option is an explicit entry: its label, group, value type and limits, whether Proxmox applies it live or only after
the guest restarts, and the Proxmox privilege needed. Anything not listed here is not editable here (disks, network adapters,
CD/ISO and tags have their own panels; hook scripts, extra QEMU args and device passthrough are root-only on Proxmox).
Values are checked against Proxmox's own limits BEFORE a preview is built, so a bad value is a form error, not a failed task.
"""

import re

OWN_PANEL = {
    # Shown read-only on the Config tab with the place that edits them.
    "memory": "Resize (memory)", "balloon": "Resize (memory)", "cores": "Resize (CPU)", "sockets": "Resize (CPU)", "vcpus": "Resize (CPU)",
    "cpu": "Hardware > CPU type", "machine": "Hardware > machine type", "bios": "Hardware > firmware",
}

OSTYPES_VM = ["other", "wxp", "w2k", "w2k3", "w2k8", "wvista", "win7", "win8", "win10", "win11", "l24", "l26", "solaris"]
KEYBOARDS = ["de", "de-ch", "da", "en-gb", "en-us", "es", "fi", "fr", "fr-be", "fr-ca", "fr-ch", "hu", "is", "it", "ja", "lt", "mk", "nl", "no", "pl", "pt", "pt-br", "sv", "sl", "tr"]

# key -> spec. type: text|bool|int|float|enum|startup|regex.  apply: live | restart.  priv: the VM.* privilege Proxmox checks.
VM = {
    "name": dict(label="Name", group="General", type="regex", pattern=r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,61}[A-Za-z0-9])?$", apply="live", priv="VM.Config.Options", hint="letters, digits, dots and dashes"),
    "description": dict(label="Description", group="General", type="text", max=8192, apply="live", priv="VM.Config.Options"),
    "onboot": dict(label="Start at boot", group="General", type="bool", apply="live", priv="VM.Config.Options"),
    "startup": dict(label="Start order and delays", group="General", type="startup", apply="live", priv="VM.Config.Options"),
    "protection": dict(label="Protection (blocks delete and disk removal)", group="General", type="bool", apply="live", priv="VM.Config.Options"),
    "ostype": dict(label="OS type", group="General", type="enum", choices=OSTYPES_VM, apply="restart", priv="VM.Config.Options"),
    "agent": dict(label="QEMU guest agent", group="General", type="bool", apply="restart", priv="VM.Config.Options"),
    "cpulimit": dict(label="CPU limit (0 = none)", group="CPU", type="float", min=0, max=128, apply="live", priv="VM.Config.CPU"),
    "cpuunits": dict(label="CPU weight", group="CPU", type="int", min=1, max=262144, apply="live", priv="VM.Config.CPU"),
    "numa": dict(label="NUMA", group="CPU", type="bool", apply="restart", priv="VM.Config.CPU"),
    "shares": dict(label="Memory ballooning shares (0 = off)", group="Memory", type="int", min=0, max=50000, apply="live", priv="VM.Config.Memory"),
    "vga": dict(label="Display", group="Boot, firmware and hardware", type="regex", pattern=r"^[a-z0-9-]+(,memory=\d{1,5})?$", apply="restart", priv="VM.Config.HWType", hint="e.g. std, virtio, qxl, serial0, or std,memory=32"),
    "tablet": dict(label="USB tablet pointer", group="Boot, firmware and hardware", type="bool", apply="restart", priv="VM.Config.HWType"),
    "keyboard": dict(label="Keyboard layout", group="Boot, firmware and hardware", type="enum", choices=KEYBOARDS, apply="restart", priv="VM.Config.Options"),
    "localtime": dict(label="Hardware clock uses local time", group="Boot, firmware and hardware", type="bool", apply="restart", priv="VM.Config.Options"),
    "acpi": dict(label="ACPI", group="Boot, firmware and hardware", type="bool", apply="restart", priv="VM.Config.HWType"),
    "kvm": dict(label="KVM hardware virtualization", group="Boot, firmware and hardware", type="bool", apply="restart", priv="VM.Config.HWType"),
    "startdate": dict(label="Start date", group="Boot, firmware and hardware", type="regex", pattern=r"^(now|\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2})?)$", apply="restart", priv="VM.Config.Options", hint="now, 2026-11-02 or 2026-11-02T10:30:00"),
    "hotplug": dict(label="Hotplug", group="Boot, firmware and hardware", type="regex", pattern=r"^(0|1|(disk|network|usb|memory|cpu)(,(disk|network|usb|memory|cpu))*)$", apply="restart", priv="VM.Config.Options", hint="0, 1, or a list of disk,network,usb,memory,cpu"),
    "boot": dict(label="Boot order", group="Boot, firmware and hardware", type="regex", pattern=r"^order=[A-Za-z0-9_\[\]]+(;[A-Za-z0-9_\[\]]+)*$", apply="restart", priv="VM.Config.Disk", hint="e.g. order=scsi0;net0"),
    "migrate_downtime": dict(label="Migration max downtime (seconds)", group="Migration", type="float", min=0, max=100, apply="live", priv="VM.Config.Options"),
    "migrate_speed": dict(label="Migration speed limit (MB/s, 0 = none)", group="Migration", type="int", min=0, max=10_000_000, apply="live", priv="VM.Config.Options"),
}
CT = {
    "hostname": dict(label="Hostname", group="General", type="regex", pattern=r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,61}[A-Za-z0-9])?$", apply="restart", priv="VM.Config.Network", hint="letters, digits, dots and dashes"),
    "description": dict(label="Description", group="General", type="text", max=8192, apply="live", priv="VM.Config.Options"),
    "onboot": dict(label="Start at boot", group="General", type="bool", apply="live", priv="VM.Config.Options"),
    "startup": dict(label="Start order and delays", group="General", type="startup", apply="live", priv="VM.Config.Options"),
    "protection": dict(label="Protection (blocks delete and disk removal)", group="General", type="bool", apply="live", priv="VM.Config.Options"),
    "timezone": dict(label="Time zone", group="General", type="regex", pattern=r"^(host|[A-Za-z0-9_+/-]{1,64})$", apply="restart", priv="VM.Config.Options", hint="host, or e.g. America/Denver"),
    "cpulimit": dict(label="CPU limit (0 = none)", group="CPU", type="float", min=0, max=8192, apply="live", priv="VM.Config.CPU"),
    "cpuunits": dict(label="CPU weight", group="CPU", type="int", min=0, max=500000, apply="live", priv="VM.Config.CPU"),
    "swap": dict(label="Swap (MB)", group="Memory", type="int", min=0, max=1_000_000, apply="live", priv="VM.Config.Memory"),
    "nameserver": dict(label="DNS servers", group="DNS", type="regex", pattern=r"^[0-9a-fA-F:., ]{0,200}$", apply="restart", priv="VM.Config.Network", hint="IP addresses separated by spaces"),
    "searchdomain": dict(label="DNS search domain", group="DNS", type="regex", pattern=r"^[A-Za-z0-9.-]{0,255}$", apply="restart", priv="VM.Config.Network"),
    "cmode": dict(label="Console mode", group="Console", type="enum", choices=["tty", "console", "shell"], apply="restart", priv="VM.Config.Options"),
    "tty": dict(label="Number of ttys", group="Console", type="int", min=0, max=6, apply="restart", priv="VM.Config.Options"),
    "console": dict(label="Attach a console device", group="Console", type="bool", apply="restart", priv="VM.Config.Options"),
}
SPECS = {"vm": VM, "ct": CT}
GROUP_ORDER = ["General", "CPU", "Memory", "DNS", "Boot, firmware and hardware", "Console", "Migration"]


class ConfigValueError(ValueError):
    pass


def truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "yes", "true", "on")


def parse_startup(s: str | None) -> dict:
    """"order=2,up=30,down=60" -> {"order": 2, "up": 30, "down": 60} (only the parts present)."""
    out = {}
    for part in (s or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k in ("order", "up", "down") and v.strip().lstrip("-").isdigit():
                out[k] = int(v)
    return out


def normalize(spec: dict, value) -> str | None:
    """Validate a requested value and return the string Proxmox stores, or None to remove the option. Raises ConfigValueError."""
    t = spec["type"]
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    if t == "bool":
        if isinstance(value, bool):
            return "1" if value else "0"
        if str(value).strip().lower() in ("1", "0", "true", "false", "yes", "no", "on", "off"):
            return "1" if truthy(value) else "0"
        raise ConfigValueError("must be yes or no")
    if t == "int":
        try:
            n = int(str(value).strip())
        except ValueError:
            raise ConfigValueError("must be a whole number")
        if n < spec["min"] or n > spec["max"]:
            raise ConfigValueError(f"must be between {spec['min']} and {spec['max']}")
        return str(n)
    if t == "float":
        try:
            f = float(str(value).strip())
        except ValueError:
            raise ConfigValueError("must be a number")
        if f < spec["min"] or f > spec["max"]:
            raise ConfigValueError(f"must be between {spec['min']} and {spec['max']}")
        return str(int(f)) if f == int(f) else str(f)
    if t == "enum":
        v = str(value).strip()
        if v not in spec["choices"]:
            raise ConfigValueError("must be one of: " + ", ".join(spec["choices"]))
        return v
    if t == "regex":
        v = str(value).strip()
        if not re.match(spec["pattern"], v):
            raise ConfigValueError("not a valid value" + (f" ({spec['hint']})" if spec.get("hint") else ""))
        return v
    if t == "text":
        v = str(value)
        if len(v) > spec["max"]:
            raise ConfigValueError(f"too long (limit {spec['max']} characters)")
        if "\x00" in v:
            raise ConfigValueError("contains an invalid character")
        return v
    if t == "startup":
        parts = value if isinstance(value, dict) else parse_startup(str(value))
        out = []
        for k in ("order", "up", "down"):
            if parts.get(k) in (None, ""):
                continue
            try:
                n = int(parts[k])
            except (TypeError, ValueError):
                raise ConfigValueError(f"{k} must be a whole number")
            if n < 0 or n > 100000:
                raise ConfigValueError(f"{k} must be between 0 and 100000")
            out.append(f"{k}={n}")
        return ",".join(out) or None
    raise ConfigValueError("unsupported option type")


def comparable(spec: dict, stored) -> str | None:
    """A value as Proxmox returned it, in the same form normalize() produces, so old and new can be compared."""
    if stored is None or str(stored) == "":
        return None
    if spec["type"] == "bool":
        return "1" if truthy(stored) else "0"
    if spec["type"] in ("int", "float"):
        try:
            return normalize(spec, stored)
        except ConfigValueError:
            return str(stored)
    if spec["type"] == "startup":
        p = parse_startup(str(stored))
        return ",".join(f"{k}={p[k]}" for k in ("order", "up", "down") if k in p) or None
    return str(stored)


def agent_enabled(stored) -> str:
    """`agent` is stored as "enabled=1,type=virtio,..."; PyXie only toggles `enabled` and keeps the rest."""
    return "1" if re.match(r"^(1|enabled=1)", str(stored or "")) else "0"


def agent_with_enabled(stored, enabled: bool) -> str:
    rest = [p for p in re.split(r",", str(stored or "")) if p and not re.match(r"^(enabled=|[01]$)", p)]
    return ",".join([f"enabled={'1' if enabled else '0'}", *rest])


def plan_changes(kind: str, current: dict, requested: dict) -> tuple[list[dict], dict, list[str]]:
    """Diff requested changes against the guest's current config.

    Returns (changes, errors, unchanged): changes = [{key, label, old, new, apply, priv, remove}] for options that really change;
    errors = {key: message}; unchanged = keys whose requested value equals the current one. Unknown keys are errors."""
    specs = SPECS[kind]
    changes, errors, unchanged = [], {}, []
    for key, value in requested.items():
        spec = specs.get(key)
        if key == "agent" and kind == "vm":
            spec = dict(label="QEMU guest agent", group="General", type="bool", apply="restart", priv="VM.Config.Options")
        if spec is None:
            errors[key] = "this option cannot be changed from the Config tab" + (f" (use {OWN_PANEL[key]})" if key in OWN_PANEL else "")
            continue
        try:
            new = normalize(spec, value)
        except ConfigValueError as exc:
            errors[key] = f"{spec['label']}: {exc}"
            continue
        if key == "agent":
            old = agent_enabled(current.get("agent"))
            stored_new = None if new is None else agent_with_enabled(current.get("agent"), new == "1")
            if new == old or (new is None and old == "0"):
                unchanged.append(key)
                continue
            changes.append({"key": key, "label": spec["label"], "old": old, "new": new or "0", "apply": spec["apply"], "priv": spec["priv"], "remove": False, "value": stored_new or agent_with_enabled(current.get("agent"), False)})
            continue
        old = comparable(spec, current.get(key))
        if new == old:
            unchanged.append(key)
            continue
        changes.append({"key": key, "label": spec["label"], "old": old, "new": new, "apply": spec["apply"], "priv": spec["priv"], "remove": new is None, "value": new})
    return changes, errors, unchanged


def pending_keys(pending_rows: list[dict]) -> list[dict]:
    """From Proxmox's /pending listing: the options with a change waiting for the next start."""
    out = []
    for r in pending_rows or []:
        if "pending" in r or r.get("delete"):
            out.append({"key": r.get("key"), "value": r.get("value"), "pending": r.get("pending"), "delete": bool(r.get("delete"))})
    return out
