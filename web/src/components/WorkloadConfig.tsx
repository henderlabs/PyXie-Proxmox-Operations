"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { Operation } from "@/lib/api";
import OpPreview from "@/components/OpPreview";
import { useMe } from "@/lib/useMe";

type Field = {
  key: string; label: string; group: string; type: "text" | "bool" | "int" | "float" | "enum" | "regex" | "startup"; apply: "live" | "restart";
  priv: string; priv_ok: boolean | null; value: string | null; startup: { order?: number; up?: number; down?: number } | null;
  pending: string | null; choices?: string[] | null; min?: number | null; max?: number | null; hint?: string | null; max_length?: number | null;
};
type ConfigData = {
  workload_id: string; name: string | null; vmid: number; kind: "vm" | "ct"; status: string; node: string; digest: string | null; lock: string | null;
  privileges_known: boolean; groups: string[]; fields: Field[]; own_panel: { key: string; where: string; value: unknown }[];
  pending: { key: string; value: unknown; pending: unknown; delete: boolean }[];
};
type Edit = string | { order?: string; up?: string; down?: string };
type ChangeRow = { key: string; label: string; old: string | null; new: string | null; remove: boolean; applies: "live" | "restart" | "now"; priv_ok: boolean | null };

const show = (f: Field, v: string | null) => (v === null || v === "" ? "not set" : f.type === "bool" ? (v === "1" ? "Yes" : "No") : v);

function initialEdit(f: Field): Edit {
  if (f.type === "startup") return { order: f.startup?.order?.toString() ?? "", up: f.startup?.up?.toString() ?? "", down: f.startup?.down?.toString() ?? "" };
  return f.value ?? "";
}

/** The Config tab: every option PyXie may change on this guest, each with what changing it does (live / after restart), a single
 * preview -> approve -> verify path for any number of edits, and the pending (restart-waiting) changes with Revert and Restart. */
export default function WorkloadConfig({ workloadId, canEdit, onChanged }: { workloadId: string; canEdit: boolean; onChanged?: () => void }) {
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  const [data, setData] = useState<ConfigData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, Edit>>({});
  const [op, setOp] = useState<Operation | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const r = await fetch(`/api/operations/workload-config/${workloadId}`);
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `Could not read the configuration (${r.status})`);
      setData(d as ConfigData);
      setEdits({});
    } catch (e) {
      setError((e as Error).message);
    }
  }, [workloadId]);
  useEffect(() => { load(); }, [load]);

  const dirty = useMemo(() => {
    if (!data) return {} as Record<string, Edit>;
    const out: Record<string, Edit> = {};
    for (const f of data.fields) {
      if (!(f.key in edits)) continue;
      if (JSON.stringify(edits[f.key]) !== JSON.stringify(initialEdit(f))) out[f.key] = edits[f.key];
    }
    return out;
  }, [data, edits]);
  const dirtyCount = Object.keys(dirty).length;

  async function preview(body: { changes?: Record<string, unknown>; revert?: string[] }) {
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/operations/workload-config/dry-run", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workload_id: workloadId, changes: body.changes ?? {}, revert: body.revert ?? [] }),
      });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Could not check that"); return; }
      setOp(d as Operation);
    } finally {
      setBusy(false);
    }
  }

  async function previewRestart() {
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/operations/workload-lifecycle/dry-run", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workload_id: workloadId, action: "reboot" }),
      });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Could not check that"); return; }
      setOp(d as Operation);
    } finally {
      setBusy(false);
    }
  }

  function done() {
    setOp(null);
    load();
    onChanged?.();
  }

  if (error && !data) return <div className="text-sm text-bad">{error}</div>;
  if (!data) return <div className="text-sm text-muted">Reading the configuration from Proxmox…</div>;

  const editable = canEdit && isAdmin && !data.lock;
  const running = data.status === "running";
  const rows = (op?.dry_run_result as { changes?: ChangeRow[] } | null)?.changes ?? [];
  const chip = (f: Field) =>
    f.priv_ok === false ? <span className="text-[10px] px-1.5 py-0.5 rounded border border-bad/50 text-bad" title={`The Proxmox account PyXie uses lacks ${f.priv}`}>needs {f.priv}</span>
    : f.apply === "live" ? <span className="text-[10px] px-1.5 py-0.5 rounded border border-good/50 text-good" title="Takes effect immediately">Live</span>
    : <span className="text-[10px] px-1.5 py-0.5 rounded border border-warn/50 text-warn" title="Saved as a pending change; takes effect when the guest is restarted">On next restart</span>;

  function editor(f: Field) {
    const cur = f.key in edits ? edits[f.key] : initialEdit(f);
    const set = (v: Edit) => setEdits((e) => ({ ...e, [f.key]: v }));
    const cls = "bg-surface2 border border-border rounded px-2 py-1 text-sm disabled:opacity-60";
    if (f.type === "bool") {
      return (
        <select className={cls} disabled={!editable} value={cur as string} onChange={(e) => set(e.target.value)}>
          <option value="">not set</option><option value="1">Yes</option><option value="0">No</option>
        </select>
      );
    }
    if (f.type === "enum") {
      return (
        <select className={cls} disabled={!editable} value={cur as string} onChange={(e) => set(e.target.value)}>
          <option value="">not set (default)</option>
          {(f.choices || []).map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      );
    }
    if (f.type === "startup") {
      const s = cur as { order?: string; up?: string; down?: string };
      const n = (k: "order" | "up" | "down", label: string) => (
        <label className="text-xs text-muted mr-2">{label}{" "}
          <input className={`${cls} w-16`} type="number" min={0} disabled={!editable} value={s[k] ?? ""} onChange={(e) => set({ ...s, [k]: e.target.value })} />
        </label>
      );
      return <div>{n("order", "order")}{n("up", "up delay (s)")}{n("down", "down delay (s)")}</div>;
    }
    if (f.type === "text") {
      return <textarea className={`${cls} w-full max-w-xl`} rows={2} maxLength={f.max_length ?? 8192} disabled={!editable} value={cur as string} onChange={(e) => set(e.target.value)} />;
    }
    return (
      <input className={`${cls} w-64`} type={f.type === "int" || f.type === "float" ? "number" : "text"} step={f.type === "float" ? "any" : undefined}
        min={f.min ?? undefined} max={f.max ?? undefined} placeholder={f.hint ?? ""} disabled={!editable} value={cur as string} onChange={(e) => set(e.target.value)} />
    );
  }

  return (
    <div className="space-y-4">
      {data.lock && (
        <div className="border border-warn/50 bg-warn/10 rounded p-2 text-xs">
          This guest is locked by Proxmox ({data.lock}), so changes are paused. If no task is running for it, see the Health page for a stale-lock finding.
        </div>
      )}
      {!data.privileges_known && <div className="text-xs text-muted">PyXie could not check which Proxmox privileges its account has; a change that needs one it lacks will be refused at preview or approval.</div>}
      {data.pending.length > 0 && (
        <div className="border border-warn/50 bg-warn/10 rounded p-2 text-xs">
          <span className="font-medium text-text">{data.pending.length} change{data.pending.length === 1 ? "" : "s"} waiting for a restart:</span>{" "}
          {data.pending.map((p) => `${p.key} ${String(p.value ?? "not set")} → ${p.delete ? "removed" : String(p.pending)}`).join(" · ")}
          {isAdmin && canEdit && (
            <span className="ml-3 inline-flex gap-3">
              {running && <button type="button" disabled={busy} onClick={previewRestart} className="text-accent hover:underline disabled:opacity-50">Restart now…</button>}
              <button type="button" disabled={busy} onClick={() => preview({ revert: data.pending.map((p) => p.key) })} className="text-accent hover:underline disabled:opacity-50">Revert {data.pending.length === 1 ? "it" : "all"}…</button>
            </span>
          )}
          {!running && <div className="text-muted mt-0.5">The guest is stopped, so they take effect when it is next started.</div>}
        </div>
      )}

      {op && (
        <div>
          {rows.length > 0 && (
            <table className="w-full text-xs mb-2">
              <thead><tr className="text-left text-muted"><th className="py-1 pr-3">Setting</th><th className="pr-3">Now</th><th className="pr-3">Becomes</th><th>When</th></tr></thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.key} className="border-t border-border">
                    <td className="py-1 pr-3 text-text">{c.label}</td>
                    <td className="pr-3 text-muted">{c.old ?? "not set"}</td>
                    <td className="pr-3">{c.remove ? "removed" : c.new}</td>
                    <td>{c.applies === "live" ? "immediately" : c.applies === "restart" ? "after the guest restarts" : "now (guest is stopped; used at next start)"}{c.priv_ok === false ? " · missing privilege" : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <OpPreview
            op={op}
            confirmLabel={op.operation_type_id === "workload.reboot" ? "Confirm & restart" : "Confirm & apply"}
            progressText={op.operation_type_id === "workload.reboot" ? "Restarting the guest" : "Changing the configuration"}
            doneText={op.operation_type_id === "workload.reboot" ? "Restarted." : "Saved and read back from Proxmox."}
            onFinished={done}
            onClose={done}
          />
        </div>
      )}
      {error && <div className="text-sm text-bad">{error}</div>}

      {!op && data.groups.map((g) => (
        <section key={g}>
          <div className="text-xs font-semibold uppercase tracking-wide text-muted mb-1">{g}</div>
          <div className="divide-y divide-border border border-border rounded">
            {data.fields.filter((f) => f.group === g).map((f) => {
              const changed = f.key in dirty;
              return (
                <div key={f.key} className={`flex flex-wrap items-start gap-3 px-3 py-2 text-sm ${changed ? "bg-accent/5" : ""}`}>
                  <div className="w-64 shrink-0">
                    <div className="text-text">{f.label}</div>
                    <div className="text-[11px] text-muted"><code>{f.key}</code></div>
                  </div>
                  <div className="flex-1 min-w-[16rem]">
                    {editor(f)}
                    {f.pending !== null && <div className="text-[11px] text-warn mt-0.5">pending: {show(f, f.pending)} (applies at next restart)</div>}
                    {f.hint && <div className="text-[11px] text-muted mt-0.5">{f.hint}</div>}
                  </div>
                  <div className="w-36 shrink-0 text-right">{chip(f)}</div>
                </div>
              );
            })}
          </div>
        </section>
      ))}

      {!op && data.own_panel.length > 0 && (
        <section>
          <div className="text-xs font-semibold uppercase tracking-wide text-muted mb-1">Edited in their own panel</div>
          <div className="text-xs text-muted">
            {data.own_panel.map((o) => `${o.key}${o.value !== undefined && o.value !== null ? ` = ${String(o.value)}` : ""} (${o.where})`).join(" · ")}
            . Disks, network adapters, CD/ISO and tags are on the Hardware tab. Hook scripts, extra QEMU arguments and PCI/USB passthrough can only be set by root on the node.
          </div>
        </section>
      )}

      {!op && editable && (
        <div className="sticky bottom-0 flex items-center gap-3 bg-surface/90 border-t border-border py-2">
          <button
            type="button"
            disabled={busy || dirtyCount === 0}
            onClick={() => preview({ changes: dirty })}
            className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10 disabled:opacity-50"
            title="Shows exactly what would change and when it takes effect. Nothing is written until you approve."
          >
            Preview {dirtyCount > 0 ? `${dirtyCount} change${dirtyCount === 1 ? "" : "s"}` : "changes"}
          </button>
          {dirtyCount > 0 && <button type="button" className="text-xs text-muted hover:text-text" onClick={() => setEdits({})}>Reset</button>}
          <span className="text-[11px] text-muted">{busy ? "Checking with Proxmox…" : "Proxmox digest-checks the write, so a change made behind PyXie's back is never overwritten."}</span>
        </div>
      )}
    </div>
  );
}
