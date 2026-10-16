"""Can every host in the cluster run this CPU type / machine type? Pure checks over what Proxmox reports for each host.

Inputs per host (all read from Proxmox, none assumed): `cpu_model` and `flags` from /nodes/{n}/status (the physical CPU),
`models` = the CPU types its QEMU offers (/capabilities/qemu/cpu, with vendor), `machines` = the machine types it offers
(/capabilities/qemu/machines). A host whose data could not be read is reported "unverified", never "ok".

Verdict per host: level "ok" | "warn" | "block", plus a reason. On the VM's current host "block" means it would not start there.
On another host the same finding is only a "warn": the VM could not move there (live migration, evacuation, balancing).
"""

import re

# Linux /proc/cpuinfo flag names that make up the x86-64 microarchitecture levels Proxmox offers as x86-64-v2 / v3 / v4.
_V2 = {"cx16", "lahf_lm", "popcnt", "pni", "sse4_1", "sse4_2", "ssse3"}
_V3 = _V2 | {"avx", "avx2", "bmi1", "bmi2", "f16c", "fma", "abm", "movbe", "xsave"}
_V4 = _V3 | {"avx512f", "avx512bw", "avx512cd", "avx512dq", "avx512vl"}
LEVEL_FLAGS = {"x86-64-v2": _V2, "x86-64-v3": _V3, "x86-64-v4": _V4}

_CPU_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")
_MACHINE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+\-]*$")  # versioned machines can carry a revision: pc-q35-9.2+pve1


def valid_cpu_type(name: str) -> bool:
    return bool(name and _CPU_RE.match(name))


def valid_machine(name: str) -> bool:
    return bool(name and _MACHINE_RE.match(name))


def vendor_of(cpu_model: str | None) -> str | None:
    """Intel / AMD from a CPU model string or Proxmox's vendor field ('GenuineIntel', 'AuthenticAMD'). Pure."""
    m = (cpu_model or "").lower()
    if "intel" in m:
        return "Intel"
    if "amd" in m:
        return "AMD"
    return None


def split_cpu(setting: str | None) -> tuple[str, str]:
    """'host,flags=+aes' -> ('host', ',flags=+aes'); '' -> ('', ''). Proxmox's default (no setting) is kvm64. Pure."""
    s = str(setting or "")
    base, sep, rest = s.partition(",")
    return base.strip(), (sep + rest) if sep else ""


def _verdict(is_current: bool, level: str, reason: str) -> dict:
    if level == "block" and not is_current:
        level, reason = "warn", f"the VM could not move to this host: {reason}"
    return {"level": level, "reason": reason}


def check_cpu_type(cpu: str, hosts: list[dict]) -> list[dict]:
    """Per host: {"node", "level", "reason"} for running a VM with CPU type `cpu` on it. `hosts` items:
    {name, is_current, cpu_model, flags (set|None), models ({name: vendor}|None)}. Pure."""
    base = (cpu or "").strip() or "kvm64"
    level_key = re.sub(r"-AES$", "", base)
    needs_aes = base.endswith("-AES")
    cur_models = {h["cpu_model"] for h in hosts if h.get("cpu_model")}
    out = []
    for h in hosts:
        cur, name = bool(h.get("is_current")), h["name"]
        models, flags = h.get("models"), h.get("flags")
        if base == "host":
            if len(cur_models) > 1 and h.get("cpu_model"):
                v = _verdict(False, "warn", f"CPU type 'host' passes each host's own CPU through and the cluster mixes CPU models ({'; '.join(sorted(cur_models))}), so a live migration between hosts with different models fails or crashes the VM")
            else:
                v = {"level": "ok", "reason": "all hosts have the same CPU model, so 'host' is safe to migrate between them" if len(cur_models) == 1 else "host CPU models could not be compared"}
                if not cur_models:
                    v["level"] = "warn"
        elif models is None:
            v = {"level": "warn", "reason": "this host's CPU types could not be read: unverified"}
        elif base not in models and base not in ("kvm64", "qemu64", "kvm32", "qemu32", "max"):
            v = _verdict(cur, "block", f"its QEMU does not offer CPU type '{base}'")
        elif level_key in LEVEL_FLAGS:
            need = set(LEVEL_FLAGS[level_key]) | ({"aes"} if needs_aes else set())
            if flags is None:
                v = {"level": "warn", "reason": "this host's CPU flags could not be read: unverified"}
            else:
                missing = sorted(need - flags)
                v = _verdict(cur, "block", f"its CPU lacks {', '.join(missing)}, which '{base}' requires") if missing else {"level": "ok", "reason": f"its CPU has every flag '{base}' needs"}
        else:
            vendor = vendor_of(models.get(base)) if isinstance(models, dict) else None
            hv = vendor_of(h.get("cpu_model"))
            if vendor and hv and vendor != hv:
                v = _verdict(False, "warn", f"'{base}' is a {vendor} CPU model and this host is {hv}: Proxmox does not support running it there reliably")
            else:
                v = {"level": "ok", "reason": "offered by this host's QEMU"}
        out.append({"node": name, **v})
    return out


def check_machine(machine: str, hosts: list[dict]) -> list[dict]:
    """Per host: can its QEMU run machine type `machine`? 'pc' and 'q35' (no version) always can: they mean 'this host's newest'. Pure."""
    m = (machine or "").strip()
    out = []
    for h in hosts:
        cur, name, known = bool(h.get("is_current")), h["name"], h.get("machines")
        if not m or m in ("pc", "q35"):
            v = {"level": "ok", "reason": "unversioned: this host uses its own newest version"}
        elif known is None:
            v = {"level": "warn", "reason": "this host's machine types could not be read: unverified"}
        elif m not in known:
            v = _verdict(cur, "block", f"its QEMU is too old for machine type '{m}'")
        else:
            v = {"level": "ok", "reason": "offered by this host's QEMU"}
        out.append({"node": name, **v})
    return out
