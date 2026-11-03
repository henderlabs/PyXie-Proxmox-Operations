"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { hostOneLiner, type Link } from "@/lib/hostScripts";

type Host = { id: string; name: string; wrapper_version: string | null; state: "ready" | "outdated" | "not_set_up" | "not_answering" };
type HostList = { expected: string; hosts: Host[] };

/** The one line to paste in a root shell on a host: it downloads the installer from this PyXie, checks it against the fingerprint printed in the line, and runs it. */
export function OneLineCommand({ targetId, onLink }: { targetId: string; onLink?: (l: Link | null) => void }) {
  const [link, setLink] = useState<Link | null>(null);
  const [trusts, setTrusts] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const origin = typeof window !== "undefined" ? window.location.origin : "";

  const make = useCallback(async () => {
    try {
      const r = await fetch(`/api/pve-targets/${targetId}/host-kit/link`, { method: "POST" });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `Could not make the command (${r.status})`);
      setLink(d as Link); onLink?.(d as Link); setErr(null);
    } catch (e) { setErr((e as Error).message); }
  }, [targetId, onLink]);
  useEffect(() => { make(); const t = setInterval(make, 20 * 60_000); return () => clearInterval(t); }, [make]); // renewed while shown (a link lasts 30 minutes)

  if (err) return <div className="text-xs text-bad">{err}</div>;
  if (!link) return <div className="text-xs text-muted">Making the command…</div>;
  const line = hostOneLiner({ origin, link, insecure: !trusts });
  return (
    <div className="space-y-1.5">
      <div className="relative">
        <pre className="text-xs font-mono bg-canvas/60 border border-border rounded p-2 pr-20 overflow-x-auto whitespace-pre-wrap break-all">{line}</pre>
        <button type="button" className="absolute top-1.5 right-1.5 px-2 py-0.5 rounded border border-accent text-accent text-[11px] hover:bg-accent/10"
          onClick={async () => { try { await navigator.clipboard.writeText(line); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* blocked */ } }}>{copied ? "Copied" : "Copy the line"}</button>
      </div>
      <div className="text-[11px] text-muted"><b>In plain words:</b> it downloads the update from this PyXie, checks that it matches the fingerprint printed in the line (the line comes from this page, so a changed file is refused and nothing runs), then runs it. It ends with <code>OK</code> and reboots nothing. The same line works on every host; it stays valid while this page is open.</div>
      <label className="flex items-center gap-1.5 text-[11px] text-muted"><input type="checkbox" checked={trusts} onChange={(e) => setTrusts(e.target.checked)} />My hosts already trust PyXie&apos;s certificate (leaves out <code>-k</code>, which skips the certificate check; the fingerprint check protects the download either way)</label>
    </div>
  );
}

export const WRAPPER_TROUBLE: [string, string, string][] = [
  ["checksum ... FAILED", "The download is not the file this page expects. Nothing ran.", "Copy the line again (this page renews it). If it repeats, stop and investigate."],
  ["curl: (60) SSL certificate problem", "The host does not trust PyXie's certificate", "Leave the box above unticked so the line includes -k, and copy it again"],
  ["curl: (6) Could not resolve host", "The host cannot look up PyXie's name", "Check the host's DNS (its Config tab shows it)"],
  ["404, or the link is gone", "The 30-minute link ran out", "Reload this page and copy the line again"],
  ["Run as root", "You are not root in that shell", "Run sudo -i, then paste again"],
  ["sudo missing and apt cannot refresh", "No Proxmox subscription but the enterprise repository is enabled", "Disable that repository as the message explains, then paste again"],
  ["It ran fine but the host stays \"waiting\" here", "PyXie cannot reach the host over SSH", "Check the host's management address and firewall; its page shows what PyXie has on record"],
];

/** Update (or install) the maintenance wrapper on every host of one cluster, in place: one line to paste, and each host ticks itself when it answers with the new version. */
export default function WrapperUpdatePanel({ targetId, onClose }: { targetId: string; onClose?: () => void }) {
  const [list, setList] = useState<HostList | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [since] = useState(Date.now());
  const [now, setNow] = useState(Date.now());
  const hostsRef = useRef<Host[]>([]);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`/api/pve-targets/${targetId}/wrapper-hosts`);
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `Could not read the hosts (${r.status})`);
      setList(d as HostList); hostsRef.current = (d as HostList).hosts;
    } catch (e) { setErr((e as Error).message); }
  }, [targetId]);
  useEffect(() => { load(); }, [load]);

  // every few seconds ask each host that is not on the new version yet (a live check over SSH), so it ticks itself the moment its wrapper answers
  useEffect(() => {
    const t = setInterval(async () => {
      setNow(Date.now());
      const todo = hostsRef.current.filter((h) => h.state !== "ready");
      if (todo.length === 0) return;
      const updated = await Promise.all(todo.map(async (h) => {
        try {
          const r = await fetch(`/api/nodes/${h.id}/host-maintenance-status`);
          const d = await r.json();
          if (!r.ok) return h;
          const state: Host["state"] = d.wrapper_state === "ready" ? "ready" : d.wrapper_state === "outdated" ? "outdated" : d.wrapper_state === "not_answering" ? "not_answering" : "not_set_up";
          return { ...h, state, wrapper_version: d.wrapper_version ?? h.wrapper_version };
        } catch { return h; }
      }));
      const byId = new Map(updated.map((h) => [h.id, h]));
      const next = hostsRef.current.map((h) => byId.get(h.id) ?? h);
      hostsRef.current = next;
      setList((l) => (l ? { ...l, hosts: next } : l));
    }, 6000);
    return () => clearInterval(t);
  }, []);

  if (err) return <div className="text-xs text-bad mt-2">{err}</div>;
  if (!list) return <div className="text-xs text-muted mt-2">Reading the hosts…</div>;
  const left = list.hosts.filter((h) => h.state !== "ready");
  const allDone = list.hosts.length > 0 && left.length === 0;
  const slow = left.length > 0 && now - since > 3 * 60_000;
  const anyInstalled = list.hosts.some((h) => h.wrapper_version);

  return (
    <div className="mt-2 border border-border rounded p-3 text-xs space-y-3 bg-surface2/30">
      <div className="text-sm font-semibold text-text">{anyInstalled ? "Update the maintenance wrapper" : "Install the maintenance wrapper"} <span className="text-xs font-normal text-muted">· {list.hosts.length} host{list.hosts.length === 1 ? "" : "s"} · about a minute per host · PyXie runs nothing on your hosts: you paste one line on each</span></div>
      <div className="text-muted"><b>What this is:</b> a small helper PyXie asks each Proxmox host to run when it patches, restarts or clears locks. {anyInstalled ? `The new version (${list.expected}) adds clearing stale guest locks. ` : ""}Nothing about how it is protected changes: one locked-down account, one key, and a fixed list of commands.</div>

      <div><div className="font-medium text-text mb-0.5">1. Open a root shell on a host</div>
        <div className="text-muted">In the Proxmox web page choose the host, then <b>Shell</b> (or SSH in as root). The prompt should end in <code>#</code>. Do steps 1 and 2 on each host in the list.</div></div>
      <div><div className="font-medium text-text mb-0.5">2. Paste this one line and press Enter</div><OneLineCommand targetId={targetId} /></div>
      <div><div className="font-medium text-text mb-0.5">3. Watch the hosts turn green</div>
        <table className="w-full"><tbody>
          {list.hosts.map((h) => (
            <tr key={h.id} className="border-t border-border">
              <td className="py-1 pr-3 text-text">{h.name}</td>
              <td>{h.state === "ready" ? <span className="text-good">✓ {h.wrapper_version ?? list.expected} up to date</span>
                : <span className="text-warn">waiting… {h.wrapper_version ? `still ${h.wrapper_version}` : "not installed yet"} <span className="text-muted">(paste the line in {h.name}&apos;s shell)</span></span>}</td>
            </tr>
          ))}
        </tbody></table>
        <div className="text-muted mt-1">A host whose SSH fingerprint is already trusted (every host that has had the wrapper before) never asks you to confirm anything. A brand-new host asks once, on its own page.</div>
      </div>

      {allDone && <div className="rounded border border-good/50 bg-good/10 p-2 text-good">✓ All {list.hosts.length} host{list.hosts.length === 1 ? " is" : "s are"} on wrapper {list.expected}. Patching, restarts and clearing stale locks are available everywhere; the &quot;out of date&quot; findings close by themselves. {onClose && <button type="button" className="ml-2 text-muted hover:text-text" onClick={onClose}>Done</button>}</div>}

      <details open={slow}><summary className="cursor-pointer text-text">Something went wrong?{slow ? " A host has not answered after 3 minutes; start here." : ""}</summary>
        <table className="w-full mt-1"><thead><tr className="text-left text-muted"><th className="py-1 pr-3">You see</th><th className="pr-3">It means</th><th>Do this</th></tr></thead><tbody>
          {WRAPPER_TROUBLE.map(([a, b, c]) => <tr key={a} className="border-t border-border align-top"><td className="py-1 pr-3"><code>{a}</code></td><td className="pr-3 text-muted">{b}</td><td>{c}</td></tr>)}
        </tbody></table>
      </details>
    </div>
  );
}
