"use client";

import { useEffect, useState } from "react";
import type { Operation } from "@/lib/api";
import { Card, CardTitle } from "@/components/Card";
import OpPreview from "@/components/OpPreview";
import { notifyOperationsChanged } from "@/lib/operationsBus";

type Tpl = { volid: string; size: number | null; nodes: string[] };
type App = { template: string; package: string | null; os: string | null; version: string | null; headline: string | null; section: string | null };
type CtOpts = { templates: Tpl[]; nodes: { name: string; online: boolean; storages: { name: string }[] }[] };

/** Container templates: the ones installed, download one of Proxmox's official templates, delete one. */
export default function CtTemplates({ clusterId }: { clusterId: string }) {
  const [opts, setOpts] = useState<CtOpts | null>(null);
  const [apps, setApps] = useState<App[] | null>(null);
  const [node, setNode] = useState("");
  const [storage, setStorage] = useState("");
  const [tpl, setTpl] = useState("");
  const [pend, setPend] = useState<{ op: Operation; kind: "download" | "delete"; label: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => fetch(`/api/ct/options?cluster_id=${clusterId}`).then((r) => r.json()).then((d: CtOpts) => { setOpts(d); setNode((n) => n || d.nodes.find((x) => x.online)?.name || ""); }).catch(() => setError("Could not read the templates"));
  useEffect(() => { load(); }, [clusterId]);
  useEffect(() => {
    if (!node) return;
    setApps(null);
    fetch(`/api/ct/appliances?cluster_id=${clusterId}&node=${node}`).then((r) => r.json()).then((d) => (Array.isArray(d) ? setApps(d) : setError(d.error || "Could not read Proxmox's template list"))).catch(() => setError("Could not read Proxmox's template list"));
  }, [node, clusterId]);

  async function preview(kind: "download" | "delete", path: string, body: object, label: string) {
    setBusy(true); setError(null);
    try {
      const r = await fetch(`/api/ct/${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cluster_id: clusterId, ...body }) });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Could not check that"); return; }
      setPend({ op: d, kind, label }); notifyOperationsChanged();
    } finally { setBusy(false); }
  }
  const nodeObj = opts?.nodes.find((n) => n.name === node);
  return (
    <Card>
      <CardTitle>Container templates</CardTitle>
      {error && <div className="text-bad text-xs mb-2">{error}</div>}
      {pend && <div className="mb-3"><div className="text-xs text-text mb-1">{pend.label}</div><OpPreview op={pend.op} danger={pend.kind === "delete"} confirmLabel={pend.kind === "download" ? "Confirm & download" : "Confirm & delete"} progressText={pend.kind === "download" ? "Downloading the template (progress is in the Tasks panel)" : "Deleting"} doneText="Done and read back from Proxmox." onFinished={() => notifyOperationsChanged()} onClose={() => { setPend(null); load(); }} /></div>}
      {!opts ? <div className="text-sm text-muted">Reading…</div> : (
        <>
          {opts.templates.length === 0 ? <div className="text-sm text-muted mb-3">None installed.</div> : (
            <ul className="text-sm divide-y divide-border mb-3">
              {opts.templates.map((t) => (
                <li key={t.volid} className="py-1.5 flex items-center justify-between gap-3">
                  <span className="text-text break-all">{t.volid}<span className="text-muted text-xs"> {t.size ? `· ${(t.size / 1048576).toFixed(0)} MiB ` : ""}· on {t.nodes.join(", ")}</span></span>
                  <button type="button" disabled={busy || !!pend} onClick={() => preview("delete", "template-delete/dry-run", { node: t.nodes[0], volid: t.volid }, `Delete ${t.volid}`)} className="text-xs text-bad hover:underline disabled:opacity-40">Delete</button>
                </li>
              ))}
            </ul>
          )}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-2 text-sm items-end">
            <div><label className="block text-xs text-muted mb-1">Host</label><select className="bg-surface border border-border rounded px-2 py-1.5 w-full" value={node} onChange={(e) => { setNode(e.target.value); setStorage(""); }}>{opts.nodes.filter((n) => n.online).map((n) => <option key={n.name}>{n.name}</option>)}</select></div>
            <div><label className="block text-xs text-muted mb-1">Storage</label><select className="bg-surface border border-border rounded px-2 py-1.5 w-full" value={storage} onChange={(e) => setStorage(e.target.value)}><option value="">Choose…</option>{(nodeObj?.storages ?? []).map((s) => <option key={s.name}>{s.name}</option>)}</select></div>
            <div className="md:col-span-2"><label className="block text-xs text-muted mb-1">Official template</label>
              <select className="bg-surface border border-border rounded px-2 py-1.5 w-full" value={tpl} onChange={(e) => setTpl(e.target.value)}><option value="">{apps ? "Choose…" : "Reading Proxmox's list…"}</option>{(apps ?? []).map((a) => <option key={a.template} value={a.template}>{a.template}{a.headline ? ` — ${a.headline}` : ""}</option>)}</select></div>
          </div>
          <div className="mt-2"><button type="button" disabled={busy || !!pend || !node || !storage || !tpl} onClick={() => preview("download", "template-download/dry-run", { node, storage, template: tpl }, `Download ${tpl} into ${storage}`)} className="px-3 py-1.5 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50 text-xs">{busy ? "Checking…" : "Preview download"}</button>
            <span className="text-xs text-muted ml-3">The storage must hold container templates (content type vztmpl): Proxmox&apos;s default &quot;local&quot; does.</span></div>
        </>
      )}
    </Card>
  );
}
