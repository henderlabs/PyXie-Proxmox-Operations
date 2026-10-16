"use client";

import { useCallback, useEffect, useState, useRef } from "react";
import type { Operation } from "@/lib/api";
import { Card, CardTitle } from "@/components/Card";
import OpPreview from "@/components/OpPreview";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";

type Opts = { nodes: { name: string; online: boolean }[]; storages: { name: string; type: string; server: string | null; export: string | null; content: string | null; nodes: string | null }[]; servers: string[] };
type Kind = "nfs" | "iscsi" | "dir" | "lvm" | "lvmthin" | "zfspool";
const CONTENT: [string, string][] = [["images", "VM disks"], ["rootdir", "Container disks"], ["iso", "ISO images"], ["vztmpl", "Container templates"], ["backup", "Backups"], ["snippets", "Snippets"], ["import", "Import"]];
const KINDS: { key: Kind; label: string; what: string; content: string[]; allowed: string[] }[] = [
  { key: "nfs", label: "NFS share", what: "A folder shared by a NAS or file server. Every host mounts it, so VMs on it can move between hosts without copying their disks. The usual home for ISOs, backups and shared VM disks.", content: ["iso"], allowed: CONTENT.map((c) => c[0]) },
  { key: "iscsi", label: "iSCSI target", what: "Disks (LUNs) offered over the network by a SAN. Each LUN is handed to a VM as a raw disk. To carve one into many VM disks, put LVM on top (see LVM). CHAP logins are not supported here: they need a password PyXie does not store.", content: ["images"], allowed: ["images"] },
  { key: "dir", label: "Directory", what: "A folder that already exists on each host, for example a mounted disk or a clustered file system. Holds anything: ISOs, backups, VM disks. If the folder is not there, Proxmox makes an empty one on the host's own disk.", content: ["iso"], allowed: CONTENT.map((c) => c[0]) },
  { key: "lvm", label: "LVM (also Fibre Channel / shared LUN)", what: "An existing LVM volume group. For Fibre Channel or iSCSI, present the LUN to every host, create the volume group on one host, then add it here and tick Shared: all hosts then use it and guests can live-migrate. Fast, but no snapshots.", content: ["images", "rootdir"], allowed: ["images", "rootdir"] },
  { key: "lvmthin", label: "LVM-thin", what: "A thin pool inside a volume group on one host's disks. Supports snapshots and clones and does not use space until it is written. Local to the host: not shared.", content: ["images", "rootdir"], allowed: ["images", "rootdir"] },
  { key: "zfspool", label: "ZFS pool", what: "An existing ZFS pool on the host's disks. Snapshots, clones and compression built in. Local to each host that lists it: not shared.", content: ["images", "rootdir"], allowed: ["images", "rootdir"] },
];
const input = "bg-surface border border-border rounded px-2 py-1.5 text-sm w-full";
const empty = { id: "", server: "", export: "", portal: "", target: "", path: "", vgname: "", thinpool: "", pool: "", version: "", shared: false };

/** Add a datastore to the datacenter (NFS, iSCSI, directory, LVM, LVM-thin, ZFS), or remove a definition. Both are previewed against the real cluster; removing never deletes the data. */
export default function StorageSetup() {
  const me = useMe();
  const admin = me === undefined || me?.is_admin === true;
  const [clusters, setClusters] = useState<{ id: string; name: string }[]>([]);
  const [clusterId, setClusterId] = useState("");
  const curCluster = useRef(""); // the cluster now chosen: an answer that arrives for another one is ignored
  curCluster.current = clusterId;
  const [opts, setOpts] = useState<Opts | null>(null);
  const [kind, setKind] = useState<Kind>("nfs");
  const [f, setF] = useState(empty);
  const [content, setContent] = useState<string[]>(["iso"]);
  const [nodes, setNodes] = useState<string[]>([]);
  const [found, setFound] = useState<{ label: string; value: string }[] | null>(null);
  const [scanErr, setScanErr] = useState<string | null>(null);
  const [rmName, setRmName] = useState("");
  const [rmTyped, setRmTyped] = useState("");
  const [pend, setPend] = useState<{ op: Operation; label: string; danger: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const meta = KINDS.find((k) => k.key === kind)!;

  useEffect(() => { fetch("/api/defaults").then((r) => r.json()).then((d) => { setClusters(d.clusters); if (d.clusters[0]) setClusterId(d.clusters[0].id); }).catch(() => setError("Could not load clusters")); }, []);
  const load = useCallback(async () => {
    if (!clusterId) return;
    const mine = clusterId;
    const r = await fetch(`/api/storage-setup/options?cluster_id=${clusterId}`);
    const d = await r.json();
    if (curCluster.current !== mine) return;
    if (!r.ok) { setError(d.error || "Could not read the datastores"); return; }
    setOpts(d);
  }, [clusterId]);
  useEffect(() => { load(); }, [load]);

  function pickKind(k: Kind) {
    setKind(k); setFound(null); setScanErr(null);
    setContent(KINDS.find((x) => x.key === k)!.content);
    setF((x) => ({ ...empty, id: x.id }));
  }
  const scanNode = opts?.nodes.find((n) => n.online && (!nodes.length || nodes.includes(n.name)))?.name;
  async function scan() {
    setScanErr(null); setFound(null);
    if (!scanNode) return;
    const base = `cluster_id=${clusterId}&node=${scanNode}`;
    let url = "";
    if (kind === "nfs") url = `/api/storage-setup/scan-nfs?${base}&server=${encodeURIComponent(f.server)}`;
    else if (kind === "iscsi") url = `/api/storage-setup/scan/iscsi?${base}&portal=${encodeURIComponent(f.portal)}`;
    else if (kind === "lvm") url = `/api/storage-setup/scan/lvm?${base}`;
    else if (kind === "lvmthin") url = `/api/storage-setup/scan/lvmthin?${base}&vg=${encodeURIComponent(f.vgname)}`;
    else if (kind === "zfspool") url = `/api/storage-setup/scan/zfs?${base}`;
    else return;
    const r = await fetch(url);
    const d = await r.json();
    if (!r.ok) { setScanErr(d.error || "Could not look"); return; }
    const rows = d as Record<string, string>[];
    setFound(rows.map((x) => ({ label: x.path || x.target || x.vg || x.lv || x.pool || "?", value: x.path || x.target || x.vg || x.lv || x.pool || "" })));
  }
  function choose(v: string) {
    setF((x) => ({ ...x, ...(kind === "nfs" ? { export: v } : kind === "iscsi" ? { target: v } : kind === "lvm" ? { vgname: v } : kind === "lvmthin" ? { thinpool: v } : kind === "zfspool" ? { pool: v } : {}) }));
  }
  const canScan = kind === "nfs" ? !!f.server : kind === "iscsi" ? !!f.portal : kind === "lvmthin" ? !!f.vgname : kind !== "dir";
  const ready = !!f.id && content.length > 0 && (kind === "nfs" ? f.server && f.export : kind === "iscsi" ? f.portal && f.target : kind === "dir" ? f.path : kind === "lvm" ? f.vgname : kind === "lvmthin" ? f.vgname && f.thinpool : f.pool);

  async function preview(path: string, body: object, label: string, danger = false) {
    setBusy(true); setError(null);
    try {
      const r = await fetch(`/api/storage-setup/${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cluster_id: clusterId, ...body }) });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Could not check that"); return; }
      setPend({ op: d, label, danger }); notifyOperationsChanged();
    } finally { setBusy(false); }
  }
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const spec = () => ({ type: kind, id: f.id.trim(), content, nodes, ...(kind === "nfs" ? { server: f.server.trim(), export: f.export.trim(), nfs_version: f.version || null } : kind === "iscsi" ? { portal: f.portal.trim(), target: f.target.trim() } : kind === "dir" ? { path: f.path.trim() } : kind === "lvm" ? { vgname: f.vgname.trim(), shared: f.shared } : kind === "lvmthin" ? { vgname: f.vgname.trim(), thinpool: f.thinpool.trim() } : { pool: f.pool.trim() }) });
  if (!admin && me !== undefined) return <Card><div className="text-sm text-muted">Only administrators can change datastores.</div></Card>;
  return (
    <div className="space-y-4">
      {clusters.length > 1 && <select className={input + " max-w-xs"} value={clusterId} onChange={(e) => { setClusterId(e.target.value); setOpts(null); }}>{clusters.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>}
      {error && <div className="text-bad text-sm">{error}</div>}
      {pend && (
        <Card>
          <CardTitle>{pend.label}</CardTitle>
          <OpPreview key={pend.op.id} op={pend.op} danger={pend.danger} confirmLabel="Confirm" progressText="Changing the datastore" doneText="Done and read back from Proxmox." onFinished={() => notifyOperationsChanged()} onClose={() => { setPend(null); load(); }} />
        </Card>
      )}
      <Card>
        <CardTitle>Add a datastore</CardTitle>
        {!opts ? <div className="text-sm text-muted">Reading the datacenter…</div> : (
          <div className="space-y-3 text-sm">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <div><label className="block text-xs text-muted mb-1">Type</label>
                <select className={input} value={kind} onChange={(e) => pickKind(e.target.value as Kind)}>{KINDS.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}</select></div>
              <div className="md:col-span-2 text-xs text-muted self-end">{meta.what}</div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <div><label className="block text-xs text-muted mb-1">Datastore ID (the name Proxmox shows)</label><input className={input} value={f.id} onChange={(e) => setF({ ...f, id: e.target.value })} placeholder="nas-isos" /></div>
              {kind === "nfs" && (<>
                <div><label className="block text-xs text-muted mb-1">NFS server</label><input className={input} list="nfs-servers" value={f.server} onChange={(e) => { setF({ ...f, server: e.target.value }); setFound(null); }} placeholder="192.168.1.10" /><datalist id="nfs-servers">{opts.servers.map((s) => <option key={s} value={s} />)}</datalist></div>
                <div><label className="block text-xs text-muted mb-1">Export</label>
                  {found ? <select className={input} value={f.export} onChange={(e) => choose(e.target.value)}><option value="">Choose…</option>{found.map((x) => <option key={x.value}>{x.value}</option>)}</select> : <input className={input} value={f.export} onChange={(e) => setF({ ...f, export: e.target.value })} placeholder="/volume1/share" />}</div>
              </>)}
              {kind === "iscsi" && (<>
                <div><label className="block text-xs text-muted mb-1">Portal (address[:port])</label><input className={input} value={f.portal} onChange={(e) => { setF({ ...f, portal: e.target.value }); setFound(null); }} placeholder="10.0.0.9:3260" /></div>
                <div><label className="block text-xs text-muted mb-1">Target</label>
                  {found ? <select className={input} value={f.target} onChange={(e) => choose(e.target.value)}><option value="">Choose…</option>{found.map((x) => <option key={x.value}>{x.value}</option>)}</select> : <input className={input} value={f.target} onChange={(e) => setF({ ...f, target: e.target.value })} placeholder="iqn.2005-10.org.example:disk1" />}</div>
              </>)}
              {kind === "dir" && <div className="md:col-span-2"><label className="block text-xs text-muted mb-1">Folder on each host</label><input className={input} value={f.path} onChange={(e) => setF({ ...f, path: e.target.value })} placeholder="/mnt/data" /></div>}
              {(kind === "lvm" || kind === "lvmthin") && (
                <div><label className="block text-xs text-muted mb-1">Volume group</label>
                  {found && kind === "lvm" ? <select className={input} value={f.vgname} onChange={(e) => choose(e.target.value)}><option value="">Choose…</option>{found.map((x) => <option key={x.value}>{x.value}</option>)}</select> : <input className={input} value={f.vgname} onChange={(e) => { setF({ ...f, vgname: e.target.value }); if (kind === "lvmthin") setFound(null); }} placeholder="vg_san" />}</div>
              )}
              {kind === "lvmthin" && (
                <div><label className="block text-xs text-muted mb-1">Thin pool</label>
                  {found ? <select className={input} value={f.thinpool} onChange={(e) => choose(e.target.value)}><option value="">Choose…</option>{found.map((x) => <option key={x.value}>{x.value}</option>)}</select> : <input className={input} value={f.thinpool} onChange={(e) => setF({ ...f, thinpool: e.target.value })} placeholder="data" />}</div>
              )}
              {kind === "zfspool" && (
                <div><label className="block text-xs text-muted mb-1">ZFS pool or dataset</label>
                  {found ? <select className={input} value={f.pool} onChange={(e) => choose(e.target.value)}><option value="">Choose…</option>{found.map((x) => <option key={x.value}>{x.value}</option>)}</select> : <input className={input} value={f.pool} onChange={(e) => setF({ ...f, pool: e.target.value })} placeholder="tank" />}</div>
              )}
            </div>
            {kind !== "dir" && <div className="text-xs"><button type="button" onClick={scan} disabled={!canScan || !scanNode} className="text-accent hover:underline disabled:opacity-40">Ask {scanNode ?? "a host"} what is available</button> <span className="text-muted">(fills the choice above from what the host really sees)</span>{scanErr && <span className="text-bad ml-2">{scanErr}</span>}</div>}
            <div><div className="text-xs text-muted mb-1">Holds</div><div className="flex flex-wrap gap-3">{CONTENT.filter((c) => meta.allowed.includes(c[0])).map(([k, l]) => <label key={k} className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={content.includes(k)} onChange={() => setContent(toggle(content, k))} />{l}</label>)}</div></div>
            {kind === "nfs" && <div className="max-w-xs"><label className="block text-xs text-muted mb-1">NFS version (optional)</label><select className={input} value={f.version} onChange={(e) => setF({ ...f, version: e.target.value })}><option value="">Let Proxmox negotiate</option>{["3", "4", "4.1", "4.2"].map((v) => <option key={v}>{v}</option>)}</select></div>}
            {kind === "lvm" && <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={f.shared} onChange={(e) => setF({ ...f, shared: e.target.checked })} />Shared: the volume group sits on a LUN every listed host can see (Fibre Channel or iSCSI), so guests on it can be live-migrated</label>}
            <div><div className="text-xs text-muted mb-1">Used by (none ticked = every host)</div><div className="flex flex-wrap gap-3">{opts.nodes.map((n) => <label key={n.name} className="flex items-center gap-1.5 text-xs"><input type="checkbox" disabled={!n.online} checked={nodes.includes(n.name)} onChange={() => setNodes(toggle(nodes, n.name))} />{n.name}{n.online ? "" : " (offline)"}</label>)}</div></div>
            <div><button type="button" disabled={busy || !!pend || !ready} onClick={() => preview("create/dry-run", { spec: spec() }, `Add ${meta.label.split(" (")[0]} datastore ${f.id}`)} className="px-3 py-1.5 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50 text-xs">{busy ? "Checking…" : "Preview"}</button></div>
            <p className="text-xs text-muted">CIFS/SMB shares and Proxmox Backup Server datastores need a password, which PyXie does not keep: add those in Proxmox (Datacenter &gt; Storage &gt; Add). Fibre Channel needs no setup here beyond the volume group: see LVM above.</p>
          </div>
        )}
      </Card>
      <Card>
        <CardTitle>Existing datastores</CardTitle>
        {!opts ? null : (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-sm items-end">
            <div><label className="block text-xs text-muted mb-1">Remove a datastore definition</label><select className={input} value={rmName} onChange={(e) => { setRmName(e.target.value); setRmTyped(""); }}><option value="">Choose…</option>{opts.storages.map((s) => <option key={s.name} value={s.name}>{s.name} ({s.type}{s.server ? ` ${s.server}` : ""}; {s.content})</option>)}</select></div>
            <div><label className="block text-xs text-muted mb-1">Type its name to confirm</label><input className={input} value={rmTyped} onChange={(e) => setRmTyped(e.target.value)} autoComplete="off" /></div>
            <div><button type="button" disabled={busy || !!pend || !rmName || !rmTyped} onClick={() => preview("remove/dry-run", { storage: rmName, confirm: rmTyped }, `Remove the datastore ${rmName}`, true)} className="px-3 py-1.5 rounded bg-ink text-on-ink border border-bad hover:bg-bad/10 disabled:opacity-50 text-xs">Preview removal</button></div>
            <p className="md:col-span-3 text-xs text-muted">Only the definition goes: hosts stop using the datastore, and the data on it is never deleted. A datastore that a guest still has a disk, mount point or ISO on is refused. See every datastore under Datastores.</p>
          </div>
        )}
      </Card>
    </div>
  );
}
