"use client";

import { useCallback, useEffect, useState } from "react";
import { OneLineCommand } from "@/components/WrapperUpdatePanel";
import { useMe } from "@/lib/useMe";

type Status = {
  pve_target_id: string; credential_configured: boolean; host_key_pinned: boolean; host_key_fingerprint?: string | null; provisioned: boolean; reachable: boolean;
  wrapper_version?: string; error?: string; wrapper_state: "no_credential" | "not_set_up" | "not_answering" | "outdated" | "ready"; wrapper_headline: string; wrapper_next: string;
};
type Probe = { key_type: string; key_base64: string; fingerprint: string };

const CHIP: Record<Status["wrapper_state"], [string, string]> = {
  no_credential: ["Needs a key pair", "border-bad text-bad"], not_set_up: ["Not set up", "border-bad text-bad"], not_answering: ["Not answering", "border-warn text-warn"],
  outdated: ["Outdated", "border-warn text-warn"], ready: ["Ready", "border-good text-good"],
};

/** One host's maintenance wrapper: a plain state, and a guided panel to add, repair or update it. PyXie runs nothing on the host: you paste one script
 * as root, then confirm the host's SSH fingerprint by comparing it with what the host itself prints. */
export default function WrapperPanel({ nodeId, nodeName, initiallyOpen = false }: { nodeId: string; nodeName: string; initiallyOpen?: boolean }) {
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  const [st, setSt] = useState<Status | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(initiallyOpen);
  const [probe, setProbe] = useState<Probe | null>(null);
  const [busy, setBusy] = useState(false);

  const check = useCallback(async () => {
    try {
      const r = await fetch(`/api/nodes/${nodeId}/host-maintenance-status`);
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `Could not check (${r.status})`);
      setSt(d as Status); setErr(null);
    } catch (e) { setErr((e as Error).message); }
  }, [nodeId]);
  useEffect(() => { check(); }, [check]);
  // while the panel is open and the host is not ready, look again every few seconds (picks up an upgrade of an already trusted host by itself)
  useEffect(() => {
    if (!open || st?.wrapper_state === "ready") return;
    const t = setInterval(check, 5000);
    return () => clearInterval(t);
  }, [open, st?.wrapper_state, check]);

  async function probeKey() {
    setBusy(true); setErr(null);
    try {
      const r = await fetch(`/api/nodes/${nodeId}/ssh-host-key/probe`, { method: "POST" });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `Could not reach the host (${r.status})`);
      setProbe(d as Probe);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  async function trust() {
    if (!probe) return;
    setBusy(true); setErr(null);
    try {
      const r = await fetch(`/api/nodes/${nodeId}/ssh-host-key`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key_type: probe.key_type, key_base64: probe.key_base64 }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `Could not trust it (${r.status})`);
      setProbe(null); await check();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  if (!st) return <div className="text-xs text-muted">{err ?? "Checking the maintenance wrapper…"}</div>;
  const [label, cls] = CHIP[st.wrapper_state];
  const ready = st.wrapper_state === "ready";
  const action = st.wrapper_state === "outdated" ? "Update the wrapper…" : st.wrapper_state === "not_answering" ? "Repair the wrapper…" : "Add the wrapper…";

  return (
    <div className="border border-border rounded p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted">Maintenance wrapper</span>
        <span className={`text-[10px] px-1.5 py-0.5 rounded border ${cls}`}>{label}</span>
        <span className="text-xs text-muted">{st.wrapper_headline}</span>
        <span className="ml-auto flex gap-2">
          {isAdmin && st.wrapper_state !== "no_credential" && !ready && !open && <button type="button" onClick={() => setOpen(true)} className="px-2 py-1 rounded border border-accent text-accent text-xs hover:bg-accent/10">{action}</button>}
          <button type="button" onClick={check} className="px-2 py-1 rounded border border-border text-xs text-muted hover:text-text">Re-check</button>
        </span>
      </div>
      {!ready && st.wrapper_next && <div className="text-xs text-muted mt-1">{st.wrapper_next}{st.wrapper_state === "no_credential" && <> <a className="text-accent hover:underline" href="/platform/credentials">Open Credentials</a></>}</div>}
      {err && <div className="text-xs text-bad mt-1">{err}</div>}

      {open && isAdmin && st.wrapper_state !== "no_credential" && (
        <div className="mt-3 space-y-4 text-xs">
          <div className="text-muted">About two minutes. PyXie runs nothing on {nodeName}: you paste one script as root. It adds one locked-down user, one SSH key and a short list of exact commands PyXie may run, and reboots nothing.</div>

          <div><div className="font-medium text-text mb-0.5">1. Open a root shell on {nodeName}</div>
            <div className="text-muted">Easiest: in the Proxmox web page choose this host, then <b>Shell</b>. Or SSH in as root. The prompt should end in <code>#</code>.</div></div>

          <div><div className="font-medium text-text mb-0.5">2. Paste this one line and press Enter</div>
            <OneLineCommand targetId={st.pve_target_id} />
            <div className="text-muted mt-1">Success looks like: <code>OK: {nodeName} is provisioned for PyXie.</code> (or <i>was already set up; the wrapper is now upgraded</i>). Prefer to read it first? <a className="text-accent hover:underline" href={`/api/pve-targets/${st.pve_target_id}/host-kit.sh`}>Download the installer</a> and run it yourself.</div>
          </div>

          <div><div className="font-medium text-text mb-0.5">3. Confirm {nodeName}&apos;s fingerprint</div>
            {st.host_key_pinned
              ? <div className="text-muted">A fingerprint is already trusted ({st.host_key_fingerprint}). PyXie is checking again every few seconds for the wrapper.</div>
              : !probe ? (
                <div className="space-y-1"><div className="text-muted">When the script has finished, press this. PyXie fetches the fingerprint the host presents.</div>
                  <button type="button" disabled={busy} onClick={probeKey} className="px-2 py-1 rounded bg-ink text-on-ink border border-accent disabled:opacity-50">{busy ? "Contacting the host…" : "It finished: get the fingerprint"}</button></div>
              ) : (
                <div className="space-y-1.5">
                  <pre className="font-mono bg-canvas/60 border border-border rounded p-2 break-all whitespace-pre-wrap">{probe.fingerprint} ({probe.key_type})</pre>
                  <div className="text-muted">In the same root shell run <code>ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub</code> and compare. If they are the same, trust it.</div>
                  <div className="flex gap-2"><button type="button" disabled={busy} onClick={trust} className="px-2 py-1 rounded bg-ink text-on-ink border border-good disabled:opacity-50">They match: trust this host</button>
                    <button type="button" onClick={() => setProbe(null)} className="text-muted hover:text-text">They differ</button></div>
                  <div className="text-bad">If they differ, do not trust it: something other than {nodeName} is answering at that address.</div>
                </div>
              )}
          </div>

          {ready && <div className="rounded border border-good/50 bg-good/10 p-2 text-good">✓ {nodeName} is ready. Patching, reboots and stale-lock clearing are available for this host. <button type="button" className="ml-2 text-muted hover:text-text" onClick={() => setOpen(false)}>Close</button></div>}

          <details><summary className="cursor-pointer text-text">Something went wrong?</summary>
            <table className="w-full mt-1"><thead><tr className="text-left text-muted"><th className="py-1 pr-3">You see</th><th className="pr-3">It means</th><th>Do this</th></tr></thead><tbody>
              {[
                ["curl: (60) SSL certificate problem", "The host does not trust PyXie's certificate", "Tick the box in step 2 and copy the script again"],
                ["curl: (6) Could not resolve host", "The host cannot look up PyXie's name", "Check the host's DNS (its Config tab shows it)"],
                ["404, or the link is gone", "The link ran out", "Press “make a new one”"],
                ["checksum mismatch / FAILED", "The file changed on the way: the safety check worked", "Do not run it. Make a new script; if it repeats, stop and investigate"],
                ["Run as root", "You are not root in that shell", "Run sudo -i, then paste again"],
                ["sudo missing and apt cannot refresh", "No Proxmox subscription but the enterprise repository is enabled", "Disable that repository as the script explains, then paste again"],
                ["“could not reach” on step 3", "PyXie cannot open SSH to the host's management address", "Check the address and firewall; the host page shows what PyXie has on record"],
              ].map(([a, b, c]) => <tr key={a} className="border-t border-border align-top"><td className="py-1 pr-3"><code>{a}</code></td><td className="pr-3 text-muted">{b}</td><td>{c}</td></tr>)}
            </tbody></table>
          </details>
        </div>
      )}
    </div>
  );
}
