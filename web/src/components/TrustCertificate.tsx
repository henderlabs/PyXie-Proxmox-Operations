"use client";

import { useEffect, useState } from "react";

const TABS = ["macOS", "Windows", "Linux", "Proxmox node"] as const;
type Tab = (typeof TABS)[number];

/** SHA-256 of the certificate's DER bytes (the fingerprint operating systems show), as AA:BB:... from the PEM Caddy serves. */
async function fingerprint(pem: string): Promise<string | null> {
  try {
    const b64 = pem.replace(/-----[A-Z ]+-----/g, "").replace(/\s+/g, "");
    const der = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", der));
    return Array.from(hash).map((b) => b.toString(16).padStart(2, "0").toUpperCase()).join(":");
  } catch {
    return null;
  }
}

/** Shown when PyXie's web edge uses its own certificate authority (tls mode internal): one download, per-system steps, and the root's fingerprint to compare. */
export default function TrustCertificate() {
  const [tab, setTab] = useState<Tab>("macOS");
  const [fp, setFp] = useState<string | null>(null);
  const [available, setAvailable] = useState<boolean | null>(null);
  const host = typeof window !== "undefined" ? window.location.host : "<this PyXie>";

  useEffect(() => {
    fetch("/pyxie-ca/root.crt").then(async (r) => {
      if (!r.ok) { setAvailable(false); return; }
      const text = await r.text();
      if (!text.includes("BEGIN CERTIFICATE")) { setAvailable(false); return; }
      setAvailable(true);
      setFp(await fingerprint(text));
    }).catch(() => setAvailable(false));
  }, []);

  const steps: Record<Tab, React.ReactNode> = {
    macOS: <>Double-click the file. Keychain Access opens: add it to <b>System</b>, double-click it there, expand <b>Trust</b> and set <i>When using this certificate</i> to <b>Always Trust</b>. Restart the browser.</>,
    Windows: <>Double-click the file, <b>Install Certificate</b>, <b>Local Machine</b>, <i>Place all certificates in the following store</i>, <b>Trusted Root Certification Authorities</b>. Restart the browser.</>,
    Linux: <>Copy it to <code>/usr/local/share/ca-certificates/pyxie-caddy-root.crt</code> and run <code>sudo update-ca-certificates</code>. Firefox keeps its own store: Settings, Privacy and Security, View Certificates, Authorities, Import.</>,
    "Proxmox node": <>As root in the node&apos;s shell (this one command trusts PyXie for that node&apos;s own connections, such as downloading the host wrapper): <pre className="mt-1 p-2 rounded bg-canvas border border-border text-[11px] overflow-x-auto whitespace-pre-wrap break-all">{`curl -fsSLk https://${host}/pyxie-ca/root.crt -o /usr/local/share/ca-certificates/pyxie-caddy-root.crt && update-ca-certificates`}</pre><span className="text-[11px] text-muted">Compare the fingerprint below with <code>openssl x509 -noout -fingerprint -sha256 -in /usr/local/share/ca-certificates/pyxie-caddy-root.crt</code> before relying on it.</span></>,
  };

  if (available === false) return null;
  return (
    <div className="mb-3 rounded border border-border bg-surface p-3" id="trust-certificate">
      <div className="flex items-start gap-3 flex-wrap">
        <div className="flex-1 min-w-[240px]">
          <div className="text-sm font-semibold text-text">Trust this PyXie&apos;s certificate</div>
          <div className="text-xs text-muted">PyXie signs its own HTTPS certificate. Download the root certificate once and add it to each computer or node that opens PyXie; after that the 72-hour certificates renew silently. Never a private key: this is the public root only.</div>
        </div>
        <a href="/pyxie-ca/root.crt" download="pyxie-caddy-root.crt" className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10">Download root certificate</a>
      </div>
      <div className="flex gap-1 mt-3 border-b border-border">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`px-2.5 py-1 text-xs border-b-2 -mb-px ${tab === t ? "border-accent text-text" : "border-transparent text-muted hover:text-text"}`}>{t}</button>
        ))}
      </div>
      <div className="text-xs text-text mt-2">{steps[tab]}</div>
      {fp && <div className="text-[11px] text-muted mt-2">Fingerprint (SHA-256) to compare: <code className="break-all">{fp}</code></div>}
    </div>
  );
}
