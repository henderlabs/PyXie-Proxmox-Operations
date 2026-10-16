"""VMs (qemu) and containers (lxc) share most Proxmox calls, only the path differs (/qemu/ vs /lxc/). These helpers pick the right read call for a workload so
the lifecycle, snapshot and delete workflows can serve both. Pure plumbing; no decisions."""

from .models import Workload


def is_ct(workload: Workload) -> bool:
    return workload.type == "lxc"


def kind(workload: Workload) -> str:
    return "lxc" if is_ct(workload) else "qemu"


def config(client, workload: Workload, node: str) -> dict:
    return (client.lxc_config(node, workload.vmid) if is_ct(workload) else client.qemu_config(node, workload.vmid)) or {}


def snapshots(client, workload: Workload, node: str) -> list[dict]:
    return client.lxc_snapshots(node, workload.vmid) if is_ct(workload) else client.qemu_snapshots(node, workload.vmid)


def status_current(client, workload: Workload, node: str) -> dict:
    return (client.lxc_status_current(node, workload.vmid) if is_ct(workload) else client.qemu_status_current(node, workload.vmid)) or {}


def live_list(client, workload: Workload, node: str) -> list[dict]:
    return client.lxc_list(node) if is_ct(workload) else client.qemu_list(node)


def can_snapshot(client, workload: Workload, node: str) -> bool | None:
    """Proxmox's own answer for this guest; None when it could not be asked (then the attempt itself decides)."""
    try:
        return client.lxc_feature(node, workload.vmid, "snapshot") if is_ct(workload) else client.qemu_feature(node, workload.vmid, "snapshot")
    except Exception:  # noqa: BLE001
        return None
