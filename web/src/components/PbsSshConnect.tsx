"use client";

import { useState } from "react";
import { useMe } from "@/lib/useMe";

export type PbsSsh = { generated: boolean; username?: string | null; status: string | null; host_key_pinned: boolean; fingerprint: string | null; wrapper_version: string | null; expected_wrapper_version: string; wrapper_outdated: boolean };

const btn = (c: string) => `px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border ${c} disabled:opacity-50`;

async function call(method: string, path: string, body?: unknown) {
  const r = await fetch(`/api/pbs/${path}`, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `Request failed (${r.status})`);
  return d;
}

/** "Host Maintenance: Connect a Host" for a Proxmox Backup Server: same keypair, kit, pinned host key and wrapper as a Proxmox node. */
export default function PbsSshConnect({ serverId, serverName, ssh, pending, onChanged }: { serverId: string; serverName: string; ssh: PbsSsh; pending: number | null; onChanged: () => void }) {
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  const [probe, setProbe] = useState<{ key_type: string; key_base64: string; fingerprint: string } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [chk, setChk] = useState<{ ok: boolean; error?: string | null; wrapper_version?: string; kernel?: string; product_version?: string; upgradable?: number; reboot_required?: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<void>) => { setBusy(true); setMsg(null); try { await fn(); } catch (e) { setMsg((e as Error).message); } finally { setBusy(false); } };
  const ready = ssh.generated && ssh.host_key_pinned && !!ssh.wrapper_version;
  const state = !ssh.generated ? "NO KEY" : !ssh.host_key_pinned ? "HOST KEY NOT PINNED" : !ssh.wrapper_version ? "PINNED, WRAPPER NOT SEEN" : "READY";
  if (!ssh.generated) return (
    <div className="text-sm">
      <p className="text-muted mb-2">No host-maintenance credential yet for this server: generate one to start connecting it for real package patching.</p>
      {isAdmin && <button disabled={busy} onClick={() => run(async () => { await call("POST", `servers/${serverId}/ssh/generate`); onChanged(); })} className={btn("border-proxmox hover:bg-proxmox/10")}>{busy ? "Generating…" : "Generate Keypair"}</button>}
      {msg && <div className="text-bad text-xs mt-2">{msg}</div>}
    </div>
  );
  return (
    <div className="text-sm space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="text-xs text-muted">SSH identity: <span className="text-text font-mono">{ssh.username ?? "pyxie-hostmaint"}</span></div>
        {isAdmin && <div className="flex gap-2">
          <button disabled={busy} onClick={() => run(async () => { await call("POST", `servers/${serverId}/ssh/generate?force=true`); onChanged(); })} className={btn("border-warn hover:bg-warn/10")}>Regenerate Keypair</button>
        </div>}
      </div>
      <p className="text-xs text-muted">On the server: run the host script (Setup guide, Connect a backup server, or this server's Access tab), then come back here to read and pin its host key. To fully remove it later, generate the removal script there and click Disconnect below.</p>
      <div className="border border-border rounded px-3 py-2">
        <div className="flex items-center justify-between">
          <span className="text-text font-medium">{serverName}</span>
          <span className={`text-xs ${ready ? "text-good" : "text-warn"}`}>{state}</span>
        </div>
        <div className="text-xs text-muted">{ssh.wrapper_version ? `wrapper ${ssh.wrapper_version}` : "wrapper not seen"}{pending != null ? ` · ${pending} packages pending` : ""}{ssh.host_key_pinned ? ` · host key ${ssh.fingerprint}` : ""}
          {ssh.wrapper_outdated && <span className="text-warn"> · this PyXie ships {ssh.expected_wrapper_version}: re-run the kit</span>}</div>
        {isAdmin && <div className="flex gap-2 mt-2 items-center flex-wrap">
          {!ssh.host_key_pinned && <button disabled={busy} onClick={() => run(async () => setProbe(await call("POST", `servers/${serverId}/ssh/probe`)))} className={btn("border-accent hover:bg-accent/10")}>Read Host Key</button>}
          {probe && <><span className="text-xs">Fingerprint <span className="text-text">{probe.fingerprint}</span>. Compare with <code>ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub</code> on the server.</span>
            <button className={btn("border-good hover:bg-good/10")} onClick={() => run(async () => { await call("PUT", `servers/${serverId}/ssh-host-key`, { key_type: probe.key_type, key_base64: probe.key_base64 }); setProbe(null); onChanged(); })}>Pin it</button></>}
          {ssh.host_key_pinned && <button disabled={busy} className={btn("border-accent hover:bg-accent/10")} onClick={() => run(async () => { setChk(await call("POST", `servers/${serverId}/ssh/check`)); onChanged(); })}>Check Connection</button>}
          {ssh.host_key_pinned && <button disabled={busy} className={btn("border-bad hover:bg-bad/10")} onClick={() => run(async () => { await call("DELETE", `servers/${serverId}/ssh-host-key`); onChanged(); })}>Disconnect</button>}
        </div>}
      </div>
      {chk && (chk.ok ? <div className="text-xs text-good">Connected: wrapper {chk.wrapper_version}, {chk.product_version}, kernel {chk.kernel}, {chk.upgradable} package(s) upgradable{chk.reboot_required ? ", reboot needed" : ""}.</div> : <div className="text-xs text-bad">{chk.error}</div>)}
      {msg && <div className="text-bad text-xs">{msg}</div>}
    </div>
  );
}
