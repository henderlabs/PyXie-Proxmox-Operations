"use client";

import { useEffect, useState } from "react";
import type { Operation } from "@/lib/api";
import OpPreview from "@/components/OpPreview";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";

/** "100G" / "512M" / "1.5T" -> GiB. */
function toGiB(text: string | null | undefined): number | null {
  const m = String(text ?? "").trim().match(/^(\d+(?:\.\d+)?)([KMGT]?)$/);
  if (!m) return null;
  const mult: Record<string, number> = { "": 1 / 1073741824, K: 1 / 1048576, M: 1 / 1024, G: 1, T: 1024 };
  return parseFloat(m[1]) * mult[m[2]];
}

async function dryRun(path: string, body: unknown): Promise<{ op?: Operation; error?: string }> {
  try {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json().catch(() => ({}));
    return r.ok ? { op: d } : { error: d.error || "Could not check that" };
  } catch {
    return { error: "Could not reach PyXie to check that. Try again." };
  }
}

/** Grow one disk: a disk can only get bigger, never smaller. */
export function GrowDiskAction({ workloadId, disk, sizeText, onDone }: { workloadId: string; disk: string; sizeText: string | null; onDone: () => void }) {
  const me = useMe();
  const current = toGiB(sizeText);
  const [open, setOpen] = useState(false);
  const [size, setSize] = useState("");
  const [op, setOp] = useState<Operation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  if (me !== undefined && me?.is_admin !== true) return null;
  if (current == null) return null;

  async function preview() {
    setPending(true); setError(null);
    const n = parseInt(size, 10);
    const r = await dryRun("/api/operations/workload-disk-grow/dry-run", { workload_id: workloadId, disk, new_size_gb: n });
    setPending(false);
    if (r.error) { setError(r.error); return; }
    setOp(r.op!); notifyOperationsChanged();
  }
  function close() { setOpen(false); setOp(null); setError(null); onDone(); }

  if (!open) return <button type="button" onClick={() => { setSize(String(Math.ceil(current!) + 10)); setOpen(true); }} className="text-xs text-accent hover:underline">Grow…</button>;
  return (
    <div className="mt-1 text-xs">
      {op ? (
        <OpPreview op={op} confirmLabel="Confirm & grow disk" progressText={`Growing ${disk}`} doneText={`${disk} grown and read back from Proxmox. Now extend the partition and filesystem inside the guest.`} onFinished={onDone} onClose={close} />
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-muted">New size</span>
          <input type="number" min={Math.floor(current!) + 1} value={size} onChange={(e) => setSize(e.target.value)} className="bg-surface border border-border rounded px-2 py-1 w-24" />
          <span className="text-muted">GiB (now {Math.round(current!)} GiB; can never shrink)</span>
          <button type="button" disabled={pending || !size} onClick={preview} className="px-2 py-1 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50">{pending ? "Checking…" : "Preview"}</button>
          <button type="button" onClick={() => setOpen(false)} className="text-muted hover:text-text">cancel</button>
          {error && <div className="w-full text-bad">{error}</div>}
        </div>
      )}
    </div>
  );
}

type HwOptions = {
  supported: boolean; machine: string | null; cpu: string | null; can_snapshot: boolean | null; error: string | null;
  machines: { id: string; missing_on: string[] }[]; cpus: { name: string; vendor: string | null; missing_on: string[] }[];
  hosts: { name: string; is_current: boolean; cpu_model: string | null; readable: boolean }[];
};

/** Change a VM's machine type and/or CPU type, checked against every host, with an optional safety snapshot and power cycle. */
export function MachineCpuAction({ workloadId, workloadName, running, onDone }: { workloadId: string; workloadName: string; running: boolean; onDone: () => void }) {
  const me = useMe();
  const [open, setOpen] = useState(false);
  const [opts, setOpts] = useState<HwOptions | null>(null);
  const [machine, setMachine] = useState("");
  const [cpu, setCpu] = useState("");
  const [snap, setSnap] = useState(true);
  const [cycle, setCycle] = useState(false);
  const [op, setOp] = useState<Operation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [defs, setDefs] = useState<{ values: Record<string, unknown>; sources: Record<string, string> } | null>(null);

  useEffect(() => {
    if (!open) return;
    Promise.all([fetch(`/api/workloads/${workloadId}/hw-options`).then((r) => r.json()), fetch(`/api/workloads/${workloadId}/defaults`).then((r) => (r.ok ? r.json() : null)).catch(() => null)])
      .then(([d, df]: [HwOptions, { values: Record<string, unknown>; sources: Record<string, string> } | null]) => { setOpts(d); setDefs(df); setSnap(d.can_snapshot === false ? false : df ? Boolean(df.values.snapshot_first) : true); })
      .catch(() => setError("Could not read the options from Proxmox"));
  }, [open, workloadId]);
  if (me !== undefined && me?.is_admin !== true) return null;

  async function preview() {
    setPending(true); setError(null);
    const r = await dryRun("/api/operations/workload-hw-change/dry-run", { workload_id: workloadId, machine: machine || null, cpu_type: cpu || null, snapshot_first: snap, power_cycle: running && cycle });
    setPending(false);
    if (r.error) { setError(r.error); return; }
    setOp(r.op!); notifyOperationsChanged();
  }
  function close() { setOpen(false); setOp(null); setError(null); setMachine(""); setCpu(""); onDone(); }

  if (!open) return <button type="button" onClick={() => setOpen(true)} className="text-xs text-accent hover:underline">Edit machine &amp; CPU type…</button>;
  const label = (n: string, missing: string[]) => (missing.length ? `${n}  ⚠ not on ${missing.join(", ")}` : n);
  const sel = "bg-surface border border-border rounded px-2 py-1.5 text-sm w-full";
  return (
    <div className="basis-full w-full mt-2 mb-3 border border-border rounded p-3 bg-surface2/40 text-xs max-w-xl">
      <div className="font-medium text-text mb-1">Machine and CPU type for {workloadName}</div>
      {op ? (
        <OpPreview op={op} danger confirmLabel={cycle && running ? "Confirm & apply (power-cycles the VM)" : "Confirm & change"} progressText="Changing the hardware" doneText={running && !cycle ? "Saved in Proxmox. It takes effect the next time the VM is shut down and started." : "Done and read back from Proxmox."} onFinished={onDone} onClose={close} />
      ) : !opts ? (
        <div className="text-muted">{error || "Reading what every host in the cluster can run…"}</div>
      ) : !opts.supported ? (
        <div className="text-muted">Only VMs have these options.</div>
      ) : (
        <div className="space-y-2">
          <p className="text-muted">The choice is checked against every host in the cluster: its real CPU flags and the types its QEMU offers. A type marked ⚠ is missing on that host, so the VM could not move there.</p>
          <div>
            <label className="block text-muted mb-1">Machine type (now {opts.machine || "default (i440fx)"})</label>
            {defs && defs.sources.machine !== "built-in" && <div className="text-muted mb-1">Your default: {String(defs.values.machine ?? "pc")} ({defs.sources.machine})</div>}
            <select className={sel} value={machine} onChange={(e) => setMachine(e.target.value)}>
              <option value="">Keep as is</option>
              {opts.machines.filter((m) => m.id !== opts.machine).map((m) => <option key={m.id} value={m.id}>{label(m.id, m.missing_on)}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-muted mb-1">CPU type (now {opts.cpu || "default (kvm64)"})</label>
            {defs && defs.sources.cpu_type !== "built-in" && <div className="text-muted mb-1">Your default: {String(defs.values.cpu_type)} ({defs.sources.cpu_type})</div>}
            <select className={sel} value={cpu} onChange={(e) => setCpu(e.target.value)}>
              <option value="">Keep as is</option>
              {opts.cpus.filter((c) => c.name !== opts.cpu).map((c) => <option key={c.name} value={c.name}>{label(c.name + (c.vendor && c.vendor !== "default" ? ` (${c.vendor})` : ""), c.missing_on)}</option>)}
            </select>
          </div>
          <label className="flex items-center gap-2"><input type="checkbox" checked={snap} disabled={opts.can_snapshot === false} onChange={(e) => setSnap(e.target.checked)} /> Take a snapshot first (recommended: lets you undo it){opts.can_snapshot === false ? " — Proxmox says this VM cannot be snapshotted" : ""}</label>
          {running && <label className="flex items-center gap-2"><input type="checkbox" checked={cycle} onChange={(e) => setCycle(e.target.checked)} /> Shut the VM down and start it again now to apply it (about a minute off); otherwise it applies at its next shutdown and start</label>}
          {error && <div className="text-bad">{error}</div>}
          <div className="flex gap-2 items-center">
            <button type="button" disabled={pending || (!machine && !cpu)} onClick={preview} className="px-2 py-1 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50">{pending ? "Checking every host…" : "Preview"}</button>
            <button type="button" onClick={() => setOpen(false)} className="text-muted hover:text-text">cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}
