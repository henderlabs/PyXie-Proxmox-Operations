"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, CardTitle } from "@/components/Card";
import { useMe } from "@/lib/useMe";
import { BIOS, DISK_BUS, DISK_CACHE, MACHINES, NIC_MODELS, OS_TYPES, SCSI_CONTROLLERS, cpuHelp, type Choice } from "@/lib/vmOptions";

type Layer = { id: string; scope_type: "cluster" | "os" | "tag" | "pool" | "template"; scope_value: string | null; cluster_id: string | null; priority: number; description: string | null; values: Record<string, unknown>; updated_at: string | null };
type Options = {
  defaults: Layer[]; builtin: Record<string, unknown>; fields: Record<string, { label: string; group: string }>;
  clusters: { id: string; name: string }[]; tags: { tag: string; count: number }[]; pools: { pool: string; count: number }[]; storages: string[];
};

type Spec = { type: "text" | "int" | "bool" | "select" | "tags"; options?: string[]; list?: "storages" | "tags"; placeholder?: string };
const SPEC: Record<string, Spec> = {
  machine: { type: "text", placeholder: "pc, q35 or pc-q35-9.0", options: ["pc", "q35"] }, cpu_type: { type: "text", options: ["x86-64-v2-AES", "x86-64-v3", "x86-64-v4", "host", "kvm64", "max"] },
  bios: { type: "select", options: ["seabios", "ovmf"] }, ostype: { type: "text", options: ["l26", "win11", "win10", "win2k22", "win2k19", "other"] },
  cores: { type: "int" }, sockets: { type: "int" }, memory_mb: { type: "int" }, balloon: { type: "bool" }, agent: { type: "bool" },
  scsihw: { type: "select", options: ["virtio-scsi-single", "virtio-scsi-pci", "lsi", "lsi53c810", "megasas", "pvscsi"] },
  disk_bus: { type: "select", options: ["scsi", "virtio", "sata", "ide"] }, disk_cache: { type: "select", options: ["none", "directsync", "writethrough", "writeback", "unsafe"] },
  disk_discard: { type: "bool" }, disk_ssd: { type: "bool" }, disk_iothread: { type: "bool" }, disk_storage: { type: "text", list: "storages" }, disk_size_gb: { type: "int" },
  nic_model: { type: "select", options: ["virtio", "e1000", "e1000e", "rtl8139", "vmxnet3"] }, nic_bridge: { type: "text", placeholder: "vmbr0" }, nic_vlan: { type: "int" }, nic_firewall: { type: "bool" },
  start_at_boot: { type: "bool" }, snapshot_first: { type: "bool" }, add_tags: { type: "tags", placeholder: "tag1, tag2" },
};
const GROUPS = ["Compute", "Disk", "Network", "Other"];
// Plain-language names and a help line for every field that offers a fixed set of choices.
const CHOICES: Record<string, Choice[]> = { ostype: OS_TYPES, bios: BIOS, machine: MACHINES.filter((m) => m.value !== ""), scsihw: SCSI_CONTROLLERS, disk_bus: DISK_BUS, disk_cache: DISK_CACHE, nic_model: NIC_MODELS };
const FIELD_HELP: Record<string, string> = {
  cores: "Virtual CPU cores per socket. Start small: more cores than the VM uses just adds scheduling overhead.", sockets: "Virtual CPU sockets. Leave at 1 unless software licensing or NUMA needs more.",
  balloon: "Lets Proxmox take unused memory back from the guest when the host is short. Needs the guest agent or balloon driver.", agent: "QEMU guest agent: lets Proxmox see the guest's IP addresses and shut it down cleanly. Install it in the guest too.",
  disk_discard: "TRIM: tells thin-provisioned storage which blocks are free, so space comes back after deletes.", disk_ssd: "Presents the disk to the guest as an SSD (the guest then uses SSD-friendly behaviour). Does not make it faster.", disk_iothread: "Gives the disk its own thread, so heavy disk use does not slow the rest of the VM. Best with VirtIO SCSI single.",
  disk_storage: "The datastore new VM disks are created on. Must exist on the host the VM is created on.", disk_size_gb: "Size of the first disk in GiB (1 GiB = 1024 MiB of space).",
  nic_bridge: "The virtual switch the VM's network card plugs into (for example vmbr0).", nic_vlan: "VLAN tag for the VM's traffic. Leave unset for untagged. The bridge must be VLAN-aware.", nic_firewall: "Turns on the Proxmox firewall for this network card. With a datacenter default of drop it can cut a VM off until rules exist.",
  start_at_boot: "Start the VM automatically when its host boots.", snapshot_first: "Take a snapshot before risky hardware changes (machine or CPU type) so they can be undone.", add_tags: "Tags put on every new VM. Tags also drive affinity rules, pins and these defaults.", memory_mb: "RAM in GB (1 GB = 1024 MiB, as Proxmox counts).",
};
function helpFor(k: string, v: unknown): string {
  if (k === "cpu_type") return typeof v === "string" && v ? cpuHelp(v) : "";
  const c = CHOICES[k]?.find((x) => x.value === v);
  return c?.help || FIELD_HELP[k] || "";
}
const labelOf = (k: string, label: string) => (k === "memory_mb" ? "Memory (GB)" : label);
const SCOPES: { id: Layer["scope_type"]; label: string; hint: string }[] = [
  { id: "cluster", label: "Cluster", hint: "Applies to every VM in one cluster (or in all of them)." },
  { id: "os", label: "OS type", hint: "Applies to VMs of one operating system (for example Windows 11, or Linux). Overrides the cluster; tags, pools and templates override it." },
  { id: "tag", label: "Tag", hint: "Applies to VMs carrying this tag. Overrides the cluster; with two matching tags the higher priority wins." },
  { id: "pool", label: "Pool", hint: "Applies to VMs in this Proxmox resource pool. Overrides cluster and tag." },
  { id: "template", label: "Template", hint: "A named set you pick by hand when creating a VM. Overrides everything else." },
];

function show(v: unknown, k?: string): string {
  if (k === "memory_mb" && typeof v === "number") return `${Math.round((v / 1024) * 100) / 100} GB`;
  return Array.isArray(v) ? v.join(", ") : v === true ? "on" : v === false ? "off" : v === null || v === undefined ? "—" : String(v);
}

export default function DefaultsManager() {
  const me = useMe();
  const admin = me === undefined || me?.is_admin === true;
  const [data, setData] = useState<Options | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Layer | "new" | null>(null);

  const load = useCallback(async () => {
    const r = await fetch("/api/defaults");
    if (!r.ok) { setError((await r.json().catch(() => ({}))).error || "Could not load"); return; }
    setData(await r.json());
  }, []);
  useEffect(() => { load(); }, [load]);

  async function remove(l: Layer) {
    if (!window.confirm(`Delete the ${l.scope_type} default${l.scope_value ? ` "${l.scope_value}"` : ""}?`)) return;
    const r = await fetch(`/api/defaults/${l.id}`, { method: "DELETE" });
    if (!r.ok) setError((await r.json().catch(() => ({}))).error || "Delete failed");
    load();
  }

  if (error && !data) return <Card><div className="text-bad text-sm">{error}</div></Card>;
  if (!data) return <Card><div className="text-sm text-muted">Loading…</div></Card>;
  const clusterName = (id: string | null) => (id ? data.clusters.find((c) => c.id === id)?.name ?? "?" : "all clusters");
  return (
    <div className="space-y-4">
      <Card>
        <p className="text-sm text-muted">
          Defaults are what PyXie suggests for a VM&apos;s machine type, CPU type, disks and network card. Set them for a <span className="text-text">cluster</span>, for every VM with a <span className="text-text">tag</span> or in a Proxmox <span className="text-text">pool</span>, or as a named <span className="text-text">template</span> you pick by hand. A more specific layer overrides a broader one, field by field; anything you do not set is inherited, down to PyXie&apos;s built-in starting values (Proxmox&apos;s own wizard values).
          They prefill the hardware and network edit dialogs, flag VMs that differ from them, and are what the create-VM wizard starts from. They are PyXie settings and change nothing in Proxmox by themselves.
        </p>
      </Card>
      {error && <div className="text-bad text-sm">{error}</div>}
      {editing ? (
        <LayerEditor key={editing === "new" ? "new" : editing.id} data={data} layer={editing === "new" ? null : editing} onClose={(changed) => { setEditing(null); if (changed) load(); }} />
      ) : (
        <Card>
          <div className="flex items-center justify-between mb-2">
            <CardTitle>Defaults and templates · {data.defaults.length}</CardTitle>
            {admin && <button type="button" onClick={() => setEditing("new")} className="px-2 py-1 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 text-xs">Add a default</button>}
          </div>
          {data.defaults.length === 0 ? (
            <div className="text-sm text-muted">Nothing set yet: every VM starts from the built-in values. Add a cluster default to begin, for example the CPU type and machine type you want on every new VM.</div>
          ) : (
            <table className="w-full text-sm">
              <thead><tr className="text-left text-xs text-muted uppercase"><th className="py-1 pr-3">Applies to</th><th className="pr-3">Where</th><th className="pr-3">Sets</th><th /></tr></thead>
              <tbody>
                {data.defaults.map((l) => (
                  <tr key={l.id} className="border-t border-border align-top">
                    <td className="py-1.5 pr-3 text-text whitespace-nowrap">{l.scope_type === "os" ? "OS type" : l.scope_type}{l.scope_value ? `: ${l.scope_type === "os" ? OS_TYPES.find((o) => o.value === l.scope_value)?.label ?? l.scope_value : l.scope_value}` : ""}{l.scope_type === "tag" && l.priority ? <span className="text-muted text-xs"> (priority {l.priority})</span> : null}</td>
                    <td className="pr-3 text-muted whitespace-nowrap">{clusterName(l.cluster_id)}</td>
                    <td className="pr-3 text-xs text-muted">{Object.entries(l.values).map(([k, v]) => `${labelOf(k, data.fields[k]?.label ?? k)}: ${show(v, k)}`).join(" · ")}{l.description ? <div className="mt-0.5 italic">{l.description}</div> : null}</td>
                    <td className="text-right whitespace-nowrap">{admin && <><button type="button" onClick={() => setEditing(l)} className="text-xs text-accent hover:underline mr-3">Edit</button><button type="button" onClick={() => remove(l)} className="text-xs text-bad hover:underline">Delete</button></>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}
      <Card>
        <details>
          <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wider text-muted">Built-in starting values (what applies when nothing above says otherwise)</summary>
          <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-8 mt-2 text-sm">
            {Object.entries(data.fields).map(([k, f]) => <div key={k} className="flex justify-between gap-4 py-0.5"><dt className="text-muted">{labelOf(k, f.label)}</dt><dd className="text-text text-right">{show(data.builtin[k], k)}</dd></div>)}
          </dl>
        </details>
      </Card>
    </div>
  );
}

function LayerEditor({ data, layer, onClose }: { data: Options; layer: Layer | null; onClose: (changed: boolean) => void }) {
  const [scope, setScope] = useState<Layer["scope_type"]>(layer?.scope_type ?? "cluster");
  const [value, setValue] = useState(layer?.scope_value ?? "");
  const [cluster, setCluster] = useState(layer?.cluster_id ?? "");
  const [priority, setPriority] = useState(String(layer?.priority ?? 0));
  const [description, setDescription] = useState(layer?.description ?? "");
  const [vals, setVals] = useState<Record<string, unknown>>(layer?.values ?? {});
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const input = "bg-surface border border-border rounded px-2 py-1 text-sm";

  function toggle(k: string, on: boolean) {
    setVals((v) => {
      const n = { ...v };
      if (!on) { delete n[k]; return n; }
      const b = data.builtin[k];
      n[k] = SPEC[k].type === "bool" ? Boolean(b) : SPEC[k].type === "int" ? Number(b ?? 1) || 1 : SPEC[k].type === "tags" ? [] : SPEC[k].type === "select" ? (b ?? SPEC[k].options![0]) : (b ?? "");
      return n;
    });
  }
  async function save() {
    setPending(true); setError(null);
    try {
      const body = { scope_type: scope, scope_value: scope === "cluster" ? null : value.trim(), cluster_id: cluster || null, priority: parseInt(priority, 10) || 0, description: description.trim() || null, values: vals };
      const r = await fetch(layer ? `/api/defaults/${layer.id}` : "/api/defaults", { method: layer ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Could not save"); return; }
      onClose(true);
    } finally { setPending(false); }
  }

  return (
    <Card>
      <CardTitle>{layer ? "Edit default" : "Add a default"}</CardTitle>
      <div className="space-y-3 text-sm">
        <div className="flex flex-wrap gap-3">
          {SCOPES.map((s) => <label key={s.id} className="flex items-center gap-1.5"><input type="radio" checked={scope === s.id} onChange={() => setScope(s.id)} />{s.label}</label>)}
        </div>
        <div className="text-xs text-muted">{SCOPES.find((s) => s.id === scope)?.hint}</div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <div>
            <label className="block text-xs text-muted mb-1">Cluster</label>
            <select className={`${input} w-full`} value={cluster} onChange={(e) => setCluster(e.target.value)}>
              <option value="">All clusters</option>
              {data.clusters.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
          {scope !== "cluster" && (
            <div>
              <label className="block text-xs text-muted mb-1">{scope === "os" ? "Operating system" : scope === "tag" ? "Tag" : scope === "pool" ? "Pool" : "Template name"}</label>
              {scope === "os" ? <select className={`${input} w-full`} value={value} onChange={(e) => setValue(e.target.value)}><option value="">Choose…</option>{OS_TYPES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select> : <input className={`${input} w-full`} list="defaults-scope-values" value={value} onChange={(e) => setValue(e.target.value)} placeholder={scope === "template" ? "Windows 11" : ""} />}
              <datalist id="defaults-scope-values">
                {scope === "tag" && data.tags.map((t) => <option key={t.tag} value={t.tag} label={`${t.count} VMs`} />)}
                {scope === "pool" && data.pools.map((p) => <option key={p.pool} value={p.pool} label={`${p.count} VMs`} />)}
              </datalist>
            </div>
          )}
          {scope === "tag" && (
            <div>
              <label className="block text-xs text-muted mb-1">Priority (higher wins between tags)</label>
              <input type="number" className={`${input} w-full`} value={priority} onChange={(e) => setPriority(e.target.value)} />
            </div>
          )}
          <div className="md:col-span-1">
            <label className="block text-xs text-muted mb-1">Note (optional)</label>
            <input className={`${input} w-full`} value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>
        </div>
        <div className="text-xs text-muted">Tick a setting to set it here; leave it unticked to inherit it from a broader layer.</div>
        {GROUPS.map((g) => (
          <fieldset key={g} className="border border-border rounded p-2">
            <legend className="px-1 text-xs uppercase tracking-wider text-muted">{g}</legend>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-1.5">
              {Object.entries(data.fields).filter(([, f]) => f.group === g).map(([k, f]) => {
                const on = k in vals;
                const spec = SPEC[k];
                return (
                  <div key={k}>
                  <div className="flex items-center gap-2">
                    <input type="checkbox" checked={on} onChange={(e) => toggle(k, e.target.checked)} aria-label={`Set ${labelOf(k, f.label)}`} />
                    <span className={`w-48 shrink-0 ${on ? "text-text" : "text-muted"}`}>{labelOf(k, f.label)}</span>
                    {on && spec.type === "bool" && <label className="flex items-center gap-1"><input type="checkbox" checked={Boolean(vals[k])} onChange={(e) => setVals({ ...vals, [k]: e.target.checked })} />{vals[k] ? "on" : "off"}</label>}
                    {on && k === "memory_mb" && <input type="number" step="0.5" min="0.25" className={`${input} w-28`} defaultValue={Number(vals[k]) / 1024} onBlur={(e) => setVals({ ...vals, [k]: Math.max(128, Math.round((parseFloat(e.target.value) || 0) * 1024)) })} />}
                    {on && spec.type === "int" && k !== "memory_mb" && <input type="number" className={`${input} w-28`} value={Number(vals[k])} onChange={(e) => setVals({ ...vals, [k]: parseInt(e.target.value, 10) || 0 })} />}
                    {on && (spec.type === "select" || (CHOICES[k] && spec.type === "text")) && <select className={input} value={String(vals[k])} onChange={(e) => setVals({ ...vals, [k]: e.target.value })}>{(CHOICES[k] ? [...new Set([String(vals[k]), ...CHOICES[k].map((c) => c.value)])] : spec.options!).map((o) => <option key={o} value={o}>{CHOICES[k]?.find((c) => c.value === o)?.label ?? o}</option>)}</select>}
                    {on && spec.type === "text" && !CHOICES[k] && (<><input className={`${input} w-44`} list={`dl-${k}`} placeholder={spec.placeholder} value={String(vals[k] ?? "")} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} /><datalist id={`dl-${k}`}>{(spec.list === "storages" ? data.storages : spec.options ?? []).map((o) => <option key={o} value={o} />)}</datalist></>)}
                    {on && spec.type === "tags" && <input className={`${input} w-44`} placeholder={spec.placeholder} value={(vals[k] as string[]).join(", ")} onChange={(e) => setVals({ ...vals, [k]: e.target.value.split(/[,;\s]+/).filter(Boolean) })} />}
                    {!on && <span className="text-xs text-muted">inherits ({show(data.builtin[k], k)} built-in)</span>}
                  </div>
                  {helpFor(k, on ? vals[k] : data.builtin[k]) && <div className="text-xs text-muted ml-6 mt-0.5">{helpFor(k, on ? vals[k] : data.builtin[k])}</div>}
                  </div>
                );
              })}
            </div>
          </fieldset>
        ))}
        {error && <div className="text-bad">{error}</div>}
        <div className="flex gap-2 items-center">
          <button type="button" disabled={pending} onClick={save} className="px-3 py-1.5 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50 text-xs">{pending ? "Saving…" : "Save"}</button>
          <button type="button" onClick={() => onClose(false)} className="text-xs text-muted hover:text-text">cancel</button>
        </div>
      </div>
    </Card>
  );
}
