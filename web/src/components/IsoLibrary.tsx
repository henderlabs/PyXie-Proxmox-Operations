"use client";

import { useCallback, useEffect, useState, useRef } from "react";
import Link from "next/link";
import type { Operation } from "@/lib/api";
import { Card, CardTitle } from "@/components/Card";
import OpPreview from "@/components/OpPreview";
import UploadCard from "@/components/UploadCard";
import CtTemplates from "@/components/CtTemplates";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";

type Lib = {
  storages: { key: string; label: string; name: string; type: string; shared: boolean; avail: number | null; total: number | null; nodes: string[]; files: { volid: string; name: string; size: number | null; attached: { id: string; vmid: number; name: string | null; slot: string }[] }[] }[];
  candidates: { name: string; type: string; shared: boolean; nodes: string[] }[];
  errors: string[];
};
type Pending = { op: Operation; kind: "download" | "delete" | "enable"; label: string };
const GIB = 1073741824;
const input = "bg-surface border border-border rounded px-2 py-1.5 text-sm w-full";

/** The ISO library: every ISO each storage holds (read live from Proxmox), add one by URL, delete one, switch ISO images on for a storage. */
export default function IsoLibrary() {
  const me = useMe();
  const admin = me === undefined || me?.is_admin === true;
  const [clusters, setClusters] = useState<{ id: string; name: string }[]>([]);
  const [clusterId, setClusterId] = useState("");
  const curCluster = useRef(""); // the cluster now chosen: an answer that arrives for another one is ignored
  curCluster.current = clusterId;
  const [lib, setLib] = useState<Lib | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pend, setPend] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [sel, setSel] = useState<string>("__all__");
  const [form, setForm] = useState({ skey: "", storage: "", node: "", url: "", filename: "", checksum: "", algorithm: "sha256", verify: true });

  useEffect(() => {
    fetch("/api/defaults").then((r) => r.json()).then((d) => { setClusters(d.clusters); if (d.clusters[0]) setClusterId(d.clusters[0].id); }).catch(() => setError("Could not load clusters"));
  }, []);
  const load = useCallback(async () => {
    if (!clusterId) return;
    setLib(null);
    const mine = clusterId;
    const r = await fetch(`/api/iso-library?cluster_id=${clusterId}`);
    const d = await r.json();
    if (curCluster.current !== mine) return;
    if (!r.ok) { setError(d.error || "Could not read the storages"); return; }
    setLib(d);
    setForm((f) => { if (f.storage) return f; const pick = d.storages.find((x: { shared: boolean }) => x.shared) ?? d.storages[0]; return { ...f, skey: pick?.key ?? "", storage: pick?.name ?? "", node: pick?.nodes[0] ?? "" }; });
  }, [clusterId]);
  useEffect(() => { load(); }, [load]);

  async function preview(kind: Pending["kind"], path: string, body: unknown, label: string) {
    setBusy(true); setError(null);
    try {
      const r = await fetch(`/api/iso-library/${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cluster_id: clusterId, ...(body as object) }) });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Could not check that"); return; }
      setPend({ op: d, kind, label }); notifyOperationsChanged();
    } finally { setBusy(false); }
  }
  const done = () => { setPend(null); notifyOperationsChanged(); load(); };
  const stor = lib?.storages.find((s) => s.key === form.skey);
  const shown = (lib?.storages ?? []).filter((s) => sel === "__all__" || s.key === sel);
  const rows = shown.flatMap((s) => s.files.map((f) => ({ ...f, store: s })));
  const choose = (key: string) => {
    setSel(key);
    const s = lib?.storages.find((x) => x.key === key);
    if (s) setForm((f) => ({ ...f, skey: s.key, storage: s.name, node: s.nodes[0] ?? "" }));
  };
  const sizeText = (b: number | null) => (b ? (b >= GIB ? `${(b / GIB).toFixed(2)} GiB` : `${Math.round(b / 1048576)} MiB`) : "—");

  if (!admin && me !== undefined) return <Card><div className="text-sm text-muted">Only administrators can manage ISOs.</div></Card>;
  return (
    <div className="space-y-4">
      {error && <div className="text-bad text-sm">{error}</div>}
      {lib?.errors.length ? <div className="text-warn text-xs">Some storages could not be read: {lib.errors.join("; ")}</div> : null}
      {pend && (
        <Card>
          <CardTitle>{pend.label}</CardTitle>
          <OpPreview key={pend.op.id} op={pend.op} danger={pend.kind === "delete"} confirmLabel={pend.kind === "download" ? "Confirm & download" : pend.kind === "delete" ? "Confirm & delete ISO" : "Confirm & switch ISO images on"}
            progressText={pend.kind === "download" ? "Downloading (the host fetches it; a large ISO takes a while, progress is in the Tasks panel)" : pend.kind === "delete" ? "Deleting the ISO" : "Changing the storage"}
            doneText={pend.kind === "download" ? "Downloaded, checked and listed by Proxmox." : pend.kind === "delete" ? "Deleted and gone from the storage." : "ISO images are switched on for the storage."} onFinished={() => { notifyOperationsChanged(); }} onClose={done} />
        </Card>
      )}
      <Card>
        <div className="flex flex-wrap items-center gap-3 mb-3">
          <label className="text-xs text-muted">Datastore</label>
          <select className="bg-surface border border-border rounded px-2 py-1.5 text-sm" value={sel} onChange={(e) => choose(e.target.value)} disabled={!lib}>
            <option value="__all__">All ISO datastores</option>
            {(lib?.storages ?? []).map((st) => <option key={st.key} value={st.key}>{st.label} ({st.type}{st.shared ? ", shared" : ""}) · {st.files.length} ISO{st.files.length === 1 ? "" : "s"}</option>)}
          </select>
          {clusters.length > 1 && <select className="bg-surface border border-border rounded px-2 py-1.5 text-sm" value={clusterId} onChange={(e) => { setClusterId(e.target.value); setSel("__all__"); setForm((f) => ({ ...f, skey: "", storage: "", node: "" })); }}>{clusters.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>}
          {sel !== "__all__" && stor && <span className="text-xs text-muted">{stor.avail ? `${(stor.avail / GIB).toFixed(0)} GiB free · ` : ""}seen by {stor.nodes.join(", ")}</span>}
        </div>
        {!lib ? <div className="text-sm text-muted">Reading the datastores from Proxmox…</div> : lib.storages.length === 0 ? <div className="text-sm text-muted">No datastore holds ISO images yet. Switch ISO images on for one below, or add an NFS share under Storage setup.</div> : rows.length === 0 ? <div className="text-sm text-muted">No ISO images in {sel === "__all__" ? "these datastores" : sel} yet. Add one below.</div> : (
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-muted uppercase"><th className="py-1 pr-3">ISO</th>{sel === "__all__" && <th className="pr-3">Datastore</th>}<th className="pr-3">Size</th><th className="pr-3">Attached to</th><th /></tr></thead>
            <tbody>
              {rows.map((f) => (
                <tr key={f.volid} className="border-t border-border">
                  <td className="py-1.5 pr-3 text-text break-all">{f.name}</td>
                  {sel === "__all__" && <td className="pr-3 text-muted whitespace-nowrap">{f.store.name}</td>}
                  <td className="pr-3 text-muted whitespace-nowrap">{sizeText(f.size)}</td>
                  <td className="pr-3 text-xs">{f.attached.length ? f.attached.map((a) => <Link key={a.id} href={`/infrastructure/workloads/${a.id}`} className="text-accent hover:underline mr-2">{a.name || `VM ${a.vmid}`}</Link>) : <span className="text-muted">—</span>}</td>
                  <td className="text-right"><button type="button" disabled={busy || !!pend} onClick={() => preview("delete", "delete/dry-run", { node: f.store.nodes[0], volid: f.volid }, `Delete ${f.name}`)} className="text-xs text-bad hover:underline disabled:opacity-40">Delete</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 items-start">
        <Card>
          <CardTitle>Add an ISO by URL</CardTitle>
          {!lib ? <div className="text-sm text-muted">Reading the datastores from Proxmox…</div> : lib.storages.length === 0 ? <div className="text-sm text-muted">No datastore holds ISO images yet.</div> : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
              <div><label className="block text-xs text-muted mb-1">Datastore</label>
                <select className={input} value={form.skey} onChange={(e) => choose(e.target.value)}>
                  {lib.storages.map((st) => <option key={st.key} value={st.key}>{st.label} ({st.type}{st.shared ? ", shared" : ""}{st.avail ? `, ${(st.avail / GIB).toFixed(0)} GiB free` : ""})</option>)}
                </select></div>
              <div><label className="block text-xs text-muted mb-1">Host that downloads it</label>
                <select className={input} value={form.node} onChange={(e) => setForm({ ...form, node: e.target.value })}>{(stor?.nodes ?? []).map((n) => <option key={n}>{n}</option>)}</select></div>
              <div className="md:col-span-2"><label className="block text-xs text-muted mb-1">URL</label>
                <input className={input} value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="https://releases.example.org/debian-12.iso" /></div>
              <div className="md:col-span-2"><label className="block text-xs text-muted mb-1">File name (optional)</label>
                <input className={input} value={form.filename} onChange={(e) => setForm({ ...form, filename: e.target.value })} placeholder="taken from the URL" /></div>
              <div><label className="block text-xs text-muted mb-1">Checksum from the publisher (recommended)</label>
                <input className={input} value={form.checksum} onChange={(e) => setForm({ ...form, checksum: e.target.value })} placeholder="hex digits" /></div>
              <div><label className="block text-xs text-muted mb-1">Algorithm</label>
                <select className={input} value={form.algorithm} onChange={(e) => setForm({ ...form, algorithm: e.target.value })}>{["sha256", "sha512", "sha384", "sha224", "sha1", "md5"].map((a) => <option key={a}>{a}</option>)}</select></div>
              <label className="flex items-center gap-2 text-xs md:col-span-2"><input type="checkbox" checked={form.verify} onChange={(e) => setForm({ ...form, verify: e.target.checked })} /> Verify the server&apos;s TLS certificate</label>
              <div className="md:col-span-2"><button type="button" disabled={busy || !!pend || !form.url || !form.storage} onClick={() => preview("download", "download/dry-run", { node: form.node, storage: form.storage, url: form.url, filename: form.filename || null, checksum: form.checksum || null, algorithm: form.checksum ? form.algorithm : null, verify_certificates: form.verify }, `Download into ${form.storage}`)}
              className="px-3 py-1.5 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50 text-xs">{busy ? "Checking…" : "Preview"}</button></div>
            </div>
          )}
        </Card>
        {lib && lib.storages.length > 0 ? <UploadCard key={clusterId + form.skey} clusterId={clusterId} storages={lib.storages} initialStorage={form.skey} onDone={load} /> : <Card><CardTitle>Upload an ISO from this computer</CardTitle><div className="text-sm text-muted">No datastore holds ISO images yet.</div></Card>}
      </div>
      {lib && lib.candidates.length > 0 && (
        <Card>
          <CardTitle>Storages that could hold ISOs but have them switched off</CardTitle>
          <ul className="text-sm divide-y divide-border">
            {lib.candidates.map((c) => (
              <li key={c.name} className="py-1.5 flex items-center justify-between gap-3">
                <span className="text-text">{c.name} <span className="text-muted text-xs">({c.type}{c.shared ? ", shared" : ""}; {c.nodes.join(", ")})</span></span>
                <button type="button" disabled={busy || !!pend} onClick={() => preview("enable", "enable/dry-run", { storage: c.name }, `Switch ISO images on for ${c.name}`)} className="text-xs text-accent hover:underline disabled:opacity-40">Switch ISO images on…</button>
              </li>
            ))}
          </ul>
          <p className="text-xs text-muted mt-2">Proxmox can keep ISO images on directory, NFS, CIFS, CephFS and GlusterFS storages. To add a new NFS share, use Storage setup; once it exists it shows up here.</p>
        </Card>
      )}
      {clusterId && <CtTemplates key={clusterId} clusterId={clusterId} />}
      <Card><p className="text-xs text-muted">Proxmox has no rename for ISO images: to rename one, add it again under the new name and delete the old one. </p></Card>
    </div>
  );
}
