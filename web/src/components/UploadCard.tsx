"use client";

import { useRef, useState } from "react";
import { Card, CardTitle } from "@/components/Card";
import { notifyOperationsChanged } from "@/lib/operationsBus";

const GIB = 1073741824;
const input = "bg-surface border border-border rounded px-2 py-1.5 text-sm w-full";

/** Upload an ISO from this computer. The file is checked first (name, room, privileges), then streamed through PyXie to the host: nothing is kept on PyXie. */
export default function UploadCard({ clusterId, storages, initialStorage, onDone }: { clusterId: string; storages: { key: string; label: string; name: string; nodes: string[] }[]; initialStorage?: string; onDone: () => void }) {
  const first = storages.find((x) => x.key === initialStorage) ?? storages[0];
  const [storageKey, setStorage] = useState(first?.key ?? "");
  const [node, setNode] = useState(first?.nodes[0] ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [checksum, setChecksum] = useState("");
  const [algo, setAlgo] = useState("sha256");
  const [check, setCheck] = useState<{ ok: boolean; reasons: string[] } | null>(null);
  const [pct, setPct] = useState<number | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const xhr = useRef<XMLHttpRequest | null>(null);
  const st = storages.find((s) => s.key === storageKey);
  const storage = st?.name ?? "";

  async function doCheck() {
    if (!file) return;
    setBusy(true); setMsg(null); setCheck(null);
    try {
      const r = await fetch("/api/iso-library/upload/check", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cluster_id: clusterId, node, storage, filename: file.name, size: file.size, checksum: checksum.trim() || null, algorithm: checksum.trim() ? algo : null }) });
      const d = await r.json();
      setCheck(r.ok ? d : { ok: false, reasons: [d.error || "could not check"] });
    } finally { setBusy(false); }
  }
  function send() {
    if (!file) return;
    const q = new URLSearchParams({ cluster_id: clusterId, node, storage, filename: file.name, ...(checksum.trim() ? { checksum: checksum.trim(), algorithm: algo } : {}) });
    const x = new XMLHttpRequest();
    xhr.current = x;
    x.open("POST", `/api/iso-library/upload?${q}`);
    x.setRequestHeader("Content-Type", "application/octet-stream");
    x.upload.onprogress = (e) => e.lengthComputable && setPct(Math.round((e.loaded / e.total) * 100));
    x.onload = () => {
      setPct(null); setBusy(false);
      let d: { error?: string; volid?: string } = {};
      try { d = JSON.parse(x.responseText); } catch { /* not JSON */ }
      if (x.status === 200) { setMsg({ ok: true, text: `Uploaded and read back from Proxmox: ${d.volid}` }); setFile(null); setCheck(null); notifyOperationsChanged(); onDone(); }
      else setMsg({ ok: false, text: d.error || `Upload failed (${x.status})` });
    };
    x.onerror = () => { setPct(null); setBusy(false); setMsg({ ok: false, text: "The connection broke during the upload. Nothing is kept: Proxmox removes a partial file." }); };
    x.onabort = () => { setPct(null); setBusy(false); setMsg({ ok: false, text: "Upload cancelled." }); };
    setBusy(true); setPct(0); setMsg(null);
    x.send(file);
  }

  return (
    <Card>
      <CardTitle>Upload an ISO from this computer</CardTitle>
      {storages.length === 0 ? <div className="text-sm text-muted">No storage holds ISO images yet.</div> : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
          <div><label className="block text-xs text-muted mb-1">Storage</label>
            <select className={input} value={storageKey} disabled={busy} onChange={(e) => { const s = storages.find((x) => x.key === e.target.value); setStorage(e.target.value); setNode(s?.nodes[0] ?? ""); setCheck(null); }}>{storages.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}</select></div>
          <div><label className="block text-xs text-muted mb-1">Host that receives it</label>
            <select className={input} value={node} disabled={busy} onChange={(e) => { setNode(e.target.value); setCheck(null); }}>{(st?.nodes ?? []).map((n) => <option key={n}>{n}</option>)}</select></div>
          <div><label className="block text-xs text-muted mb-1">File (.iso)</label>
            <input type="file" accept=".iso,.ISO" disabled={busy} className="text-xs" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setCheck(null); setMsg(null); }} /></div>
          <div><label className="block text-xs text-muted mb-1">Checksum (optional)</label>
            <input className={input} value={checksum} disabled={busy} onChange={(e) => { setChecksum(e.target.value); setCheck(null); }} placeholder="hex digits; Proxmox verifies it" /></div>
          <div><label className="block text-xs text-muted mb-1">Algorithm</label>
            <select className={input} value={algo} disabled={busy} onChange={(e) => setAlgo(e.target.value)}>{["sha256", "sha512", "sha384", "sha224", "sha1", "md5"].map((a) => <option key={a}>{a}</option>)}</select></div>
          <div className="md:col-span-2 space-y-2">
            {file && <div className="text-xs text-muted">{file.name} · {(file.size / GIB).toFixed(2)} GiB</div>}
            {check && <ul className={`list-disc list-inside text-xs ${check.ok ? "text-muted" : "text-bad"}`}>{check.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
            {pct !== null && <div><div className="h-2 rounded bg-surface2 overflow-hidden"><div className="h-2 bg-accent" style={{ width: `${pct}%` }} /></div><div className="text-xs text-muted mt-1">{pct}% sent{pct === 100 ? ": Proxmox is filing it away…" : ""} <button type="button" className="text-bad hover:underline ml-2" onClick={() => xhr.current?.abort()}>cancel</button></div></div>}
            {msg && <div className={`text-xs ${msg.ok ? "text-good" : "text-bad"}`}>{msg.text}</div>}
            <div className="flex gap-2">
              <button type="button" disabled={!file || busy} onClick={doCheck} className="px-3 py-1.5 rounded border border-border text-xs text-text hover:bg-surface2 disabled:opacity-50">Check</button>
              <button type="button" disabled={!file || busy || !check?.ok} onClick={send} className="px-3 py-1.5 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50 text-xs">Upload</button>
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}
