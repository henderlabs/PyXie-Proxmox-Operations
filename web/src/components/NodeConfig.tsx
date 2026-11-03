"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { Operation } from "@/lib/api";
import OpPreview from "@/components/OpPreview";
import NotesCell from "@/components/NotesCell";
import DefaultStorageSelect from "@/components/DefaultStorageSelect";
import { useMe } from "@/lib/useMe";

type Field = { key: string; label: string; group: string; type: "text" | "mac" | "int" | "ip" | "domain" | "tz" | "hosts"; value: string | null; hint?: string | null; min?: number | null; max?: number | null; required?: boolean };
type Data = { node_id: string; node: string; priv: string; priv_ok: boolean | null; groups: string[]; fields: Field[] };
type ChangeRow = { key: string; label: string; old: string | null; new: string | null; remove: boolean };

async function putPolicy(body: Record<string, unknown>): Promise<void> {
  const res = await fetch("/api/policies", { method: "PUT", body: JSON.stringify(body) });
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    throw new Error(d.error || `Save failed (${res.status})`);
  }
}

/** Settings stored in PyXie only (never written to the host): preferred storage, avoid-as-destination, tiers, notes. Each saves at once. */
function PyxieSettings({ nodeId, avoid: avoid0, tiers, notes, storageOptions, defaultStorage, autoResolvesTo }: {
  nodeId: string; avoid: boolean; tiers: { performance: string; trust: string }; notes: string;
  storageOptions: { id: string; name: string; scope: string }[]; defaultStorage: string | null; autoResolvesTo?: string;
}) {
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  const [avoid, setAvoid] = useState(avoid0);
  const [t, setT] = useState(tiers);
  const [storage, setStorage] = useState<string | null>(defaultStorage);
  const [msg, setMsg] = useState<string | null>(null);
  const sel = "bg-surface2 border border-border rounded px-2 py-1 text-sm disabled:opacity-60";

  async function setTier(key: "performance" | "trust", v: string) {
    setMsg(null);
    try {
      await putPolicy({ scope_type: "node", scope_id: nodeId, key: `placement.${key}_tier`, value: v });
      setT((x) => ({ ...x, [key]: v }));
    } catch (e) {
      setMsg((e as Error).message);
    }
  }
  async function setAvoidFlag(v: boolean) {
    setMsg(null);
    setAvoid(v);
    const res = await fetch(`/api/nodes/${nodeId}/avoid-destination`, { method: "PUT", body: JSON.stringify({ avoid: v }) });
    if (!res.ok) { setAvoid(!v); setMsg("Could not save that"); }
  }

  const row = (label: string, hint: string, control: React.ReactNode) => (
    <div className="flex flex-wrap items-start gap-3 px-3 py-2 text-sm">
      <div className="w-64 shrink-0"><div className="text-text">{label}</div><div className="text-[11px] text-muted">{hint}</div></div>
      <div className="flex-1 min-w-[16rem]">{control}</div>
      <div className="w-36 shrink-0 text-right"><span className="text-[10px] px-1.5 py-0.5 rounded border border-good/50 text-good" title="Saved at once, in PyXie only">PyXie only, live</span></div>
    </div>
  );
  return (
    <section>
      <div className="text-xs font-semibold uppercase tracking-wide text-muted mb-1">PyXie settings for this host</div>
      <div className="divide-y divide-border border border-border rounded">
        {row("Preferred storage", "where migrations to this host put disks; same setting as the Hosts table and Maintenance page",
          <DefaultStorageSelect nodeId={nodeId} value={storage} autoResolvesTo={autoResolvesTo} options={storageOptions} onChanged={setStorage} />)}
        {row("Avoid as an automatic destination", "never picked automatically for migrations (about to be upgraded or drained); you can still choose it by hand",
          <label className="text-sm"><input type="checkbox" disabled={!isAdmin} checked={avoid} onChange={(e) => setAvoidFlag(e.target.checked)} /> avoid this host</label>)}
        {row("Performance tier", "how placement ranks this host",
          <select className={sel} disabled={!isAdmin} value={t.performance} onChange={(e) => setTier("performance", e.target.value)}><option>low</option><option>standard</option><option>high</option></select>)}
        {row("Trust tier", "restricted or downtime-intolerant guests are kept off a low-trust host",
          <select className={sel} disabled={!isAdmin} value={t.trust} onChange={(e) => setTier("trust", e.target.value)}><option>low</option><option>standard</option><option>high</option></select>)}
        {row("Notes", "free text for your team, stored only in PyXie",
          <NotesCell value={notes} onSave={async (v) => { await putPolicy({ scope_type: "node", scope_id: nodeId, key: "placement.notes", value: v }); }} />)}
      </div>
      {msg && <div className="text-xs text-bad mt-1">{msg}</div>}
    </section>
  );
}

type NetField = { key: string; label: string; kind: string; value: string | null; hint?: string | null; options?: string[] | null };
type NetIface = { iface: string; type: string | null; editable: boolean; active: boolean; comments: string | null; ports: string | null; cidr: string | null; gateway: string | null; mtu: string | number | null; carries: string[]; fields: NetField[] };
type NetData = { node: string; node_id: string; interfaces: NetIface[]; staged: string | null; priv: string; priv_ok: boolean | null };

/** Bridges, bonds and VLAN interfaces. Proxmox's own two-step model: an edit is STAGED (its pending file only), and a separate Reload applies it.
 * Physical NICs are read-only. A link PyXie or the cluster runs over is marked, and a reload that touches it needs the host name typed. */
function NetworkInterfaces({ nodeId, nodeName }: { nodeId: string; nodeName: string }) {
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  const [data, setData] = useState<NetData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [op, setOp] = useState<Operation | null>(null);
  const [kind, setKind] = useState<"stage" | "apply" | "discard">("stage");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const r = await fetch(`/api/operations/host-network/node/${nodeId}`);
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `Could not read the network interfaces (${r.status})`);
      setData(d as NetData);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [nodeId]);
  useEffect(() => { load(); }, [load]);

  async function preview(k: "stage" | "apply" | "discard", body: Record<string, unknown>) {
    setBusy(true); setError(null);
    try {
      const r = await fetch(`/api/operations/host-network/${k}/dry-run`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ node_id: nodeId, ...body }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `Preview failed (${r.status})`);
      setKind(k); setOp(d as Operation);
    } catch (e) {
      setError((e as Error).message);
    } finally { setBusy(false); }
  }
  function done() { setOp(null); setEditing(null); setEdits({}); load(); }

  const cur = data?.interfaces.find((i) => i.iface === editing) ?? null;
  const changed = cur ? cur.fields.filter((f) => f.key in edits && (edits[f.key] || "") !== (f.value ?? "")) : [];
  const critical = (c: string[]) => c.some((x) => x.startsWith("host management") || x.startsWith("cluster"));
  const input = "bg-surface2 border border-border rounded px-2 py-1 text-sm";
  const res = op?.dry_run_result as { changes?: { key: string; label: string; old: string | null; new: string | null; remove: boolean }[]; staged?: string; confirm_name?: string | null } | null;

  return (
    <section>
      <div className="flex items-center justify-between mb-1">
        <div className="text-xs font-semibold uppercase tracking-wide text-muted">Network interfaces</div>
        <span className="text-[10px] px-1.5 py-0.5 rounded border border-warn/50 text-warn" title="An edit is written to Proxmox's pending file only; the running network changes when you press Reload">Staged, then Reload</span>
      </div>
      {error && <div className="text-xs text-bad mb-1">{error}</div>}
      {!data && !error && <div className="text-xs text-muted">Reading the interfaces from Proxmox…</div>}
      {data && data.staged && !op && (
        <div className="border border-warn/60 bg-warn/5 rounded p-3 mb-2 text-xs">
          <div className="font-semibold text-text mb-1">Staged network changes are waiting</div>
          <pre className="whitespace-pre-wrap text-[11px] text-muted max-h-40 overflow-auto mb-2">{data.staged}</pre>
          <div className="flex gap-2 items-center">
            {isAdmin && <button disabled={busy} onClick={() => preview("apply", {})} className="px-2 py-1 rounded bg-ink text-on-ink border border-warn hover:bg-warn/10 disabled:opacity-50">Review and reload…</button>}
            {isAdmin && <button disabled={busy} onClick={() => preview("discard", {})} className="text-muted hover:text-text">Discard staged changes…</button>}
            <span className="text-muted">Staged means only Proxmox's pending file is written; the running network is untouched until you reload.</span>
          </div>
        </div>
      )}
      {op && (
        <div className="mb-2">
          {kind === "stage" && res?.changes && (
            <table className="text-xs mb-2 w-full"><tbody>
              {res.changes.map((c) => (
                <tr key={c.key} className="border-t border-border"><td className="py-1 pr-3 text-muted">{c.label}</td><td className="pr-2">{c.old ?? "(none)"}</td><td className="pr-2 text-muted">→</td><td className="font-medium">{c.remove ? "(removed)" : c.new}</td></tr>
              ))}
            </tbody></table>
          )}
          {kind === "apply" && res?.staged && <pre className="whitespace-pre-wrap text-[11px] text-muted max-h-48 overflow-auto mb-2 border border-border rounded p-2">{res.staged}</pre>}
          <OpPreview op={op} confirmName={kind === "apply" ? res?.confirm_name : null}
            confirmLabel={kind === "stage" ? "Stage this change" : kind === "apply" ? "Reload the network" : "Discard staged changes"}
            progressText={kind === "stage" ? "Staging the change" : kind === "apply" ? "Reloading the network and checking the host is still reachable" : "Discarding the staged changes"}
            doneText={kind === "stage" ? "Staged and read back from Proxmox. Nothing is applied yet: use Review and reload when you are ready." : kind === "apply" ? "Reloaded. PyXie re-read the host and it is reachable, with nothing left staged." : "Discarded. The running network was not touched."}
            danger={kind === "apply"} onFinished={load} onClose={done} />
        </div>
      )}
      {data && !op && (
        <div className="border border-border rounded overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-muted uppercase"><th className="py-1 px-3">Interface</th><th className="pr-3">Type</th><th className="pr-3">Address</th><th className="pr-3">Gateway</th><th className="pr-3">Ports / slaves</th><th className="pr-3">MTU</th><th className="pr-3">Carries</th><th /></tr></thead>
            <tbody>
              {data.interfaces.map((i) => (
                <tr key={i.iface} className="border-t border-border align-top">
                  <td className="py-1.5 px-3">{i.iface}{!i.active && <span className="text-warn text-xs ml-1">down</span>}</td>
                  <td className="pr-3">{i.type ?? "-"}</td>
                  <td className="pr-3">{i.cidr || "-"}</td>
                  <td className="pr-3">{i.gateway || "-"}</td>
                  <td className="pr-3">{i.ports || "-"}</td>
                  <td className="pr-3">{i.mtu || "default"}</td>
                  <td className="pr-3">{i.carries.length === 0 ? <span className="text-muted">-</span> : i.carries.map((c) => (
                    <span key={c} className={`inline-block text-[10px] px-1.5 py-0.5 rounded border mr-1 mb-0.5 ${critical([c]) ? "border-bad/60 text-bad" : "border-border text-muted"}`}>{c}</span>))}</td>
                  <td className="pr-3 text-right whitespace-nowrap">
                    {i.editable
                      ? (isAdmin && data.priv_ok !== false && <button type="button" onClick={() => { setEditing(i.iface); setEdits({}); }} className="text-xs text-accent hover:underline">Edit</button>)
                      : <span className="text-[10px] px-1.5 py-0.5 rounded border border-border text-muted" title="Hardware, Open vSwitch and anything else PyXie does not edit">Read-only</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && data.priv_ok === false && <div className="text-[11px] text-warn mt-1">The Proxmox account PyXie uses lacks {data.priv} on {nodeName}, so edits are off.</div>}
      {cur && !op && (
        <div className="border border-border rounded p-3 mt-2 text-sm">
          <div className="font-semibold mb-1">{cur.iface} <span className="text-muted font-normal text-xs">({cur.type})</span></div>
          {critical(cur.carries) && <div className="text-xs text-bad mb-2">This link carries {cur.carries.join(", ")}. Changing its address or gateway is refused; other edits can still drop PyXie or the cluster if they are wrong, and Proxmox cannot undo a reload.</div>}
          <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(15rem, 1fr))" }}>
            {cur.fields.map((f) => {
              const v = f.key in edits ? edits[f.key] : f.value ?? "";
              return (
                <label key={f.key} className="block text-xs text-muted">{f.label}
                  {f.kind === "bool" ? (
                    <div className="mt-1"><input type="checkbox" checked={v === "1"} onChange={(e) => setEdits((x) => ({ ...x, [f.key]: e.target.checked ? "1" : "0" }))} /></div>
                  ) : f.kind === "enum" ? (
                    <select className={`${input} block w-full mt-1`} value={v} onChange={(e) => setEdits((x) => ({ ...x, [f.key]: e.target.value }))}><option value="">(default)</option>{(f.options ?? []).map((o) => <option key={o}>{o}</option>)}</select>
                  ) : (
                    <input className={`${input} block w-full mt-1`} value={v} placeholder={f.hint ?? ""} onChange={(e) => setEdits((x) => ({ ...x, [f.key]: e.target.value }))} />
                  )}
                </label>
              );
            })}
          </div>
          <div className="flex gap-2 items-center mt-3">
            <button type="button" disabled={busy || changed.length === 0} onClick={() => preview("stage", { iface: cur.iface, changes: Object.fromEntries(changed.map((f) => [f.key, edits[f.key]])) })}
              className="px-2 py-1 rounded bg-ink text-on-ink border border-warn hover:bg-warn/10 disabled:opacity-50 text-xs">Preview {changed.length || ""} change{changed.length === 1 ? "" : "s"}</button>
            <button type="button" className="text-xs text-muted hover:text-text" onClick={() => { setEditing(null); setEdits({}); }}>Cancel</button>
            <span className="text-[11px] text-muted">{busy ? "Checking with Proxmox…" : "Previewing changes nothing. Approving only stages the edit; you reload separately."}</span>
          </div>
        </div>
      )}
      <div className="text-[11px] text-muted mt-1">Creating or deleting interfaces and bonds, Open vSwitch options and SDN stay on the Network page or in Proxmox.</div>
    </section>
  );
}

/** A host's Config tab: PyXie-side settings (immediate) plus Proxmox settings (node options, DNS, time zone, hosts file) that go through
 * preview -> approve -> digest-checked write -> read-back. Network interfaces are staged, then reloaded (see NetworkInterfaces). */
export default function NodeConfig({ nodeId, nodeName, pyxie }: { nodeId: string; nodeName: string; pyxie: React.ComponentProps<typeof PyxieSettings> }) {
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [op, setOp] = useState<Operation | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const r = await fetch(`/api/operations/node-config/${nodeId}`);
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `Could not read the host settings (${r.status})`);
      setData(d as Data);
      setEdits({});
    } catch (e) {
      setError((e as Error).message);
    }
  }, [nodeId]);
  useEffect(() => { load(); }, [load]);

  const dirty = useMemo(() => {
    const out: Record<string, string> = {};
    if (!data) return out;
    for (const f of data.fields) if (f.key in edits && edits[f.key] !== (f.value ?? "")) out[f.key] = edits[f.key];
    return out;
  }, [data, edits]);
  const dirtyCount = Object.keys(dirty).length;

  async function preview() {
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/operations/node-config/dry-run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ node_id: nodeId, changes: dirty }) });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Could not check that"); return; }
      setOp(d as Operation);
    } finally {
      setBusy(false);
    }
  }
  const done = () => { setOp(null); load(); };
  const rows = (op?.dry_run_result as { changes?: ChangeRow[] } | null)?.changes ?? [];
  const editable = isAdmin && data?.priv_ok !== false;
  const cls = "bg-surface2 border border-border rounded px-2 py-1 text-sm disabled:opacity-60";

  return (
    <div className="space-y-4">
      <PyxieSettings {...pyxie} />

      <section>
        <div className="text-xs font-semibold uppercase tracking-wide text-muted mb-1">Proxmox settings on {nodeName}</div>
        {!data && !error && <div className="text-sm text-muted">Reading the settings from Proxmox…</div>}
        {error && <div className="text-sm text-bad mb-2">{error}</div>}
        {data?.priv_ok === false && <div className="border border-bad/50 bg-bad/10 rounded p-2 text-xs mb-2">The Proxmox account PyXie uses lacks {data.priv} on this host, so these settings are read-only here.</div>}
        {op && (
          <div className="mb-3">
            {rows.length > 0 && (
              <table className="w-full text-xs mb-2">
                <thead><tr className="text-left text-muted"><th className="py-1 pr-3">Setting</th><th className="pr-3">Now</th><th>Becomes</th></tr></thead>
                <tbody>
                  {rows.map((c) => (
                    <tr key={c.key} className="border-t border-border align-top">
                      <td className="py-1 pr-3 text-text">{c.label}</td>
                      <td className="pr-3 text-muted whitespace-pre-wrap">{c.key === "hosts" ? "(current file)" : c.old ?? "not set"}</td>
                      <td className="whitespace-pre-wrap">{c.remove ? "removed" : c.key === "hosts" ? "(new file, see the box below)" : c.new}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <OpPreview op={op} confirmLabel="Confirm & apply" progressText="Changing the host settings" doneText="Saved and read back from Proxmox." onFinished={done} onClose={done} />
          </div>
        )}
        {data && !op && data.groups.map((g) => (
          <div key={g} className="mb-3">
            <div className="text-[11px] text-muted mb-0.5">{g}</div>
            <div className="divide-y divide-border border border-border rounded">
              {data.fields.filter((f) => f.group === g).map((f) => {
                const cur = f.key in edits ? edits[f.key] : f.value ?? "";
                const set = (v: string) => setEdits((e) => ({ ...e, [f.key]: v }));
                return (
                  <div key={f.key} className={`flex flex-wrap items-start gap-3 px-3 py-2 text-sm ${f.key in dirty ? "bg-accent/5" : ""}`}>
                    <div className="w-64 shrink-0"><div className="text-text">{f.label}</div><div className="text-[11px] text-muted"><code>{f.key}</code></div></div>
                    <div className="flex-1 min-w-[16rem]">
                      {f.type === "hosts" ? (
                        <textarea className={`${cls} w-full font-mono text-xs`} rows={9} spellCheck={false} disabled={!editable} value={cur} onChange={(e) => set(e.target.value)} />
                      ) : f.type === "text" ? (
                        <textarea className={`${cls} w-full max-w-xl`} rows={2} disabled={!editable} value={cur} onChange={(e) => set(e.target.value)} />
                      ) : (
                        <input className={`${cls} w-72`} type={f.type === "int" ? "number" : "text"} min={f.min ?? undefined} max={f.max ?? undefined} placeholder={f.hint ?? ""} disabled={!editable} value={cur} onChange={(e) => set(e.target.value)} />
                      )}
                      {f.hint && f.type !== "hosts" && <div className="text-[11px] text-muted mt-0.5">{f.hint}</div>}
                    </div>
                    <div className="w-36 shrink-0 text-right"><span className="text-[10px] px-1.5 py-0.5 rounded border border-good/50 text-good">Live</span></div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
        {data && !op && isAdmin && (
          <div className="flex items-center gap-3 py-1">
            <button type="button" disabled={busy || dirtyCount === 0 || !editable} onClick={preview} className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10 disabled:opacity-50"
              title="Shows exactly what would change on the host. Nothing is written until you approve.">
              Preview {dirtyCount > 0 ? `${dirtyCount} change${dirtyCount === 1 ? "" : "s"}` : "changes"}
            </button>
            {dirtyCount > 0 && <button type="button" className="text-xs text-muted hover:text-text" onClick={() => setEdits({})}>Reset</button>}
            <span className="text-[11px] text-muted">{busy ? "Checking with Proxmox…" : "The write is digest-checked, so a change made on the host in the meantime is never overwritten."}</span>
          </div>
        )}
      </section>

      <NetworkInterfaces nodeId={nodeId} nodeName={nodeName} />
    </div>
  );
}
