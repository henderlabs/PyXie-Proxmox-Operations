"""Create a container: validate what the wizard sends and turn it into the parameters of Proxmox's `POST /nodes/{node}/lxc`. Pure.

Trimmed on purpose: an unprivileged container (a privileged one can only be made by root@pam, which PyXie does not use), one root disk, one network device, an optional
SSH public key. No password is taken: PyXie would have to keep it until the worker runs, and a secret does not belong in an operation record. Log in with the key, or
open a shell from Proxmox (`pct enter`) and set a password there.
"""

import ipaddress
import re

from .defaults import NAME_RE
from .nic_workflow import BRIDGE_RE
from .tags_workflow import TAG_RE
from .vm_create import STORAGE_RE, VM_NAME_RE
from .labels import mem_gb

TEMPLATE_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]{0,39}):vztmpl/([A-Za-z0-9][A-Za-z0-9_.+\-]{0,120}\.tar\.(gz|xz|zst|bz2))$")
KEY_PREFIXES = ("ssh-rsa ", "ssh-ed25519 ", "ecdsa-sha2-", "sk-ssh-ed25519@", "sk-ecdsa-sha2-")


def valid_ssh_keys(text: str) -> tuple[str, list[str]]:
    """(cleaned keys one per line, problems). Pure."""
    keys, problems = [], []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith(KEY_PREFIXES) or len(line.split()) < 2:
            problems.append("an SSH public key starts with ssh-ed25519, ssh-rsa, ecdsa-sha2-... followed by the key text")
        else:
            keys.append(line)
    return "\n".join(keys), problems


def validate_ct_spec(spec: dict) -> tuple[dict, list[str], list[str]]:
    """(clean spec, blocking errors, notes). Pure."""
    errors: list[str] = []
    notes: list[str] = []
    s = dict(spec or {})

    def pick(k, d=None):
        v = s.get(k)
        return d if v is None or v == "" else v

    def need_int(k, lo, hi, label, d):
        v = pick(k, d)
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            errors.append(f"{label} must be a whole number from {lo} to {hi}")
            return d
        return v

    out: dict = {"node": str(pick("node", "")).strip(), "hostname": str(pick("hostname", "")).strip()}
    if not out["node"]:
        errors.append("choose a host")
    if not VM_NAME_RE.match(out["hostname"]) or len(out["hostname"]) > 63:
        errors.append("the hostname must be a DNS-style name: letters, digits and hyphens, at most 63 characters")
    vmid = pick("vmid")
    if vmid is not None and (isinstance(vmid, bool) or not isinstance(vmid, int) or vmid < 100):
        errors.append("the ID must be a whole number from 100 up")
        vmid = None
    out["vmid"] = vmid
    out["template"] = str(pick("template", ""))
    if not TEMPLATE_RE.match(out["template"]):
        errors.append("choose a container template (storage:vztmpl/name.tar.zst)")
    out["cores"] = need_int("cores", 1, 256, "cores", 1)
    out["memory_mb"] = need_int("memory_mb", 16, 4194304, "memory (MiB)", 512)
    out["swap_mb"] = need_int("swap_mb", 0, 4194304, "swap (MiB)", 512)
    out["disk_storage"] = str(pick("disk_storage", "")).strip()
    if not STORAGE_RE.match(out["disk_storage"]):
        errors.append("choose the storage for the root disk")
    out["disk_size_gb"] = need_int("disk_size_gb", 1, 65536, "root disk size (GiB)", 8)
    out["nic_bridge"] = str(pick("nic_bridge", "")).strip()
    if not BRIDGE_RE.match(out["nic_bridge"]):
        errors.append("choose a bridge")
    vlan = pick("nic_vlan")
    if vlan is not None and (isinstance(vlan, bool) or not isinstance(vlan, int) or not 1 <= vlan <= 4094):
        errors.append("a VLAN tag is a number from 1 to 4094 (or empty)")
        vlan = None
    out["nic_vlan"] = vlan
    out["nic_firewall"] = bool(pick("nic_firewall", False))
    ip = str(pick("ip", "dhcp")).strip()
    out["ip"], out["gateway"] = ip, str(pick("gateway", "")).strip() or None
    if ip not in ("dhcp", "manual"):
        try:
            iface = ipaddress.ip_interface(ip)
            if iface.version != 4 or "/" not in ip:
                raise ValueError
        except ValueError:
            errors.append("the address is 'dhcp' or an IPv4 address with its prefix, like 192.168.1.50/24")
        if out["gateway"]:
            try:
                ipaddress.IPv4Address(out["gateway"])
            except ValueError:
                errors.append("the gateway must be an IPv4 address")
    elif out["gateway"]:
        notes.append("a gateway is only used with a fixed address: ignored")
        out["gateway"] = None
    out["nameserver"] = str(pick("nameserver", "")).strip() or None
    if out["nameserver"]:
        try:
            for n in out["nameserver"].split():
                ipaddress.ip_address(n)
        except ValueError:
            errors.append("the DNS server must be an IP address (several separated by spaces)")
    out["searchdomain"] = str(pick("searchdomain", "")).strip() or None
    keys, problems = valid_ssh_keys(str(pick("ssh_keys", "")))
    errors += problems
    out["ssh_keys"] = keys or None
    if not keys:
        notes.append("no SSH key given and no password is set: nobody can log in over the network. Open a shell from Proxmox (pct enter) to set one")
    tags = []
    for t in pick("tags", []) or []:
        t = str(t).strip()
        if not t:
            continue
        if len(t) > 40 or not TAG_RE.match(t):
            errors.append(f"'{t}' is not a valid tag")
        elif t.lower() not in [x.lower() for x in tags]:
            tags.append(t)
    out["tags"] = tags
    pool = pick("pool")
    if pool is not None and not NAME_RE.match(str(pool)):
        errors.append("the pool name is not valid")
    out["pool"] = str(pool) if pool else None
    out["start_at_boot"] = bool(pick("start_at_boot", False))
    out["start_after"] = bool(pick("start_after", False))
    return out, errors, notes


def build_ct_params(spec: dict, vmid: int) -> dict:
    """The form fields of `POST /nodes/{node}/lxc`. Pure."""
    net = f"name=eth0,bridge={spec['nic_bridge']},ip={spec['ip']}"
    if spec.get("gateway"):
        net += f",gw={spec['gateway']}"
    if spec["nic_vlan"]:
        net += f",tag={spec['nic_vlan']}"
    if spec["nic_firewall"]:
        net += ",firewall=1"
    p: dict = {"vmid": vmid, "ostemplate": spec["template"], "hostname": spec["hostname"], "cores": spec["cores"], "memory": spec["memory_mb"], "swap": spec["swap_mb"],
               "rootfs": f"{spec['disk_storage']}:{spec['disk_size_gb']}", "net0": net, "unprivileged": 1}
    if spec["ssh_keys"]:
        p["ssh-public-keys"] = spec["ssh_keys"]
    if spec["nameserver"]:
        p["nameserver"] = spec["nameserver"]
    if spec["searchdomain"]:
        p["searchdomain"] = spec["searchdomain"]
    if spec["tags"]:
        p["tags"] = ";".join(spec["tags"])
    if spec["pool"]:
        p["pool"] = spec["pool"]
    if spec["start_at_boot"]:
        p["onboot"] = 1
    return p


def summarize(spec: dict) -> str:
    return (f"{spec['hostname']}: {spec['cores']} vCPU, {mem_gb(spec['memory_mb'])} RAM, {spec['disk_size_gb']} GB on {spec['disk_storage']}, {spec['nic_bridge']} ({spec['ip']})"
            + (f" VLAN {spec['nic_vlan']}" if spec["nic_vlan"] else "") + ", unprivileged")
