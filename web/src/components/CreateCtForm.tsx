"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Operation } from "@/lib/api";
import { Card, CardTitle } from "@/components/Card";
import OpPreview from "@/components/OpPreview";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";
import GbInput from "@/components/GbInput";

type CtOpts = {
  nodes: { name: string; online: boolean; maintenance: boolean; mem_total: number | null; mem_used: number | null; storages: { name: string; type: string | null; avail: number | null; shared: boolean }[] }[];
  templates: { volid: string; size: number | null; nodes: string[] }[]; bridges: { name: string; vlan_aware: boolean; missing_on: string[] }[]; pools: string[];
  pins: { tag: string; strict: boolean; nodes: string[] }[]; next_vmid: number | null; errors: string[];
};
const GIB = 1073741824;
const input = "bg-surface border border-border rounded px-2 py-1.5 text-sm w-full";
const Field = ({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) => <div><label className="block text-xs text-muted mb-1">{label}</label>{children}{hint && <div className="text-xs text-muted mt-1">{hint}</div>}</div>;

/** The trimmed create-container form: an unprivileged container, one root disk, one network device. Everything is checked against the cluster in a preview first. */
export default function CreateCtForm() {
  const me = useMe();
  const admin = me === undefined || me?.is_admin === true;
  const [clusters, setClusters] = useState<{ id: string; name: string }[]>([]);
  const [clusterId, setClusterId] = useState("");
  const [opts, setOpts] = useState<CtOpts | null>(null);
  const [f, setF] = useState<Record<string, string | boolean>>({ hostname: "", vmid: "", node: "", template: "", cores: "1", memory_mb: "512", swap_mb: "512", disk_storage: "", disk_size_gb: "8", nic_bridge: "", nic_vlan: "", nic_firewall: false, ip: "dhcp", gateway: "", nameserver: "", searchdomain: "", ssh_keys: "", tagsText: "", pool: "", start_at_boot: false, start_after: false });
  const touched = useRef<Set<string>>(new Set());
  const [op, setOp] = useState<Operation | null>(null);
  const [createdId, setCreatedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const set = (k: string, v: string | boolean) => { touched.current.add(k); setF((p) => ({ ...p, [k]: v })); };
  const tags = useMemo(() => String(f.tagsText).split(/[,;\s]+/).filter(Boolean), [f.tagsText]);

  useEffect(() => { fetch("/api/defaults").then((r) => r.json()).then((d) => { setClusters(d.clusters); if (d.clusters[0]) setClusterId(d.clusters[0].id); }).catch(() => setError("Could not load clusters")); }, []);
  useEffect(() => {
    if (!clusterId) return;
    setOpts(null);
    fetch(`/api/ct/options?cluster_id=${clusterId}`).then((r) => r.json()).then((d) => (d.error ? setError(d.error) : setOpts(d))).catch(() => setError("Could not read the cluster"));
  }, [clusterId]);
  useEffect(() => {
    if (!opts) return;
    const hard = opts.pins.filter((p) => p.strict && tags.includes(p.tag)).map((p) => p.nodes);
    const ok = opts.nodes.filter((n) => n.online && !n.maintenance && hard.every((a) => a.includes(n.name)));
    const best = [...ok].sort((a, b) => ((b.mem_total ?? 0) - (b.mem_used ?? 0)) - ((a.mem_total ?? 0) - (a.mem_used ?? 0)))[0];
    setF((p) => ({ ...p, ...(touched.current.has("node") ? {} : { node: best?.name ?? "" }), ...(touched.current.has("nic_bridge") || !opts.bridges.length ? {} : { nic_bridge: opts.bridges[0].name }) }));
  }, [opts, tags.join(",")]);
  const node = opts?.nodes.find((n) => n.name === f.node);
  const tpls = (opts?.templates ?? []).filter((t) => !f.node || t.nodes.includes(String(f.node)));
  useEffect(() => {
    if (!node) return;
    setF((p) => ({
      ...p,
      ...(touched.current.has("disk_storage") && node.storages.some((s) => s.name === p.disk_storage) ? {} : { disk_storage: node.storages[0]?.name ?? "" }),
      ...(touched.current.has("template") && tpls.some((t) => t.volid === p.template) ? {} : { template: tpls[0]?.volid ?? "" }),
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node?.name, opts]);

  async function check() {
    setPending(true); setError(null);
    try {
      const n = (k: string) => parseInt(String(f[k]), 10);
      const spec = { node: f.node, hostname: String(f.hostname).trim(), vmid: f.vmid ? n("vmid") : null, template: f.template, cores: n("cores"), memory_mb: n("memory_mb"), swap_mb: n("swap_mb"), disk_storage: f.disk_storage, disk_size_gb: n("disk_size_gb"),
        nic_bridge: f.nic_bridge, nic_vlan: f.nic_vlan ? n("nic_vlan") : null, nic_firewall: !!f.nic_firewall, ip: String(f.ip).trim() || "dhcp", gateway: f.gateway || null, nameserver: f.nameserver || null, searchdomain: f.searchdomain || null,
        ssh_keys: f.ssh_keys || null, tags, pool: f.pool || null, start_at_boot: !!f.start_at_boot, start_after: !!f.start_after };
      const r = await fetch("/api/ct/dry-run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cluster_id: clusterId, spec }) });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Could not check that"); return; }
      setOp(d); notifyOperationsChanged();
    } finally { setPending(false); }
  }
  async function finished() {
    if (!op) return;
    const r = await fetch(`/api/operations/${op.id}`);
    if (r.ok) setCreatedId((((await r.json()) as Operation).context as { workload_id?: string } | null)?.workload_id ?? null);
  }
  if (!admin) return <Card><div className="text-sm text-muted">Only administrators can create containers.</div></Card>;
  const free = (n: { mem_total: number | null; mem_used: number | null }) => (n.mem_total && n.mem_used != null ? `${((n.mem_total - n.mem_used) / GIB).toFixed(0)} GiB free` : "");
  return (
    <div className="space-y-4">
      {!opts ? <Card><div className="text-sm text-muted">{error || "Reading the cluster from Proxmox…"}</div></Card> : (
        <>
          {opts.templates.length === 0 && <Card><div className="text-sm text-warn">No container template is installed yet. Add one in the <Link href="/infrastructure/storage/iso" className="text-accent hover:underline">ISO library</Link> (Container templates), then come back.</div></Card>}
          <Card>
            <CardTitle>Basics</CardTitle>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              {clusters.length > 1 && <Field label="Cluster"><select className={input} value={clusterId} onChange={(e) => { touched.current.clear(); setClusterId(e.target.value); }}>{clusters.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>}
              <Field label="Hostname"><input className={input} value={String(f.hostname)} onChange={(e) => set("hostname", e.target.value)} placeholder="web01" /></Field>
              <Field label="ID" hint={`Empty = next free${opts.next_vmid ? ` (${opts.next_vmid})` : ""}.`}><input type="number" className={input} value={String(f.vmid)} onChange={(e) => set("vmid", e.target.value)} /></Field>
              <Field label="Host"><select className={input} value={String(f.node)} onChange={(e) => set("node", e.target.value)}><option value="">Choose…</option>{opts.nodes.map((n) => <option key={n.name} value={n.name} disabled={!n.online || n.maintenance}>{n.name}{n.maintenance ? " (maintenance)" : !n.online ? " (offline)" : ""} {free(n)}</option>)}</select></Field>
              <Field label="Template" hint="Unprivileged container."><select className={input} value={String(f.template)} onChange={(e) => set("template", e.target.value)}>{tpls.map((t) => <option key={t.volid} value={t.volid}>{t.volid.split("/")[1] ?? t.volid}{t.size ? ` (${(t.size / 1048576).toFixed(0)} MiB)` : ""}</option>)}</select></Field>
              <Field label="Tags"><input className={input} value={String(f.tagsText)} onChange={(e) => set("tagsText", e.target.value)} placeholder="linux, web" /></Field>
              <Field label="Pool (optional)"><select className={input} value={String(f.pool)} onChange={(e) => set("pool", e.target.value)}><option value="">None</option>{opts.pools.map((p) => <option key={p}>{p}</option>)}</select></Field>
            </div>
          </Card>
          <Card>
            <CardTitle>Resources</CardTitle>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              <Field label="Cores"><input type="number" className={input} value={String(f.cores)} onChange={(e) => set("cores", e.target.value)} /></Field>
              <Field label="Memory (GB)"><GbInput className={input} value={String(f.memory_mb)} onChange={(v) => set("memory_mb", v)} /></Field>
              <Field label="Swap (GB)"><GbInput className={input} value={String(f.swap_mb)} onChange={(v) => set("swap_mb", v)} /></Field>
              <Field label="Root disk (GiB)"><input type="number" className={input} value={String(f.disk_size_gb)} onChange={(e) => set("disk_size_gb", e.target.value)} /></Field>
              <Field label="Disk storage"><select className={input} value={String(f.disk_storage)} onChange={(e) => set("disk_storage", e.target.value)}>{(node?.storages ?? []).map((s) => <option key={s.name} value={s.name}>{s.name} ({s.type}{s.avail ? `, ${(s.avail / GIB).toFixed(0)} GiB free` : ""})</option>)}</select></Field>
            </div>
          </Card>
          <Card>
            <CardTitle>Network and access</CardTitle>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
              <Field label="Bridge"><select className={input} value={String(f.nic_bridge)} onChange={(e) => set("nic_bridge", e.target.value)}>{opts.bridges.map((b) => <option key={b.name} value={b.name}>{b.name}{b.vlan_aware ? " (VLAN-aware)" : ""}{b.missing_on.length ? `  ⚠ not on ${b.missing_on.join(", ")}` : ""}</option>)}</select></Field>
              <Field label="VLAN tag"><input type="number" className={input} value={String(f.nic_vlan)} onChange={(e) => set("nic_vlan", e.target.value)} placeholder="untagged" /></Field>
              <Field label="IPv4 address" hint="dhcp, or 192.168.1.50/24"><input className={input} value={String(f.ip)} onChange={(e) => set("ip", e.target.value)} /></Field>
              <Field label="Gateway" hint="Fixed address only."><input className={input} value={String(f.gateway)} onChange={(e) => set("gateway", e.target.value)} /></Field>
              <Field label="DNS server"><input className={input} value={String(f.nameserver)} onChange={(e) => set("nameserver", e.target.value)} placeholder="from the host" /></Field>
              <Field label="Search domain"><input className={input} value={String(f.searchdomain)} onChange={(e) => set("searchdomain", e.target.value)} placeholder="from the host" /></Field>
              <div className="md:col-span-4"><Field label="SSH public key(s)" hint="One per line. No password is set: PyXie does not keep secrets in operation records. Without a key, open a shell from Proxmox to set one."><textarea className={input} rows={2} value={String(f.ssh_keys)} onChange={(e) => set("ssh_keys", e.target.value)} placeholder="ssh-ed25519 AAAA… you@host" /></Field></div>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!f.nic_firewall} onChange={(e) => set("nic_firewall", e.target.checked)} /> NIC firewall</label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!f.start_at_boot} onChange={(e) => set("start_at_boot", e.target.checked)} /> Start at host boot</label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!f.start_after} onChange={(e) => set("start_after", e.target.checked)} /> Start it when created</label>
            </div>
          </Card>
          <Card>
            {error && <div className="text-bad text-sm mb-2">{error}</div>}
            {op ? (
              <div>
                <OpPreview op={op} confirmLabel="Confirm & create container" progressText="Creating the container" doneText="The container exists in Proxmox and was read back." onFinished={finished} onClose={() => setOp(null)} />
                {createdId && <div className="mt-2 text-sm"><Link href={`/infrastructure/workloads/${createdId}`} className="text-accent hover:underline">Open the new container →</Link></div>}
              </div>
            ) : <button type="button" disabled={pending} onClick={check} className="px-3 py-1.5 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50 text-sm">{pending ? "Checking the whole cluster…" : "Check everything"}</button>}
          </Card>
        </>
      )}
    </div>
  );
}
