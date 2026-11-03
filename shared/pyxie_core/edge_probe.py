"""Is the Caddy edge proxy routing the embedded-console websocket to the API?

Caddy bind-mounts the ops/caddy DIRECTORY. When an update replaces that directory the running container keeps the old
(deleted) inode: it sees an empty /etc/caddy and serves its in-memory config from before the update -- without the
/console-ws route -- so the console websocket falls through to the web layer and fails with a 502. The route is healthy
when a websocket request with a made-up ticket reaches the API, which refuses it with 403 (no such ticket).

Standard library only: the host updater imports this too.
"""

import os
import socket
import ssl

PROBE_PATH = "/console-ws/pyxie-route-probe"
CADDY_SERVICE = "pyxie-manager-caddy"


def probe_console_route(connect_to: str, hostname: str, *, port: int = 443, timeout: float = 5.0) -> int | None:
    """HTTP status Caddy answers for a websocket upgrade on a fake console ticket, or None when Caddy cannot be reached.
    `connect_to` is where to open the TCP connection (127.0.0.1 on the host, the Caddy container from the app);
    `hostname` is the site name Caddy serves (its TLS name and Host header). Certificate trust is not checked: this
    only asks which backend answers, and sends no credentials."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = (
        f"GET {PROBE_PATH} HTTP/1.1\r\nHost: {hostname}\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n"
        "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
    ).encode()
    try:
        with socket.create_connection((connect_to, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=hostname) as tls:
                tls.settimeout(timeout)
                tls.sendall(req)
                line = b""
                while b"\r\n" not in line and len(line) < 512:
                    chunk = tls.recv(256)
                    if not chunk:
                        break
                    line += chunk
        parts = line.split(b"\r\n", 1)[0].split()
        return int(parts[1]) if len(parts) >= 2 and parts[0].startswith(b"HTTP/") else None
    except (OSError, ValueError):
        return None


def console_route_ok(status: int | None) -> bool:
    return status == 403


def probe_from_app() -> int | None:
    """Probe through the Caddy container from the API/worker. None when this is not a Docker install or no hostname is set."""
    hostname = os.environ.get("PYXIE_HOSTNAME", "").strip()
    if not hostname:
        return None
    try:
        socket.gethostbyname(CADDY_SERVICE)
    except OSError:
        return None  # native install: there is no Caddy container to ask
    return probe_console_route(CADDY_SERVICE, hostname)


def tls_mode() -> str:
    """How the Caddy edge gets its certificate: internal (self-signed by Caddy's own CA), file, acme -- or "" when this is
    not a Docker install. Used to tell operators that a node will not trust the certificate (internal) up front."""
    try:
        socket.gethostbyname(CADDY_SERVICE)
    except OSError:
        return ""
    return os.environ.get("PYXIE_TLS_MODE", "").strip() or "internal"


RENEW_ALARM_HOURS = 12.0  # never alarm earlier than this many hours left
RENEW_ALARM_FRACTION = 0.15  # Caddy renews with a third of the lifetime left; alarm only when well past that: under 15% of the lifetime


def leaf_validity(connect_to: str, hostname: str, *, port: int = 443, timeout: float = 5.0):
    """(not_before, not_after) of the certificate the edge presents (UTC datetimes), or None when it cannot be read. Trust is not checked: this only
    reads the certificate. Needs the `cryptography` package (the app and worker have it; the host updater does not use this)."""
    from datetime import timezone
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((connect_to, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=hostname) as tls:
                der = tls.getpeercert(binary_form=True)
        from cryptography import x509
        cert = x509.load_der_x509_certificate(der)
        na = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(tzinfo=timezone.utc)
        nb = getattr(cert, "not_valid_before_utc", None) or cert.not_valid_before.replace(tzinfo=timezone.utc)
        return nb, na
    except Exception:  # noqa: BLE001
        return None


def expiry_state() -> tuple[float, float] | None:
    """(hours left, total lifetime in hours) of the edge's certificate, or None when not a Docker install, no hostname is set, or it cannot be read."""
    hostname = os.environ.get("PYXIE_HOSTNAME", "").strip()
    if not hostname or not tls_mode():
        return None
    from datetime import datetime, timezone
    v = leaf_validity(CADDY_SERVICE, hostname)
    if v is None:
        return None
    return (v[1] - datetime.now(timezone.utc)).total_seconds() / 3600, (v[1] - v[0]).total_seconds() / 3600


def renewal_overdue(hours_left: float, lifetime_hours: float) -> bool:
    """Pure. Caddy renews a certificate with about a third of its lifetime left; this is true only when it is far past that."""
    return hours_left < min(RENEW_ALARM_HOURS, RENEW_ALARM_FRACTION * lifetime_hours)
