"""Proxmox Backup Server as a managed host: credentials, live status, and the SSH maintenance credential.
Read-only except where a workflow calls it."""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .crypto import decrypt_secret
from .host_maintenance_client import HostMaintenanceCredentials
from .models import HostMaintenanceCredential, ProtectionCredential, ProtectionTarget
from .pbs_client import PbsClient, PbsCredentials

now = lambda: datetime.now(timezone.utc)


class PbsNotConfigured(Exception):
    pass


def load_pbs_credentials(db: Session, target: ProtectionTarget, slot: str = "inventory") -> PbsCredentials:
    cred = db.query(ProtectionCredential).filter(
        ProtectionCredential.protection_target_id == target.id, ProtectionCredential.slot_name == slot).one_or_none()
    if cred is None:
        raise PbsNotConfigured(f"no {slot!r} API token is saved for {target.name!r}")
    return PbsCredentials(target.hostname, target.api_port, cred.token_user, cred.token_id,
                          decrypt_secret(cred.encrypted_secret), target.tls_verify)


def load_pbs_ssh_credentials(db: Session, target: ProtectionTarget) -> HostMaintenanceCredentials:
    cred = db.query(HostMaintenanceCredential).filter(HostMaintenanceCredential.protection_target_id == target.id).one_or_none()
    if cred is None:
        raise PbsNotConfigured(f"no SSH credential is set up for {target.name!r}")
    return HostMaintenanceCredentials(
        hostname=target.hostname, port=cred.ssh_port, username=cred.ssh_username,
        private_key_pem=decrypt_secret(cred.encrypted_private_key),
        expected_host_key_type=target.ssh_host_key_type, expected_host_key_base64=target.ssh_host_key_base64)


def _kernel(k):
    if isinstance(k, dict):
        return f"{k.get('sysname', 'Linux')} {k.get('release', '')}".strip()
    return k


def _vkey(v: str) -> tuple:
    import re as _re
    return tuple(int(x) for x in _re.findall(r"\d+", v or ""))


def reboot_needed(running_kernel: str | None, versions: list[dict] | None) -> bool | None:
    """Same test the host wrapper uses: the newest installed proxmox-kernel package differs from the running kernel
    (old kernels stay installed, so take the highest version). None when it cannot be told."""
    if not running_kernel or not versions:
        return None
    installed = [v.get("Version") for v in versions if str(v.get("Package", "")).startswith("proxmox-kernel-") and str(v.get("Package", "")).endswith("-pve-signed") and v.get("Version")]
    if not installed:
        return None
    latest = max(installed, key=_vkey)
    base = running_kernel[:-4] if running_kernel.endswith("-pve") else running_kernel
    return latest != base


def _safe(fn, default):
    try:
        return fn()
    except Exception:  # noqa: BLE001 -- a missing permission must not hide the rest of the status
        return default


def collect_status(client: PbsClient, at: float | None = None) -> dict:
    """One read-only pass over a PBS server. Anything the token cannot read comes back as None, not an error."""
    ver = _safe(client.version, {}) or {}
    st = _safe(client.node_status, {}) or {}
    updates = _safe(client.apt_updates, None)
    sub = _safe(client.subscription, {}) or {}
    running = _safe(lambda: client.tasks(running=True, limit=100), None)
    since = int((at or now().timestamp()) - 86400)
    failed = _safe(lambda: client.tasks(errors=True, limit=50, since=since), None)
    usage = _safe(client.datastore_usage, None)
    versions = _safe(lambda: client.apt_versions(), None)
    mem, root = st.get("memory") or {}, st.get("root") or {}
    return {
        "reachable": bool(ver),
        "version": ver.get("version"), "release": ver.get("release"),
        "uptime": st.get("uptime"), "cpu": st.get("cpu"), "load": st.get("loadavg"),
        "memory": {"used": mem.get("used"), "total": mem.get("total")} if mem else None,
        "root": {"used": root.get("used"), "total": root.get("total")} if root else None,
        "kernel": _kernel(st.get("current-kernel") or st.get("kversion")),
        "reboot_required": reboot_needed((st.get("current-kernel") or {}).get("release") if isinstance(st.get("current-kernel"), dict) else None, versions),
        "updates": None if updates is None else {
            "count": len(updates),
            "packages": [{"package": u.get("Package"), "current_version": u.get("OldVersion"), "new_version": u.get("Version")} for u in updates[:200]],
        },
        "subscription": sub.get("status"),
        "tasks_running": None if running is None else [{"type": t.get("worker_type"), "id": t.get("worker_id"), "start": t.get("starttime")} for t in running],
        "tasks_failed_24h": None if failed is None else [
            {"type": t.get("worker_type"), "id": t.get("worker_id"), "end": t.get("endtime"), "status": t.get("status")}
            for t in failed if t.get("status") not in (None, "OK") and not str(t.get("status")).startswith("WARNINGS")][:20],
        "datastores": None if usage is None else [
            {"name": u.get("store"), "used": u.get("used"), "total": u.get("total"), "avail": u.get("avail"),
             "estimated_full": u.get("estimated-full-date")} for u in usage],
        "checked_at": datetime.fromtimestamp(at or now().timestamp(), tz=timezone.utc).isoformat(),
    }


def refresh_status(db: Session, target: ProtectionTarget) -> dict:
    """Read and store this server's status. A server that cannot be reached is stored as reachable false with the reason."""
    try:
        creds = load_pbs_credentials(db, target)
        with PbsClient(creds) as client:
            status = collect_status(client)
    except Exception as exc:  # noqa: BLE001
        status = {"reachable": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}", "checked_at": now().isoformat()}
    target.host_status = status
    target.host_status_at = now()
    db.commit()
    return status


def _set_reboot_flag(db: Session, target: ProtectionTarget, needed: bool) -> None:
    """The wrapper knows best (it compares the running kernel with what is installed); keep the stored status in step."""
    st = dict(target.host_status or {})
    st["reboot_required"] = needed
    target.host_status = st


def check_ssh(db: Session, target: ProtectionTarget) -> dict:
    """Connect over the pinned SSH key and ask the wrapper for its version and status (read-only: nothing is installed or
    changed). Records what it sees so the setup shows READY, and says plainly what is wrong when it cannot."""
    from .host_maintenance_client import (HostMaintenanceClient, HostMaintenanceConnectionError, HostMaintenanceProtocolError,
                                          HostKeyMismatchError, HostKeyNotPinnedError, SUPPORTED_CONTRACT_VERSION)
    cred = db.query(HostMaintenanceCredential).filter(HostMaintenanceCredential.protection_target_id == target.id).one_or_none()
    if cred is None:
        return {"ok": False, "error": "no SSH key has been generated for this server"}
    if not target.ssh_host_key_base64:
        return {"ok": False, "error": "the server's SSH host key is not pinned yet"}
    try:
        with HostMaintenanceClient(load_pbs_ssh_credentials(db, target)) as client:
            version = client.version()
            status = client.status()
    except HostKeyMismatchError:
        return {"ok": False, "error": "the server presented a different SSH host key than the one pinned (reinstalled? possible interception). Disconnect and pin again only if you expected this."}
    except HostKeyNotPinnedError:
        return {"ok": False, "error": "the server's SSH host key is not pinned yet"}
    except (HostMaintenanceConnectionError, HostMaintenanceProtocolError) as exc:
        cred.status = "invalid"
        db.commit()
        return {"ok": False, "error": f"could not run the wrapper: {exc}. Has the host kit been run on the server (bash pyxie-host-kit.sh)?"}
    cred.status = "valid" if version.get("contract_version") == SUPPORTED_CONTRACT_VERSION else "invalid"
    cred.last_validated_at = now()
    cred.last_seen_wrapper_version = version.get("wrapper_version")
    cred.last_seen_contract_version = version.get("contract_version")
    target.wrapper_version = version.get("wrapper_version")
    target.wrapper_checked_at = now()
    _set_reboot_flag(db, target, bool(status.get("reboot_required")))
    db.commit()
    return {"ok": cred.status == "valid", "wrapper_version": version.get("wrapper_version"), "contract_version": version.get("contract_version"),
            "kernel": status.get("kernel_version"), "product_version": status.get("pve_version"), "upgradable": status.get("upgradable_count"),
            "reboot_required": status.get("reboot_required"),
            "error": None if cred.status == "valid" else f"wrapper contract {version.get('contract_version')} is not supported by this PyXie; re-run the host kit"}


def busy_reasons(db: Session, target: ProtectionTarget) -> list[str]:
    """Why this backup server is not idle: a PBS task running, or a backup running on a Proxmox host. Raises when
    what PBS is doing cannot be read, because "cannot tell" is not "idle"."""
    from .models import PveTask
    out: list[str] = []
    with PbsClient(load_pbs_credentials(db, target, "inventory")) as c:
        running = c.tasks(running=True, limit=100)
    if running:
        out.append("PBS is busy: " + ", ".join(f"{t.get('worker_type')} {t.get('worker_id') or ''}".strip() for t in running[:6]) + ". Wait for it to finish.")
    pve = db.query(PveTask).filter(PveTask.status == "running", PveTask.task_type == "vzdump").count()
    if pve:
        out.append(f"{pve} backup(s) are running on Proxmox hosts and may be writing to this server. Wait for them to finish.")
    return out


def status_is_stale(target: ProtectionTarget, minutes: int = 10) -> bool:
    return target.host_status_at is None or target.host_status_at < now() - timedelta(minutes=minutes)
