"""Create a VM: validate what the wizard sends and turn it into the exact parameters of Proxmox's `POST /nodes/{node}/qemu`. Pure.

Trimmed on purpose: one disk, one network device, an optional ISO, an EFI disk when the BIOS is OVMF, an optional TPM. PCI passthrough, extra disks and
extra NICs are added afterwards with the editors on the workload page. Parameter forms follow the Proxmox VE API reference for `qemu` create
(e.g. `scsi0: <storage>:<size in GiB>,<options>` allocates a new disk of that size).
"""

import re

from .defaults import BIOS, DISK_BUS, DISK_CACHE, NAME_RE, OSTYPE_RE, SCSIHW
from .hw_compat import valid_cpu_type, valid_machine
from .nic_workflow import BRIDGE_RE, MODELS as NIC_MODELS
from .tags_workflow import TAG_RE
from .labels import mem_gb

VM_NAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*$")
VOLID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*:iso/[^,;\s]+$")
STORAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,39}$")
WINDOWS_PREFIXES = ("win", "w2k")


def is_windows(ostype: str | None) -> bool:
    return str(ostype or "").lower().startswith(WINDOWS_PREFIXES)


def validate_spec(spec: dict) -> tuple[dict, list[str], list[str]]:
    """(clean spec, blocking errors, notes). Missing optional fields get the Proxmox wizard's own values. Pure."""
    errors: list[str] = []
    notes: list[str] = []
    s = dict(spec or {})

    def pick(key, default=None):
        v = s.get(key)
        return default if v is None or v == "" else v

    def need_int(key, lo, hi, label, default=None):
        v = pick(key, default)
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            errors.append(f"{label} must be a whole number from {lo} to {hi}")
            return default
        return v

    out: dict = {}
    name = str(pick("name", "")).strip()
    if not VM_NAME_RE.match(name) or len(name) > 63:
        errors.append("the name must be a DNS-style name: letters, digits and hyphens (dots between labels), at most 63 characters")
    out["name"] = name
    out["node"] = str(pick("node", "")).strip()
    if not out["node"]:
        errors.append("choose a host")
    vmid = pick("vmid")
    if vmid is not None and (isinstance(vmid, bool) or not isinstance(vmid, int) or not 100 <= vmid <= 999999999):
        errors.append("the VM ID must be a whole number from 100 up")
        vmid = None
    out["vmid"] = vmid
    out["ostype"] = str(pick("ostype", "l26"))
    if not OSTYPE_RE.match(out["ostype"]):
        errors.append("OS type looks wrong (examples: l26, win11, win10, win2k22, other)")
    out["bios"] = pick("bios", "seabios")
    if out["bios"] not in BIOS:
        errors.append("BIOS must be seabios or ovmf")
    machine = pick("machine")
    if machine is not None and not valid_machine(str(machine)):
        errors.append("machine type is not a valid name")
    out["machine"] = str(machine) if machine else None
    cpu = str(pick("cpu_type", "x86-64-v2-AES"))
    if not valid_cpu_type(cpu):
        errors.append("CPU type is not a valid name")
    out["cpu_type"] = cpu
    out["cores"] = need_int("cores", 1, 256, "cores", 1)
    out["sockets"] = need_int("sockets", 1, 16, "sockets", 1)
    out["memory_mb"] = need_int("memory_mb", 128, 4194304, "memory (MiB)", 2048)
    out["balloon"] = bool(pick("balloon", True))
    out["agent"] = bool(pick("agent", False))
    out["scsihw"] = pick("scsihw", "virtio-scsi-single")
    if out["scsihw"] not in SCSIHW:
        errors.append("SCSI controller is not one Proxmox knows")
    # disk
    bus = pick("disk_bus", "scsi")
    if bus not in DISK_BUS:
        errors.append("disk bus must be scsi, virtio, sata or ide")
    out["disk_bus"] = bus
    out["disk_size_gb"] = need_int("disk_size_gb", 1, 65536, "disk size (GiB)", 32)
    out["disk_storage"] = str(pick("disk_storage", "")).strip()
    if not STORAGE_RE.match(out["disk_storage"]):
        errors.append("choose the storage for the disk")
    cache = pick("disk_cache", "none")
    if cache not in DISK_CACHE:
        errors.append("disk cache is not a Proxmox cache mode")
    out["disk_cache"] = cache
    out["disk_discard"] = bool(pick("disk_discard", False))
    out["disk_ssd"] = bool(pick("disk_ssd", False))
    out["disk_iothread"] = bool(pick("disk_iothread", True))
    if out["disk_ssd"] and bus == "virtio":
        errors.append("SSD emulation is not available on a VirtIO disk: use SCSI or SATA, or turn it off")
    if out["disk_iothread"] and bus not in ("virtio", "scsi"):
        out["disk_iothread"] = False
        notes.append("IO thread only exists for VirtIO and SCSI disks: left off")
    if out["disk_iothread"] and bus == "scsi" and out["scsihw"] != "virtio-scsi-single":
        notes.append("an IO thread on a SCSI disk only takes effect with the 'virtio-scsi-single' controller")
    # firmware extras
    out["efi_storage"] = str(pick("efi_storage", out["disk_storage"])).strip() if out["bios"] == "ovmf" else None
    if out["bios"] == "ovmf" and not STORAGE_RE.match(out["efi_storage"] or ""):
        errors.append("choose a storage for the EFI disk")
    out["tpm"] = bool(pick("tpm", False))
    out["tpm_storage"] = str(pick("tpm_storage", out["disk_storage"])).strip() if out["tpm"] else None
    if out["tpm"] and out["bios"] != "ovmf":
        notes.append("a TPM needs UEFI: the BIOS is switched to OVMF for you")
        out["bios"] = "ovmf"
        out["efi_storage"] = out["efi_storage"] or out["disk_storage"]
    if is_windows(out["ostype"]) and out["ostype"] == "win11" and not (out["tpm"] and out["bios"] == "ovmf"):
        notes.append("Windows 11 requires UEFI and a TPM 2.0: its installer refuses to continue without them")
    iso = pick("iso")
    if iso is not None and not VOLID_RE.match(str(iso)):
        errors.append("the ISO must be a Proxmox volume like local:iso/name.iso")
    out["iso"] = str(iso) if iso else None
    # nic
    out["nic_model"] = pick("nic_model", "virtio")
    if out["nic_model"] not in NIC_MODELS:
        errors.append(f"NIC model must be one of {', '.join(NIC_MODELS)}")
    out["nic_bridge"] = str(pick("nic_bridge", "")).strip()
    if not BRIDGE_RE.match(out["nic_bridge"]):
        errors.append("choose a bridge for the network device")
    vlan = pick("nic_vlan")
    if vlan is not None and (isinstance(vlan, bool) or not isinstance(vlan, int) or not 1 <= vlan <= 4094):
        errors.append("a VLAN tag is a number from 1 to 4094 (or empty)")
        vlan = None
    out["nic_vlan"] = vlan
    out["nic_firewall"] = bool(pick("nic_firewall", False))
    # misc
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
    if out["start_after"] and not out["iso"]:
        notes.append("the VM has no installation media and an empty disk: it will start and find nothing to boot")
    return out, errors, notes


def build_params(spec: dict, vmid: int) -> dict:
    """The form fields of `POST /nodes/{node}/qemu` for a validated spec. Pure."""
    disk_key = f"{spec['disk_bus']}0"
    opts = []
    if spec["disk_cache"] != "none":
        opts.append(f"cache={spec['disk_cache']}")
    if spec["disk_discard"]:
        opts.append("discard=on")
    if spec["disk_ssd"]:
        opts.append("ssd=1")
    if spec["disk_iothread"]:
        opts.append("iothread=1")
    p: dict = {
        "vmid": vmid, "name": spec["name"], "ostype": spec["ostype"], "bios": spec["bios"], "cpu": spec["cpu_type"], "cores": spec["cores"],
        "sockets": spec["sockets"], "memory": spec["memory_mb"], "scsihw": spec["scsihw"],
        disk_key: f"{spec['disk_storage']}:{spec['disk_size_gb']}" + ("," + ",".join(opts) if opts else ""),
        "net0": f"{spec['nic_model']},bridge={spec['nic_bridge']}" + (f",tag={spec['nic_vlan']}" if spec["nic_vlan"] else "") + (",firewall=1" if spec["nic_firewall"] else ""),
    }
    if spec["machine"]:
        p["machine"] = spec["machine"]
    if not spec["balloon"]:
        p["balloon"] = 0
    if spec["agent"]:
        p["agent"] = 1
    if spec["bios"] == "ovmf":
        p["efidisk0"] = f"{spec['efi_storage']}:1,efitype=4m,pre-enrolled-keys=1"
    if spec["tpm"]:
        p["tpmstate0"] = f"{spec['tpm_storage']}:1,version=v2.0"
    boot = []
    if spec["iso"]:
        p["ide2"] = f"{spec['iso']},media=cdrom"
        boot.append("ide2")
    boot += [disk_key, "net0"]
    p["boot"] = "order=" + ";".join(boot)
    if spec["tags"]:
        p["tags"] = ";".join(spec["tags"])
    if spec["pool"]:
        p["pool"] = spec["pool"]
    if spec["start_at_boot"]:
        p["onboot"] = 1
    return p


def summarize(spec: dict) -> str:
    return (f"{spec['name']}: {spec['cores'] * spec['sockets']} vCPU ({spec['cpu_type']}), {mem_gb(spec['memory_mb'])} RAM, {spec['disk_size_gb']} GB {spec['disk_bus']} on "
            f"{spec['disk_storage']}, {spec['nic_model']} on {spec['nic_bridge']}" + (f" VLAN {spec['nic_vlan']}" if spec["nic_vlan"] else "") + f", {spec['bios']}")
