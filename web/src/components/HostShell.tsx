"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import "@xterm/xterm/css/xterm.css";

type ShellState = "ready" | "admin_only" | "no_permission" | "disabled" | "unavailable" | "no_credential" | "needs_maintenance";

type ShellInfo = {
  node: string;
  state: ShellState;
  credential: "shell" | null;
  in_maintenance: boolean;
  require_maintenance: boolean;
  running_guests: number;
  idle_minutes: number;
};

const REFUSAL: Record<Exclude<ShellState, "ready">, string> = {
  admin_only: "The host shell is for admin accounts.",
  no_permission: "You do not have the host shell permission. An admin can grant it on the Users page.",
  disabled: "The host shell is switched off. An admin can turn it on under Settings. Until then PyXie behaves exactly as before.",
  unavailable: "This host is not answering, so there is no shell to open.",
  no_credential: "No shell token is saved. An admin can set it up under Settings > Setup guide (Host shell token).",
  needs_maintenance: "Settings allow the shell only while the host is in maintenance mode. Put this host in maintenance mode first.",
};

type Phase = "idle" | "connecting" | "connected" | "ended";

/** Root shell on a Proxmox host, through PyXie's ticketed websocket proxy (api/app/routers/shell.py).
 * Mounted only while the Shell tab is open; leaving the tab closes the session. The server enforces
 * the idle limit, the 4 h cap and the session checks; this component only draws the terminal. */
export default function HostShell({ nodeId, nodeName }: { nodeId: string; nodeName: string }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const termElRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<any>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const pingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [info, setInfo] = useState<ShellInfo | null>(null);
  const [typed, setTyped] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [openedAt, setOpenedAt] = useState<Date | null>(null);

  useEffect(() => {
    let alive = true;
    fetch(`/api/nodes/${nodeId}/shell-info`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (alive) setInfo(d); })
      .catch(() => {});
    return () => { alive = false; };
  }, [nodeId]);

  const teardown = useCallback(() => {
    if (pingRef.current) clearInterval(pingRef.current);
    pingRef.current = null;
    try { wsRef.current?.close(); } catch { /* already closed */ }
    wsRef.current = null;
    try { termRef.current?.dispose(); } catch { /* already gone */ }
    termRef.current = null;
  }, []);

  useEffect(() => teardown, [teardown]);

  async function connect() {
    setPhase("connecting");
    setMessage(null);
    try {
      const res = await fetch(`/api/nodes/${nodeId}/shell`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm_name: typed }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
      const [{ Terminal }, { FitAddon }] = await Promise.all([import("@xterm/xterm"), import("@xterm/addon-fit")]);
      const term = new Terminal({
        cursorBlink: true, fontSize: 13, scrollback: 5000,
        fontFamily: "ui-monospace, Menlo, Consolas, monospace",
        theme: { background: "#000000", foreground: "#d8dde8" },
      });
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.open(termElRef.current!);
      fit.fit();
      termRef.current = term;

      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${window.location.host}${data.ws_path}`, ["binary"]);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;
      const enc = new TextEncoder();
      const sendResize = () => {
        if (ws.readyState === WebSocket.OPEN) ws.send(`1:${term.cols}:${term.rows}:`);
      };
      ws.onopen = () => {
        setPhase("connected");
        setOpenedAt(new Date());
        sendResize();
        term.focus();
        pingRef.current = setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send("2"); }, 30000);
      };
      ws.onmessage = (e) => term.write(typeof e.data === "string" ? e.data : new Uint8Array(e.data));
      ws.onclose = (e) => {
        if (pingRef.current) clearInterval(pingRef.current);
        pingRef.current = null;
        setPhase("ended");
        setMessage(prettyEnd(e.reason, e.wasClean));
      };
      term.onData((d: string) => { if (ws.readyState === WebSocket.OPEN) ws.send(`0:${enc.encode(d).length}:${d}`); });
      term.onResize(sendResize);
      const onWin = () => { try { fit.fit(); } catch { /* not visible */ } };
      window.addEventListener("resize", onWin);
      document.addEventListener("fullscreenchange", onWin);
    } catch (e) {
      teardown();
      setPhase("ended");
      setMessage((e as Error).message);
    }
  }

  function closeSession() {
    teardown();
    setPhase("ended");
    setMessage("Shell closed.");
  }

  function copySelection() {
    const sel = termRef.current?.getSelection?.();
    if (sel) navigator.clipboard?.writeText(sel).catch(() => {});
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else wrapRef.current?.requestFullscreen?.();
  }

  if (!info) return <div className="text-sm text-muted">Checking whether a shell can be opened…</div>;

  const live = phase === "connecting" || phase === "connected";
  const refusal = info.state === "ready" ? null : REFUSAL[info.state];

  return (
    <div ref={wrapRef} className="space-y-3">
      {!live && refusal && (
        <div className="rounded border border-border p-3 text-sm">
          <b className="text-text">No shell right now.</b> <span className="text-muted">{refusal}</span>
        </div>
      )}

      {!live && !refusal && (
        <div className="rounded border border-warn/50 bg-warn/5 p-3 text-sm space-y-2">
          <div>
            <b className="text-text">This opens a terminal on {nodeName}, at its login prompt.</b>{" "}
            <span className="text-muted">
              Proxmox does not sign you in for you: type a Linux account on the host (for example root) and its
              password. Once you are in, nothing you type has a preview, an approval or an undo. PyXie records who
              opened it and when it closed, not what you typed, and it does not store the password.
            </span>
          </div>
          <div className="text-muted">
            Guests running on this host: <b className="text-text">{info.running_guests}</b>{" "}
            {info.in_maintenance ? (
              <span className="text-[11px] px-2 py-0.5 rounded-full border border-good/50 text-good">in maintenance mode</span>
            ) : (
              <span className="text-[11px] px-2 py-0.5 rounded-full border border-warn/60 text-warn">not in maintenance mode</span>
            )}
          </div>
          <form
            className="flex flex-wrap items-center gap-2"
            onSubmit={(e) => { e.preventDefault(); if (typed === nodeName) void connect(); }}
          >
            <label className="text-muted" htmlFor="shell-confirm">Type the host name to open it:</label>
            <input
              id="shell-confirm" className="input w-56" autoComplete="off" value={typed}
              onChange={(e) => setTyped(e.target.value)} placeholder={nodeName}
            />
            <button type="submit" disabled={typed !== nodeName} className="px-3 py-1.5 rounded border border-accent bg-accent/10 text-text text-sm hover:bg-accent/20 disabled:opacity-40">
              Open shell
            </button>
          </form>
          <div className="text-[11px] text-muted">
            Closes by itself after {info.idle_minutes} minutes without typing, and after 4 hours in any case.
          </div>
        </div>
      )}

      {message && !live && <div className="text-sm text-muted">{message}</div>}

      {live && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <span>
              <span className={`text-[11px] px-2 py-0.5 rounded-full border ${phase === "connected" ? "border-good/50 text-good" : "border-border text-muted"}`}>
                {phase === "connected" ? "connected" : "connecting…"}
              </span>{" "}
              <span className="text-muted">{nodeName}{openedAt ? ` · opened ${openedAt.toLocaleTimeString()}` : ""}</span>
            </span>
            <span className="flex gap-2">
              <button type="button" className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface" onClick={copySelection}>Copy selection</button>
              <button type="button" className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface" onClick={toggleFullscreen}>Full screen</button>
              <button type="button" className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface text-bad" onClick={closeSession}>Close session</button>
            </span>
          </div>
          <div className="text-[11px] text-muted">
            Closes after {info.idle_minutes} minutes without typing, when you sign out, or if an admin switches the shell off.
          </div>
        </div>
      )}
      <div
        ref={termElRef}
        className={live ? "rounded border border-border bg-black p-2" : "hidden"}
        style={{ height: "max(420px, calc(100vh - 360px))" }}
      />
    </div>
  );
}

function prettyEnd(reason: string, clean: boolean): string {
  const map: Record<string, string> = {
    idle: "Closed: no typing for a while.",
    "time limit reached": "Closed: the 4 hour limit was reached.",
    "shell exited": "The shell ended (you left it).",
    "browser closed": "Shell closed.",
    "session ended": "Closed: your PyXie session ended.",
    "host shell switched off": "Closed: an admin switched the host shell off.",
    "host left maintenance mode": "Closed: the host left maintenance mode.",
  };
  if (reason && map[reason]) return map[reason];
  if (reason) return `Shell closed: ${reason}.`;
  return clean ? "Shell closed." : "The shell connection ended or could not be opened.";
}
