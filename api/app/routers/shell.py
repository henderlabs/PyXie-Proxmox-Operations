"""Embedded host shell: a ticketed websocket proxy to PVE's node terminal (termproxy).

Flow (see docs/host-shell.md): an admin types the host name and POSTs /api/nodes/{id}/shell; PyXie asks
PVE for a termproxy session with the 'shell' token (the dedicated token) and
keeps PVE's ticket server-side in Redis behind a one-time 30 s PyXie ticket. The browser then opens
wss://<host>/shell-ws/<ticket> (Caddy sends that path straight here) and this module pipes the terminal
frames to PVE. The session is re-checked on connect and every 60 s, closes after the idle limit and after
4 hours, and only the open and close are audited (what is typed is never recorded).
"""

import asyncio
import hashlib
import json
import os
import ssl
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

import redis
import websockets
import websockets.asyncio.client
import websockets.exceptions
from fastapi import APIRouter, Depends, Header, HTTPException, WebSocket
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pyxie_core import console_tickets as ct
from pyxie_core.audit import write_audit_event
from pyxie_core.credentials import CredentialNotConfigured, load_pve_credentials
from pyxie_core.db import SessionLocal
from pyxie_core.models import AppSettings, Cluster, Node, PveTarget, Session as SessionModel, User, Workload
from pyxie_core.pve_write_client import PveConsoleClient

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db

router = APIRouter(tags=["shell"])

IDLE_CHOICES = (5, 60)
HANDSHAKE_SECONDS = 8

_redis = None


def _r():
    global _redis
    if _redis is None:
        _redis = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    return _redis


def _key(ticket: str) -> str:
    return "pyxie:shell:ticket:" + hashlib.sha256(ticket.encode()).hexdigest()


def store_ticket(r, ticket: str, payload: dict) -> None:
    r.set(_key(ticket), json.dumps(payload), ex=ct.TICKET_TTL_SECONDS)


def consume_ticket(r, ticket: str) -> Optional[dict]:
    if not ticket or len(ticket) > 128:
        return None
    raw = r.getdel(_key(ticket))
    return json.loads(raw) if raw else None


def idle_seconds(minutes) -> int:
    lo, hi = IDLE_CHOICES
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        m = 15
    return max(lo, min(hi, m)) * 60


def frame_is_input(msg) -> bool:
    """PVE's terminal framing: '0:<len>:<data>' is typing, '1:<cols>:<rows>:' a resize, '2' a ping.
    Only typing counts as activity for the idle limit."""
    return isinstance(msg, str) and msg.startswith("0:")


def shell_state(*, is_admin: bool, has_permission: bool = True, enabled: bool, has_credential: bool, node_missing: bool, node_offline: bool,
                in_maintenance: bool, require_maintenance: bool) -> str:
    if not is_admin:
        return "admin_only"
    if not has_permission:
        return "no_permission"
    if not enabled:
        return "disabled"
    if node_missing or node_offline:
        return "unavailable"
    if not has_credential:
        return "no_credential"
    if require_maintenance and not in_maintenance:
        return "needs_maintenance"
    return "ready"


def _settings(db: Session) -> AppSettings:
    return db.query(AppSettings).filter(AppSettings.id == 1).one()


def _node_ctx(db: Session, node_id):
    node = db.query(Node).filter(Node.id == node_id).one_or_none()
    if node is None:
        raise HTTPException(404, "host not found")
    cluster = db.query(Cluster).filter(Cluster.id == node.cluster_id).one_or_none()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one_or_none() if cluster else None
    if target is None:
        raise HTTPException(409, "the PVE target for this host is not known to PyXie")
    return node, cluster, target


def _shell_creds(db: Session, target: PveTarget):
    """Only the dedicated 'shell' token (Sys.Console, nothing else): no shell token, no shell. The maintenance
    token is deliberately never used, so shell power cannot leak through the credential that does maintenance."""
    return load_pve_credentials(db, target, "shell"), "shell"


def _has_credential(db: Session, target: PveTarget) -> Optional[str]:
    try:
        return _shell_creds(db, target)[1]
    except CredentialNotConfigured:
        return None


@router.get("/api/shell/status", dependencies=[Depends(require_admin)])
def shell_token_status(db: Session = Depends(get_db)):
    """For the Settings card: per PVE target, is a shell token saved and did its last test pass."""
    from pyxie_core.models import PveCredential
    out = []
    for t in db.query(PveTarget).order_by(PveTarget.name).all():
        c = db.query(PveCredential).filter(PveCredential.pve_target_id == t.id, PveCredential.slot_name == "shell").one_or_none()
        out.append({
            "target_id": str(t.id), "target": t.name, "saved": c is not None,
            "status": c.status if c else None,
            "token": f"{c.token_user}!{c.token_id}" if c else None,
        })
    return out


@router.get("/api/nodes/{node_id}/shell-info")
def shell_info(node_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    node, _cluster, target = _node_ctx(db, node_id)
    s = _settings(db)
    using = _has_credential(db, target)
    running = (
        db.query(Workload)
        .filter(Workload.node_id == node.id, Workload.is_missing.is_(False), Workload.status == "running")
        .count()
    )
    return {
        "node": node.name,
        "state": shell_state(
            is_admin=bool(user.is_admin), has_permission=bool(user.can_use_shell), enabled=bool(s.shell_enabled), has_credential=using is not None,
            node_missing=bool(node.is_missing), node_offline=node.status == "offline",
            in_maintenance=bool(node.maintenance_mode), require_maintenance=bool(s.shell_require_maintenance)),
        "credential": using,
        "in_maintenance": bool(node.maintenance_mode),
        "require_maintenance": bool(s.shell_require_maintenance),
        "running_guests": running,
        "idle_minutes": idle_seconds(s.shell_idle_minutes) // 60,
    }


class ShellRequest(BaseModel):
    confirm_name: str = ""


def _audit_denied(db: Session, user: User, node: Node, cluster: Cluster, why: str):
    write_audit_event(
        db, event_category="shell", event_type="shell.denied", actor=user.email, actor_type="user",
        node_id=node.id, cluster_id=cluster.id, result="failure", severity="warning", error=why[:300])


@router.post("/api/nodes/{node_id}/shell", dependencies=[Depends(require_admin)])
def create_shell_ticket(
    node_id: uuid.UUID, body: ShellRequest,
    user: User = Depends(get_current_user),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    node, cluster, target = _node_ctx(db, node_id)
    s = _settings(db)
    if not user.can_use_shell:
        _audit_denied(db, user, node, cluster, "no host shell permission")
        raise HTTPException(403, "You do not have the host shell permission. An admin can grant it on the Users page.")
    if not s.shell_enabled:
        _audit_denied(db, user, node, cluster, "host shell switched off")
        raise HTTPException(403, "The host shell is switched off. An admin can enable it in Settings.")
    if node.is_missing or node.status == "offline":
        raise HTTPException(409, "The host is not answering, so there is no shell to open.")
    if body.confirm_name.strip() != node.name:
        raise HTTPException(400, "Type the host name exactly to open the shell.")
    if s.shell_require_maintenance and not node.maintenance_mode:
        _audit_denied(db, user, node, cluster, "host not in maintenance mode")
        raise HTTPException(409, "Settings allow the shell only while the host is in maintenance mode.")
    try:
        creds, _slot = _shell_creds(db, target)
    except CredentialNotConfigured:
        raise HTTPException(409, "No shell token is saved for this PVE target. An admin can set it up under Settings > Setup guide.")
    if not ct.ticket_rate_ok(_r(), "shell:" + str(user.id)):
        raise HTTPException(429, "Too many shell requests. Wait a minute and try again.")
    try:
        with PveConsoleClient(creds) as client:
            proxy = client.termproxy(node.name)
    except Exception as exc:  # noqa: BLE001
        _audit_denied(db, user, node, cluster, str(exc))
        raise HTTPException(502, f"PVE refused the shell request: {str(exc)[:200]}")

    ticket = ct.new_ticket()
    store_ticket(_r(), ticket, {
        "user_id": str(user.id),
        "session_id": (authorization or "").removeprefix("Bearer ").strip(),
        "node_id": str(node.id), "node": node.name,
        "host": proxy["host"], "api_port": proxy["api_port"], "port": proxy["port"],
        "ticket": proxy["ticket"], "pve_user": proxy["user"],
    })
    write_audit_event(
        db, event_category="shell", event_type="shell.requested", actor=user.email, actor_type="user",
        node_id=node.id, cluster_id=cluster.id, metadata={"node": node.name})
    return {
        "ws_path": f"/shell-ws/{ticket}", "expires_in": ct.TICKET_TTL_SECONDS,
        "idle_seconds": idle_seconds(s.shell_idle_minutes), "max_session_seconds": ct.MAX_SESSION_SECONDS,
    }


def _revalidate(session_id: str, user_id: str, node_id: str) -> tuple[bool, str, dict]:
    """Everything that must still be true for a shell to stay open. Returns (ok, reason, ctx)."""
    db = SessionLocal()
    try:
        s = db.query(SessionModel).filter(SessionModel.id == session_id).one_or_none()
        if s is None or s.expires_at < datetime.now(timezone.utc):
            return False, "session ended", {}
        user = db.query(User).filter(User.id == s.user_id, User.is_active.is_(True)).one_or_none()
        if user is None or str(user.id) != user_id or not user.is_admin:
            return False, "not authorised", {}
        if not user.can_use_shell:
            return False, "host shell permission removed", {}
        st = _settings(db)
        if not st.shell_enabled:
            return False, "host shell switched off", {}
        node = db.query(Node).filter(Node.id == node_id).one_or_none()
        if node is None or node.is_missing:
            return False, "host not available", {}
        if st.shell_require_maintenance and not node.maintenance_mode:
            return False, "host left maintenance mode", {}
        cluster = db.query(Cluster).filter(Cluster.id == node.cluster_id).one_or_none()
        target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one_or_none()
        creds, _slot = _shell_creds(db, target)
        return True, "", {
            "email": user.email, "node_id": node.id, "cluster_id": cluster.id,
            "auth": f"PVEAPIToken={creds.token_user}!{creds.token_id}={creds.token_secret}",
            "tls_verify": target.tls_verify, "idle_seconds": idle_seconds(st.shell_idle_minutes),
        }
    except CredentialNotConfigured:
        return False, "shell credential removed", {}
    finally:
        db.close()


def _audit(**kw):
    db = SessionLocal()
    try:
        write_audit_event(db, event_category="shell", actor_type="user", **kw)
    finally:
        db.close()


@router.websocket("/shell-ws/{ticket}")
async def shell_ws(websocket: WebSocket, ticket: str):
    if not ct.origin_allowed(websocket.headers.get("origin"), websocket.headers.get("host")):
        await websocket.close(code=1008)
        return
    payload = await run_in_threadpool(consume_ticket, _r(), ticket)
    if payload is None:
        await websocket.close(code=1008)
        return
    ok, reason, ctx = await run_in_threadpool(_revalidate, payload["session_id"], payload["user_id"], payload["node_id"])
    if not ok:
        await websocket.close(code=1008, reason=reason)
        return
    conn_id = uuid.uuid4().hex
    slot_user = "shell:" + payload["user_id"]
    if not await run_in_threadpool(ct.acquire_slot, _r(), slot_user, conn_id):
        await websocket.close(code=1013, reason="too many open shells")
        return

    started = time.monotonic()
    last_input = [started]
    sent = {"to_pve": 0, "to_browser": 0}
    end_reason = "closed"
    common = dict(actor=ctx["email"], node_id=ctx["node_id"], cluster_id=ctx["cluster_id"])
    try:
        url = (f"wss://{payload['host']}:{payload['api_port']}/api2/json/nodes/{quote(payload['node'])}/"
               f"vncwebsocket?port={int(payload['port'])}&vncticket={quote(payload['ticket'], safe='')}")
        sslctx = ssl.create_default_context()
        if not ctx["tls_verify"]:
            sslctx.check_hostname = False
            sslctx.verify_mode = ssl.CERT_NONE
        try:
            upstream = await websockets.asyncio.client.connect(
                url, additional_headers={"Authorization": ctx["auth"]}, subprotocols=["binary"],
                ssl=sslctx, max_size=ct.MAX_CLIENT_MESSAGE_BYTES * 4, open_timeout=10, ping_interval=None)
        except Exception as exc:  # noqa: BLE001
            end_reason = f"pve connect failed: {str(exc)[:120]}"
            await websocket.close(code=1011, reason="could not reach the shell on PVE")
            return
        # PVE wants "user:ticket\n" first and answers "OK" before the terminal starts.
        first_extra = b""
        try:
            await upstream.send(f"{payload['pve_user']}:{payload['ticket']}\n")
            hello = await asyncio.wait_for(upstream.recv(), HANDSHAKE_SECONDS)
            raw = hello.encode() if isinstance(hello, str) else hello
            if not raw.startswith(b"OK"):
                raise ValueError("PVE did not accept the shell ticket")
            first_extra = raw[2:]
        except Exception as exc:  # noqa: BLE001
            end_reason = f"pve handshake failed: {str(exc)[:120]}"
            await upstream.close()
            await websocket.close(code=1011, reason="PVE did not open the shell")
            return
        offered = websocket.scope.get("subprotocols") or []
        await websocket.accept(subprotocol="binary" if "binary" in offered else None)
        if first_extra:
            await websocket.send_bytes(first_extra)
        await run_in_threadpool(lambda: _audit(event_type="shell.opened", metadata={"node": payload["node"]}, **common))

        async def browser_to_pve():
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    return "browser closed"
                data = msg.get("bytes") if msg.get("bytes") is not None else msg.get("text")
                if data is None:
                    continue
                if len(data) > ct.MAX_CLIENT_MESSAGE_BYTES:
                    return "message too large"
                if frame_is_input(data):
                    last_input[0] = time.monotonic()
                sent["to_pve"] += len(data)
                await upstream.send(data)

        async def pve_to_browser():
            try:
                async for data in upstream:
                    sent["to_browser"] += len(data)
                    if isinstance(data, bytes):
                        await websocket.send_bytes(data)
                    else:
                        await websocket.send_text(data)
            except websockets.exceptions.ConnectionClosed:
                pass
            return "shell exited"

        async def watchdog():
            checked = started
            while True:
                await asyncio.sleep(10)
                now = time.monotonic()
                if now - started > ct.MAX_SESSION_SECONDS:
                    return "time limit reached"
                if now - last_input[0] > ctx["idle_seconds"]:
                    return "idle"
                if now - checked >= 60:
                    checked = now
                    good, why, fresh = await run_in_threadpool(
                        _revalidate, payload["session_id"], payload["user_id"], payload["node_id"])
                    if not good:
                        return why
                    ctx["idle_seconds"] = fresh["idle_seconds"]

        tasks = [asyncio.create_task(f()) for f in (browser_to_pve, pve_to_browser, watchdog)]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for t in done:
            exc = t.exception()
            end_reason = str(t.result()) if exc is None else f"error: {type(exc).__name__}"
        await upstream.close()
        try:
            await websocket.close(code=1000, reason=end_reason[:100])
        except Exception:  # noqa: BLE001
            pass
    finally:
        await run_in_threadpool(ct.release_slot, _r(), slot_user, conn_id)
        await run_in_threadpool(
            lambda: _audit(event_type="shell.closed", metadata={
                "node": payload["node"], "reason": end_reason, "seconds": int(time.monotonic() - started),
                "bytes_to_pve": sent["to_pve"], "bytes_to_browser": sent["to_browser"]}, **common))
