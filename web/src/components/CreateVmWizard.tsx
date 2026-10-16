"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Operation } from "@/lib/api";
import { Card } from "@/components/Card";
import OpPreview from "@/components/OpPreview";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";
import { BIOS, DISK_BUS, DISK_CACHE, MACHINES, NIC_MODELS, OS_TYPES, SCSI_CONTROLLERS, cpuHelp, gbToMb, mbToGb, type Choice } from "@/lib/vmOptions";

type Cluster = { id: string; name: string };
type NodeOpt = { name: string; online: boolean; maintenance: boolean; mem_total: number | null; mem_used: number | null; cpu_model: string | null; storages: { name: string; type: string | null; avail: number | null; shared: boolean }[] };
type Options = {
  nodes: NodeOpt[]; isos: { volid: string; size: number | null; nodes: string[] }[]; bridges: { name: string; vlan_aware: boolean; missing_on: string[] }[];
  cpus: { name: string; vendor: string | null; missing_on: string[] }[]; machines: string[]; pools: string[]; pins: { tag: string; strict: boolean; nodes: string[] }[];
  next_vmid: number | null; errors: string[];
};
type Resolved = { values: Record<string, unknown>; sources: Record<string, string> };
const STEPS = ["Basics", "System", "Disk & media", "Network", "Review"];
const GIB = 1073741824;
const input = "bg-surface border border-border rounded px-2 py-1.5 text-sm w-full";

function Field({ label, hint, children }: { label: React.ReactNode; hint?: string; children: React.ReactNode }) {
  return <div><label className="block text-xs text-muted mb-1">{label}</label>{children}{hint && <div className="text-xs text-muted mt-1">{hint}</div>}</div>;
}
function Check({ label, checked, onChange, hint }: { label: string; checked: boolean; onChange: (v: boolean) => void; hint?: string }) {
  return <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-0.5" checked={checked} onChange={(e) => onChange(e.target.checked)} /><span>{label}{hint && <span className="block text-xs text-muted">{hint}</span>}</span></label>;
}

/** A real dropdown with plain-language names, a help line for the chosen option, and the default (if someone set one) starred and first. */
function Pick({ label, value, onChange, choices, def, extra }: { label: React.ReactNode; value: string; onChange: (v: string) => void; choices: Choice[]; def?: string; extra?: string }) {
  const list = choices.some((c) => c.value === value) || value === "" ? choices : [{ value, label: value, help: "" }, ...choices];
  const ordered = def !== undefined ? [...list.filter((c) => c.value === def), ...list.filter((c) => c.value !== def)] : list;
  const chosen = list.find((c) => c.value === value);
  return (
    <div>
      <label className="block text-xs text-muted mb-1">{label}</label>
      <select className={input} value={value} onChange={(e) => onChange(e.target.value)}>
        {ordered.map((c) => <option key={c.value || "__none"} value={c.value}>{c.value === def ? "★ " : ""}{c.label}{c.value === def ? " (default)" : ""}</option>)}
      </select>
      {(chosen?.help || extra) && <div className="text-xs text-muted mt-1">{chosen?.help}{extra ? ` ${extra}` : ""}</div>}
    </div>
  );
}

/** The trimmed create-VM wizard: one disk, one network device, optional ISO, UEFI/TPM when needed. Everything is checked in a preview first. */
export default function CreateVmWizard() {
  const me = useMe();
  const admin = me === undefined || me?.is_admin === true;
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [templates, setTemplates] = useState<string[]>([]);
  const [clusterId, setClusterId] = useState("");
  const curCluster = useRef(""); // the cluster now chosen: an answer that arrives for another one is ignored
  curCluster.current = clusterId;
  const [template, setTemplate] = useState("");
  const [opts, setOpts] = useState<Options | null>(null);
  const [step, setStep] = useState(0);
  const [f, setF] = useState<Record<string, unknown>>({ name: "", node: "", vmid: "", pool: "", tagsText: "", start_after: false, tpm: false, iso: "", efi_storage: "" });
  const touched = useRef<Set<string>>(new Set());
  const [sources, setSources] = useState<Record<string, string>>({});
  const [defaultVals, setDefaultVals] = useState<Record<string, unknown>>({});
  const [memText, setMemText] = useState("");
  const memEditing = useRef(false);
  const [op, setOp] = useState<Operation | null>(null);
  const [createdId, setCreatedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const set = (k: string, v: unknown) => { touched.current.add(k); setF((p) => ({ ...p, [k]: v })); };
  const tags = useMemo(() => String(f.tagsText || "").split(/[,;\s]+/).filter(Boolean), [f.tagsText]);

  useEffect(() => {
    fetch("/api/defaults").then((r) => r.json()).then((d) => {
      setClusters(d.clusters); if (d.clusters[0]) setClusterId(d.clusters[0].id);
      setTemplates([...new Set<string>(d.defaults.filter((x: { scope_type: string }) => x.scope_type === "template").map((x: { scope_value: string }) => x.scope_value))]);
    }).catch(() => setError("Could not load clusters"));
  }, []);

  useEffect(() => {
    if (!clusterId) return;
    setOpts(null);
    const mine = clusterId;
    fetch(`/api/vm-create/options?cluster_id=${clusterId}`).then((r) => r.json()).then((d) => { if (curCluster.current !== mine) return; d.error ? setError(d.error) : setOpts(d); }).catch(() => setError("Could not read the cluster from Proxmox"));
  }, [clusterId]);

  // Apply the defaults that belong to this cluster / tags / pool / template to every field the person has not touched.
  const tagKey = tags.join(",");
  useEffect(() => {
    if (!clusterId) return;
    const q = new URLSearchParams({ cluster_id: clusterId, tags: tagKey, ...(f.pool ? { pool: String(f.pool) } : {}), ...(template ? { template } : {}), ...(f.ostype ? { ostype: String(f.ostype) } : {}) });
    const mine = clusterId;
    fetch(`/api/defaults/resolve?${q}`).then((r) => r.json()).then((d: Resolved) => {
      if (!d.values || curCluster.current !== mine) return;
      setSources(d.sources); setDefaultVals(d.values);
      setF((prev) => {
        const next = { ...prev };
        for (const [k, v] of Object.entries(d.values)) {
          if (touched.current.has(k) || k === "add_tags" || k === "snapshot_first") continue;
          if (v === null || v === undefined) continue; // nothing is set for this field: keep what the wizard already chose (first datastore, first bridge)
          next[k] = v;
        }
        if (!touched.current.has("tpm")) next.tpm = String(next.ostype) === "win11";
        if (!touched.current.has("tagsText") && Array.isArray(d.values.add_tags) && d.values.add_tags.length && !prev.tagsText) next.tagsText = (d.values.add_tags as string[]).join(", ");
        return next;
      });
    }).catch(() => {});
  }, [clusterId, template, tagKey, f.pool, f.ostype]);

  // Suggest a host: online, not in maintenance, allowed by hard tag pins, most free memory.
  useEffect(() => {
    if (!opts || touched.current.has("node")) return;
    const hard = opts.pins.filter((p) => p.strict && tags.includes(p.tag)).flatMap((p) => [p.nodes]);
    const ok = opts.nodes.filter((n) => n.online && !n.maintenance && hard.every((allowed) => allowed.includes(n.name)));
    const best = [...ok].sort((a, b) => ((b.mem_total ?? 0) - (b.mem_used ?? 0)) - ((a.mem_total ?? 0) - (a.mem_used ?? 0)))[0];
    setF((p) => ({ ...p, node: best?.name ?? "" }));
  }, [opts, tagKey]);

  useEffect(() => { if (!memEditing.current) setMemText(mbToGb(f.memory_mb)); }, [f.memory_mb]);
  const node = opts?.nodes.find((n) => n.name === f.node);
  const nodeStorages = node?.storages ?? [];
  useEffect(() => {
    if (!node || touched.current.has("disk_storage")) return;
    const current = String(f.disk_storage || "");
    if (!nodeStorages.some((s) => s.name === current)) setF((p) => ({ ...p, disk_storage: nodeStorages[0]?.name ?? "" }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node?.name, opts]);
  useEffect(() => {
    if (!opts || touched.current.has("nic_bridge")) return;
    if (!opts.bridges.some((b) => b.name === f.nic_bridge)) setF((p) => ({ ...p, nic_bridge: opts.bridges[0]?.name ?? "" }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opts]);

  const spec = useCallback(() => ({
    name: String(f.name).trim(), node: f.node, vmid: f.vmid ? parseInt(String(f.vmid), 10) : null, pool: f.pool || null, tags,
    ostype: f.ostype, bios: f.bios, machine: f.machine || null, cpu_type: f.cpu_type, cores: Number(f.cores), sockets: Number(f.sockets), memory_mb: Number(f.memory_mb),
    balloon: !!f.balloon, agent: !!f.agent, scsihw: f.scsihw, disk_bus: f.disk_bus, disk_size_gb: Number(f.disk_size_gb), disk_storage: f.disk_storage, disk_cache: f.disk_cache,
    disk_discard: !!f.disk_discard, disk_ssd: !!f.disk_ssd, disk_iothread: !!f.disk_iothread, efi_storage: f.efi_storage || null, tpm: !!f.tpm, iso: f.iso || null,
    nic_model: f.nic_model, nic_bridge: f.nic_bridge, nic_vlan: f.nic_vlan ? parseInt(String(f.nic_vlan), 10) : null, nic_firewall: !!f.nic_firewall,
    start_at_boot: !!f.start_at_boot, start_after: !!f.start_after,
  }), [f, tags]);

  async function check() {
    setPending(true); setError(null);
    try {
      const r = await fetch("/api/vm-create/dry-run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cluster_id: clusterId, spec: spec() }) });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Could not check that"); return; }
      setOp(d); notifyOperationsChanged();
    } finally { setPending(false); }
  }
  async function finished() {
    if (!op) return;
    const r = await fetch(`/api/operations/${op.id}`);
    if (r.ok) {
      const d = (await r.json()) as Operation;
      const ctx = (d.context as { workload_id?: string; vmid?: number } | null) ?? {};
      setCreatedId(ctx.workload_id ?? null);
      const job = clusterJobs.find((j) => j.job_id === backupJob);
      if (job && !job.all_guests && ctx.vmid) {
        try {
          const b = await fetch("/api/protection/backup-jobs/dry-run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cluster_id: clusterId, job_id: job.job_id, add_vmids: [ctx.vmid] }) });
          const bd = await b.json();
          if (b.ok) { setBkOp(bd); notifyOperationsChanged(); } else setBkError(bd.error || "Could not preview adding it to the backup job");
        } catch { setBkError("Could not preview adding it to the backup job"); }
      }
    }
  }

  const iso = opts?.isos.filter((i) => !f.node || i.nodes.includes(String(f.node))) ?? [];
  const free = (n: NodeOpt) => (n.mem_total && n.mem_used != null ? ((n.mem_total - n.mem_used) / GIB).toFixed(0) + " GiB free" : "");
  const dflt = (k: string): string | undefined => (sources[k] && sources[k] !== "built-in" ? String(defaultVals[k] ?? "") : undefined);
  const cpuChoices: Choice[] = (() => {
    const first = ["host", "x86-64-v2-AES", "x86-64-v3", "x86-64-v4"];
    const all = (opts?.cpus ?? []).map((c) => ({ value: c.name, label: c.name + (c.missing_on.length ? ` (not on ${c.missing_on.join(", ")})` : ""), help: cpuHelp(c.name) + (c.missing_on.length ? ` Not available on ${c.missing_on.join(", ")}: a VM using it could not run or move there.` : "") }));
    return [...first.map((n) => all.find((c) => c.value === n)).filter((c): c is Choice => !!c), ...all.filter((c) => !first.includes(c.value)).sort((a, b) => a.value.localeCompare(b.value))];
  })();
  const machineChoices: Choice[] = [...MACHINES, ...(opts?.machines ?? []).filter((m) => !["pc", "q35"].includes(m)).map((m) => ({ value: m, label: m, help: "A specific machine version: the VM keeps the same virtual hardware even after Proxmox is upgraded." }))];
  const [jobs, setJobs] = useState<{ job_id: string; cluster_id: string; storage: string; schedule: string | null; enabled: boolean; all_guests: boolean }[]>([]);
  const [backupJob, setBackupJob] = useState("");
  const [bkOp, setBkOp] = useState<Operation | null>(null);
  const [bkError, setBkError] = useState<string | null>(null);
  useEffect(() => { fetch("/api/protection/backup-jobs").then((r) => (r.ok ? r.json() : [])).then((d) => setJobs(Array.isArray(d) ? d : [])).catch(() => {}); }, []);
  const clusterJobs = jobs.filter((j) => j.cluster_id === clusterId);
  const [dScope, setDScope] = useState("cluster");
  const [dMsg, setDMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const scopeOptions: { key: string; scope: string; value: string | null; label: string }[] = [
    { key: "cluster", scope: "cluster", value: null, label: "Every new VM in this cluster" },
    ...(f.ostype ? [{ key: "os", scope: "os", value: String(f.ostype), label: `Every new ${OS_TYPES.find((o) => o.value === f.ostype)?.label ?? f.ostype}` }] : []),
    ...tags.map((t) => ({ key: "tag:" + t, scope: "tag", value: t, label: `Every VM tagged ${t}` })),
    ...(f.pool ? [{ key: "pool", scope: "pool", value: String(f.pool), label: `Every VM in pool ${f.pool}` }] : []),
  ];
  async function saveDefaults() {
    const target = scopeOptions.find((o) => o.key === dScope) ?? scopeOptions[0];
    const keys = ["machine", "cpu_type", "bios", "cores", "sockets", "memory_mb", "balloon", "agent", "scsihw", "disk_bus", "disk_cache", "disk_discard", "disk_ssd", "disk_iothread", "disk_size_gb", "disk_storage", "nic_model", "nic_bridge", "nic_vlan", "nic_firewall", "start_at_boot"];
    const vals: Record<string, unknown> = {};
    for (const k of keys) { const v = (spec() as Record<string, unknown>)[k] ?? f[k]; if (v !== "" && v !== null && v !== undefined && !(typeof v === "number" && isNaN(v))) vals[k] = k === "nic_vlan" ? Number(v) : v; }
    setDMsg(null);
    try {
      const list = await fetch("/api/defaults").then((r) => r.json());
      const cid = target.scope === "cluster" ? clusterId : null;
      const existing = (list.defaults as { id: string; scope_type: string; scope_value: string | null; cluster_id: string | null; priority: number; description: string | null; values: Record<string, unknown> }[]).find((l) => l.scope_type === target.scope && (l.scope_value ?? null) === target.value && (l.cluster_id ?? null) === cid);
      const body = { scope_type: target.scope, scope_value: target.value, cluster_id: cid, priority: existing?.priority ?? 0, description: existing?.description ?? "Saved from the create-VM wizard", values: { ...(existing?.values ?? {}), ...vals } };
      const r = await fetch(existing ? `/api/defaults/${existing.id}` : "/api/defaults", { method: existing ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const d = await r.json();
      setDMsg(r.ok ? { ok: true, text: `Saved: ${target.label} now starts from the choices on these pages. Change them any time under Workloads > Defaults.` } : { ok: false, text: d.error || "Could not save" });
    } catch { setDMsg({ ok: false, text: "Could not save" }); }
  }
  const src = (k: string) => (sources[k] && sources[k] !== "built-in" ? <span className="text-accent"> · from {sources[k]}</span> : null);
  if (!admin) return <Card><div className="text-sm text-muted">Only administrators can create VMs.</div></Card>;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1 border-b border-border">
        {STEPS.map((s, i) => <button key={s} type="button" onClick={() => { if (op && !["blocked", "awaiting_approval"].includes(op.status)) return; setOp(null); setStep(i); }} className={`px-3 py-2 text-sm -mb-px border-b-2 ${step === i ? "border-accent text-accent" : "border-transparent text-muted hover:text-text"}`}>{i + 1}. {s}</button>)}
      </div>
      {opts?.errors.length ? <div className="text-warn text-xs">Some information could not be read from Proxmox: {opts.errors.join("; ")}</div> : null}
      <Card>
        {!opts ? <div className="text-sm text-muted">{error || "Reading the cluster from Proxmox…"}</div> : (
          <div className="space-y-3">
            {step === 0 && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <Field label="Cluster"><select className={input} value={clusterId} onChange={(e) => { touched.current.clear(); setF((p) => ({ ...p, pool: "", node: "", disk_storage: "", nic_bridge: "" })); setOpts(null); setClusterId(e.target.value); }}>{clusters.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
                <Field label="Template (optional)" hint={templates.length ? "A named set of defaults from Workloads > Defaults & templates." : "No templates yet: create some in Workloads > Defaults & templates."}>
                  <select className={input} value={template} onChange={(e) => { setTemplate(e.target.value); ["ostype", "bios", "machine", "cpu_type", "cores", "sockets", "memory_mb", "balloon", "agent", "scsihw", "disk_bus", "disk_cache", "disk_discard", "disk_ssd", "disk_iothread", "disk_size_gb", "nic_model", "nic_vlan", "nic_firewall", "start_at_boot"].forEach((k) => touched.current.delete(k)); }}>
                    <option value="">None</option>{templates.map((t) => <option key={t}>{t}</option>)}
                  </select>
                </Field>
                <Field label="Name" hint="Letters, digits and hyphens (a DNS-style name).">
                  <input className={input} value={String(f.name)} onChange={(e) => set("name", e.target.value)} placeholder="web01" />
                </Field>
                <Field label="VM ID" hint={`Leave empty for the next free one${opts.next_vmid ? ` (${opts.next_vmid})` : ""}.`}>
                  <input type="number" className={input} value={String(f.vmid)} onChange={(e) => set("vmid", e.target.value)} placeholder="automatic" />
                </Field>
                <Field label="Host" hint="Suggested: online, not in maintenance, allowed by hard tag pins, most free memory.">
                  <select className={input} value={String(f.node)} onChange={(e) => set("node", e.target.value)}>
                    <option value="">Choose a host…</option>
                    {opts.nodes.map((n) => <option key={n.name} value={n.name} disabled={!n.online || n.maintenance}>{n.name}{n.maintenance ? " (maintenance)" : !n.online ? " (offline)" : ""} {free(n)}</option>)}
                  </select>
                </Field>
                <Field label="Tags" hint={opts.pins.some((p) => tags.includes(p.tag)) ? `Pinned: ${opts.pins.filter((p) => tags.includes(p.tag)).map((p) => `${p.tag} → ${p.nodes.join(", ")} (${p.strict ? "hard" : "soft"})`).join("; ")}` : "Comma separated. They also drive pins, affinity rules and tag defaults."}>
                  <input className={input} value={String(f.tagsText)} onChange={(e) => set("tagsText", e.target.value)} placeholder="linux, web" />
                </Field>
                <Field label="Pool (optional)">
                  <select className={input} value={String(f.pool)} onChange={(e) => set("pool", e.target.value)}><option value="">None</option>{opts.pools.map((p) => <option key={p}>{p}</option>)}</select>
                </Field>
              </div>
            )}
            {step === 1 && (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <Pick label={<>Operating system{src("ostype")}</>} value={String(f.ostype)} onChange={(v) => set("ostype", v)} choices={OS_TYPES} def={dflt("ostype")} />
                <Pick label={<>Firmware (BIOS){src("bios")}</>} value={String(f.bios)} onChange={(v) => set("bios", v)} choices={BIOS} def={dflt("bios")} />
                <Pick label={<>Machine type{src("machine")}</>} value={String(f.machine ?? "")} onChange={(v) => set("machine", v)} choices={machineChoices} def={dflt("machine")} />
                <Pick label={<>CPU type{src("cpu_type")}</>} value={String(f.cpu_type)} onChange={(v) => set("cpu_type", v)} choices={cpuChoices} def={dflt("cpu_type")} />
                <Field label="Sockets"><input type="number" className={input} value={String(f.sockets)} onChange={(e) => set("sockets", e.target.value)} /></Field>
                <Field label={<>Cores{src("cores")}</>}><input type="number" className={input} value={String(f.cores)} onChange={(e) => set("cores", e.target.value)} /></Field>
                <Field label={<>Memory (GB){src("memory_mb")}</>} hint="Proxmox counts in GiB: 1 GB here is 1024 MiB. Common sizes: 2, 4, 8, 16."><input type="number" step="0.5" min="0.25" className={input} value={memText} onFocus={() => { memEditing.current = true; }} onBlur={() => { memEditing.current = false; setMemText(mbToGb(f.memory_mb)); }} onChange={(e) => { setMemText(e.target.value); set("memory_mb", e.target.value !== "" && !isNaN(parseFloat(e.target.value)) ? gbToMb(e.target.value) : ""); }} /></Field>
                <Pick label="SCSI controller" value={String(f.scsihw)} onChange={(v) => set("scsihw", v)} choices={SCSI_CONTROLLERS} def={dflt("scsihw")} />
                <div className="space-y-2 pt-5">
                  <Check label="Ballooning" checked={!!f.balloon} onChange={(v) => set("balloon", v)} hint="Lets Proxmox read the guest's real memory use." />
                  <Check label="QEMU guest agent" checked={!!f.agent} onChange={(v) => set("agent", v)} hint="Install the agent in the guest, or shutdowns wait for it." />
                  <Check label="TPM 2.0" checked={!!f.tpm} onChange={(v) => set("tpm", v)} hint="Windows 11 needs it (and UEFI)." />
                </div>
              </div>
            )}
            {step === 2 && (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <Field label="Installation ISO (optional)" hint="What the host's ISO storage holds."><select className={input} value={String(f.iso)} onChange={(e) => set("iso", e.target.value)}><option value="">None</option>{iso.map((i) => <option key={i.volid} value={i.volid}>{i.volid}{i.size ? ` (${(i.size / GIB).toFixed(1)} GiB)` : ""}</option>)}</select></Field>
                <Pick label="Disk bus" value={String(f.disk_bus)} onChange={(v) => set("disk_bus", v)} choices={DISK_BUS} def={dflt("disk_bus")} />
                <Field label={<>Disk size (GiB){src("disk_size_gb")}</>}><input type="number" className={input} value={String(f.disk_size_gb)} onChange={(e) => set("disk_size_gb", e.target.value)} /></Field>
                <Field label={<>Storage{src("disk_storage")}</>} hint={`On ${f.node || "the chosen host"}.`}>
                  <select className={input} value={String(f.disk_storage)} onChange={(e) => set("disk_storage", e.target.value)}>
                    {nodeStorages.map((s) => <option key={s.name} value={s.name}>{s.name} ({s.type}{s.shared ? ", shared" : ""}{s.avail ? `, ${(s.avail / GIB).toFixed(0)} GiB free` : ""})</option>)}
                  </select>
                </Field>
                <Pick label="Cache" value={String(f.disk_cache)} onChange={(v) => set("disk_cache", v)} choices={DISK_CACHE} def={dflt("disk_cache")} />
                <div className="space-y-2 pt-5">
                  <Check label="Discard (TRIM)" checked={!!f.disk_discard} onChange={(v) => set("disk_discard", v)} />
                  <Check label="SSD emulation" checked={!!f.disk_ssd} onChange={(v) => set("disk_ssd", v)} />
                  <Check label="IO thread" checked={!!f.disk_iothread} onChange={(v) => set("disk_iothread", v)} />
                </div>
                {String(f.bios) === "ovmf" && <Field label="EFI disk storage" hint="Empty = the disk's storage."><select className={input} value={String(f.efi_storage)} onChange={(e) => set("efi_storage", e.target.value)}><option value="">Same as the disk</option>{nodeStorages.map((s) => <option key={s.name}>{s.name}</option>)}</select></Field>}
              </div>
            )}
            {step === 3 && (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <Pick label={<>Model{src("nic_model")}</>} value={String(f.nic_model)} onChange={(v) => set("nic_model", v)} choices={NIC_MODELS} def={dflt("nic_model")} />
                <Field label={<>Bridge{src("nic_bridge")}</>}><select className={input} value={String(f.nic_bridge)} onChange={(e) => set("nic_bridge", e.target.value)}>{opts.bridges.map((b) => <option key={b.name} value={b.name}>{b.name}{b.vlan_aware ? " (VLAN-aware)" : ""}{b.missing_on.length ? `  ⚠ not on ${b.missing_on.join(", ")}` : ""}</option>)}</select></Field>
                <Field label={<>VLAN tag{src("nic_vlan")}</>} hint="Empty = untagged."><input type="number" className={input} value={String(f.nic_vlan ?? "")} onChange={(e) => set("nic_vlan", e.target.value)} /></Field>
                <div className="pt-1"><Check label="Firewall" checked={!!f.nic_firewall} onChange={(v) => set("nic_firewall", v)} hint="Only has an effect where the Proxmox firewall is on." /></div>
              </div>
            )}
            {step === 4 && (
              <div className="space-y-3">
                <div className="text-sm text-text">{String(f.name) || "(no name yet)"} on {String(f.node) || "(no host)"}: {Number(f.cores) * Number(f.sockets)} vCPU {String(f.cpu_type)}, {mbToGb(f.memory_mb)} GB RAM, {String(f.disk_size_gb)} GB {String(f.disk_bus)} on {String(f.disk_storage)}, {String(f.nic_model)} on {String(f.nic_bridge)}{f.nic_vlan ? ` VLAN ${f.nic_vlan}` : ""}, {String(f.bios)}{f.tpm ? " + TPM" : ""}{f.iso ? `, boots ${String(f.iso)}` : ""}.</div>
                <div className="flex flex-wrap gap-6">
                  <Check label="Start the VM when it is created" checked={!!f.start_after} onChange={(v) => set("start_after", v)} />
                  <Check label="Start at host boot" checked={!!f.start_at_boot} onChange={(v) => set("start_at_boot", v)} />
                </div>
                {!op && (
                  <Field label="Back up this VM (optional)" hint={backupJob && clusterJobs.find((j) => j.job_id === backupJob)?.all_guests ? "That job already backs up every guest, so the new VM is covered automatically." : "Adds the new VM to a Proxmox Backup Server job once it exists (a second preview and approval). Backups are not scheduled for a VM that is in no job."}>
                    <select className={input + " max-w-md"} value={backupJob} onChange={(e) => setBackupJob(e.target.value)}>
                      <option value="">Not in a backup job</option>
                      {clusterJobs.map((j) => <option key={j.job_id} value={j.job_id}>{j.job_id} → {j.storage}{j.schedule ? ` · ${j.schedule}` : ""}{j.enabled ? "" : " (disabled)"}{j.all_guests ? " · covers every guest" : ""}</option>)}
                    </select>
                  </Field>
                )}
                {!op && (
                  <div className="rounded border border-border p-3 space-y-2">
                    <div className="text-xs text-muted">Make these choices the default (system, disk and network settings from the earlier steps; name, host, ISO and ID are not saved):</div>
                    <div className="flex flex-wrap items-center gap-2">
                      <select className={input + " max-w-xs"} value={dScope} onChange={(e) => setDScope(e.target.value)}>{scopeOptions.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}</select>
                      <button type="button" onClick={saveDefaults} className="px-3 py-1.5 rounded border border-border text-xs text-text hover:bg-surface2">Save as default</button>
                    </div>
                    {dMsg && <div className={`text-xs ${dMsg.ok ? "text-good" : "text-bad"}`}>{dMsg.text}</div>}
                  </div>
                )}
                {error && <div className="text-bad text-sm">{error}</div>}
                {op ? (
                  <div>
                    <OpPreview op={op} confirmLabel="Confirm & create VM" progressText="Creating the VM" doneText="The VM exists in Proxmox and was read back." onFinished={finished} onClose={() => { setOp(null); }} />
                    {createdId && <div className="mt-2 text-sm"><Link href={`/infrastructure/workloads/${createdId}`} className="text-accent hover:underline">Open the new VM →</Link></div>}
                    {bkError && <div className="mt-2 text-xs text-bad">{bkError}</div>}
                    {bkOp && <div className="mt-3 rounded border border-border p-3"><div className="text-xs text-muted mb-1">Next: add it to the backup job</div><OpPreview key={bkOp.id} op={bkOp} confirmLabel="Add to the backup job" progressText="Updating the backup job" doneText="The VM is in the backup job, read back from Proxmox." onFinished={() => notifyOperationsChanged()} onClose={() => setBkOp(null)} /></div>}
                  </div>
                ) : <button type="button" disabled={pending} onClick={check} className="px-3 py-1.5 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50 text-sm">{pending ? "Checking the whole cluster…" : "Check everything"}</button>}
              </div>
            )}
          </div>
        )}
      </Card>
      {!op && (
        <div className="flex gap-2">
          <button type="button" disabled={step === 0} onClick={() => setStep(step - 1)} className="px-3 py-1.5 rounded border border-border text-sm text-muted hover:text-text disabled:opacity-40">Back</button>
          {step < 4 && <button type="button" onClick={() => setStep(step + 1)} className="px-3 py-1.5 rounded border border-border text-sm text-text hover:bg-surface2">Next</button>}
        </div>
      )}
    </div>
  );
}
