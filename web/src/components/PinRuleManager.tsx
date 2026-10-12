"use client";

import { useRef, useState } from "react";
import type { Node, Workload } from "@/lib/api";
import { Card, CardTitle, EmptyState } from "@/components/Card";
import { useMe } from "@/lib/useMe";
import WorkloadSearchSelect from "@/components/WorkloadSearchSelect";
import { GuestPill } from "@/components/AffinityPlacement";
import { PIN_PALETTE, hostsText, pinColor, pinMembers, pinSentence, type PinRule } from "@/lib/pins";

const EMPTY = { scopeType: "workload", workloadId: "", tag: "", nodeIds: [] as string[], strict: false, description: "", color: "" };

/** The Pinning column: pin a guest, or every guest with a tag, to one or more hosts. Soft by default. */
export default function PinRuleManager({ pins, setPins, workloads, nodes }: { pins: PinRule[]; setPins: React.Dispatch<React.SetStateAction<PinRule[]>>; workloads: Workload[]; nodes: Node[] }) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const formRef = useRef<HTMLDivElement>(null);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;

  const byId = new Map(workloads.map((w) => [w.id, w]));
  const nodeName = (id: string) => nodes.find((n) => n.id === id)?.name || "unknown host";
  const picked = byId.get(form.workloadId);
  const hostChoices = [...nodes].filter((n) => form.scopeType !== "workload" || !picked || n.cluster_id === picked.cluster_id).sort((a, b) => a.name.localeCompare(b.name));
  const tagCounts = new Map<string, number>();
  for (const w of workloads) if (!(w as Workload & { is_missing?: boolean }).is_missing) for (const t of w.tags || []) tagCounts.set(t, (tagCounts.get(t) || 0) + 1);
  const knownTags = [...tagCounts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const typedTag = form.tag.trim();
  const tagMatches = typedTag ? workloads.filter((w) => !(w as Workload & { is_missing?: boolean }).is_missing && (w.tags || []).includes(typedTag)) : [];
  const toggleHost = (id: string) => setForm((f) => ({ ...f, nodeIds: f.nodeIds.includes(id) ? f.nodeIds.filter((x) => x !== id) : [...f.nodeIds, id] }));

  function startEdit(p: PinRule) {
    setEditingId(p.id);
    setForm({ scopeType: p.scope_type, workloadId: p.workload_id || "", tag: p.tag || "", nodeIds: p.node_ids, strict: p.strict, description: p.description || "", color: p.color || "" });
    setError(null);
    setTimeout(() => formRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }), 0);
  }
  function cancelEdit() { setEditingId(null); setForm(EMPTY); setError(null); }

  async function submit() {
    setPending(true);
    setError(null);
    try {
      if (form.scopeType === "workload" && !form.workloadId) { setError("Pick the guest to pin"); return; }
      if (form.scopeType === "tag_group" && !typedTag) { setError("Tag is required"); return; }
      if (form.nodeIds.length === 0) { setError("Pick at least one host"); return; }
      const body = {
        scope_type: form.scopeType, workload_id: form.scopeType === "workload" ? form.workloadId : null, tag: form.scopeType === "tag_group" ? typedTag : null,
        node_ids: form.nodeIds, strict: form.strict, description: form.description || null, color: form.color || null,
      };
      const res = await fetch(editingId ? `/api/placement/pin-rules/${editingId}` : "/api/placement/pin-rules", { method: editingId ? "PUT" : "POST", body: JSON.stringify(body) });
      const data = await res.json();
      if (!res.ok) { setError(data.error || "Could not save the pin"); return; }
      setPins((ps) => (editingId ? ps.map((p) => (p.id === editingId ? data : p)) : [data, ...ps]));
      cancelEdit();
    } finally {
      setPending(false);
    }
  }

  async function remove(id: string) {
    setConfirmDeleteId(null);
    const res = await fetch(`/api/placement/pin-rules/${id}`, { method: "DELETE" });
    // 404: it is already gone (deleted elsewhere since this page loaded), so the list should just drop it.
    if (!res.ok && res.status !== 404) {
      const data = await res.json().catch(() => ({}));
      setListError(data.error || "Could not delete the pin. Nothing was changed.");
      return;
    }
    setListError(null);
    setPins((ps) => ps.filter((p) => p.id !== id));
    if (editingId === id) cancelEdit();
  }

  const sel = "bg-surface2 border border-border rounded px-2 py-1.5 text-sm";
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        <span className="text-text">Pinning</span> ties a guest, or every guest with a tag, to one or more hosts. A <span className="text-text">soft</span> pin prefers those hosts: Balance Load leaves a guest that is on one alone and placement favors them, but maintenance can still move it. A <span className="text-text">hard</span> pin allows only those hosts, maintenance included, so a maintenance plan that cannot honor it is blocked.
      </p>
      {isAdmin && (
        <div ref={formRef}>
          <Card>
            <CardTitle>{editingId ? "Edit pin" : "New pin"}</CardTitle>
            <div className="space-y-3">
              <div className="flex flex-wrap items-end gap-3">
                <div>
                  <label className="block text-xs text-muted mb-1">Pin</label>
                  <select className={sel} value={form.scopeType} onChange={(e) => setForm((f) => ({ ...f, scopeType: e.target.value, nodeIds: [] }))}>
                    <option value="workload">one guest</option>
                    <option value="tag_group">every guest with a tag</option>
                  </select>
                </div>
                {form.scopeType === "workload" ? (
                  <div>
                    <label className="block text-xs text-muted mb-1">Guest</label>
                    <WorkloadSearchSelect vms={workloads} value={form.workloadId} onChange={(id) => setForm((f) => ({ ...f, workloadId: id, nodeIds: [] }))} />
                  </div>
                ) : (
                  <div>
                    <label className="block text-xs text-muted mb-1">Tag</label>
                    <input className={sel} value={form.tag} onChange={(e) => setForm((f) => ({ ...f, tag: e.target.value }))} placeholder="e.g. dns" list="pin-known-tags" />
                    <datalist id="pin-known-tags">{knownTags.map(([t, c]) => <option key={t} value={t} label={`${c} guest${c === 1 ? "" : "s"}`} />)}</datalist>
                  </div>
                )}
                <div>
                  <label className="block text-xs text-muted mb-1">Description</label>
                  <input className={`${sel} min-w-[200px]`} value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} placeholder="why it is pinned" />
                </div>
              </div>
              <div>
                <label className="block text-xs text-muted mb-1">Hosts (pick one or more)</label>
                <div className="flex flex-wrap gap-2">
                  {hostChoices.length === 0 && <span className="text-xs text-muted">Pick the guest first.</span>}
                  {hostChoices.map((n) => {
                    const on = form.nodeIds.includes(n.id);
                    return (
                      <button key={n.id} type="button" onClick={() => toggleHost(n.id)} className={`px-2.5 py-1 rounded border text-sm ${on ? "border-accent bg-accent/10 text-text" : "border-border text-muted hover:text-text"}`}>{n.name}</button>
                    );
                  })}
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-4">
                <label className="flex items-center gap-1.5 text-sm text-text">
                  <input type="checkbox" checked={form.strict} onChange={(e) => setForm((f) => ({ ...f, strict: e.target.checked }))} />
                  Hard pin: only these hosts, even for maintenance (leave unchecked for a soft preference)
                </label>
              </div>
              <div>
                <label className="block text-xs text-muted mb-1">Color (dashed outline and pin mark)</label>
                <div className="flex items-center gap-1.5">
                  {PIN_PALETTE.map((c) => (
                    <button key={c} type="button" title={c} aria-label={`Use ${c}`} onClick={() => setForm((f) => ({ ...f, color: c }))}
                      className={`w-5 h-5 rounded-sm border-2 border-dashed ${form.color === c ? "ring-2 ring-accent" : ""}`} style={{ background: `${c}66`, borderColor: c }} />
                  ))}
                  <input type="color" aria-label="Pick any color" title="Pick any color" value={form.color || "#0369a1"} onChange={(e) => setForm((f) => ({ ...f, color: e.target.value }))} className="w-7 h-6 p-0 bg-transparent border border-border rounded cursor-pointer" />
                  {form.color && <button type="button" onClick={() => setForm((f) => ({ ...f, color: "" }))} className="text-xs text-muted hover:underline">Default</button>}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button onClick={submit} disabled={pending} className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10 disabled:opacity-50">
                  {pending ? "Saving…" : editingId ? "Save Changes" : "Create Pin"}
                </button>
                {editingId && <button onClick={cancelEdit} className="px-3 py-1.5 rounded text-sm font-medium border border-border text-text hover:bg-surface2">Cancel</button>}
              </div>
              {form.scopeType === "tag_group" && typedTag !== "" && (
                <p className="text-xs text-muted">
                  {tagMatches.length === 0 ? <span className="text-warn">No guest carries the tag &ldquo;{typedTag}&rdquo; (tags are case-sensitive), so this pin would do nothing yet.</span>
                    : `Covers ${tagMatches.length} guest${tagMatches.length === 1 ? "" : "s"} right now: ${tagMatches.map((w) => `${w.name || "vmid " + w.vmid} on ${nodeName(w.node_id)}`).join(", ")}.`}
                </p>
              )}
              {error && <div className="text-sm text-bad">{error}</div>}
            </div>
          </Card>
        </div>
      )}

      <Card>
        <CardTitle>Active Pins</CardTitle>
        {listError && <div className="text-sm text-bad mb-2">{listError}</div>}
        {pins.length === 0 ? (
          <EmptyState message="No pins yet." />
        ) : (
          <div className="divide-y divide-border">
            {pins.map((p, i) => {
              const color = pinColor(p, pins, i);
              const members = pinMembers(p, workloads);
              const off = members.filter((m) => !p.node_ids.includes(m.node_id));
              const status = members.length === 0 ? { t: "Nothing to check", c: "bg-muted/15 text-muted" } : off.length === 0 ? { t: "Holding", c: "bg-good/15 text-good" } : p.strict ? { t: "Broken", c: "bg-bad/15 text-bad" } : { t: "Elsewhere", c: "bg-warn/15 text-warn" };
              return (
                <div key={p.id} className={`flex items-center justify-between gap-3 py-3 ${editingId === p.id ? "bg-accent/5" : ""}`}>
                  <span className="w-1.5 self-stretch rounded shrink-0 border-l-2 border-dashed" style={{ borderColor: color, background: `${color}44` }} />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-text">
                      {pinSentence(p, byId, nodes)} <span className={`text-[11px] px-1.5 py-0.5 rounded ${p.strict ? "bg-bad/15 text-bad" : "bg-accent/15 text-accent"}`}>{p.strict ? "hard" : "soft"}</span>
                      {p.origin === "workload_page" && <span className="text-xs text-muted"> · set on the workload page</span>}
                      {editingId === p.id && <span className="text-xs text-accent"> editing above</span>}
                    </div>
                    {p.description && <div className="text-xs text-muted">{p.description}</div>}
                    <div className="text-xs text-muted mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span>Right now:</span>
                      {members.length === 0 && <span>no guests match</span>}
                      {members.map((m) => (
                        <span key={m.id} className="inline-flex items-center gap-1">
                          <GuestPill w={m} pin={{ color }} flag={p.node_ids.includes(m.node_id) ? null : p.strict ? "broken" : "unmet"} /> on {nodeName(m.node_id)}
                        </span>
                      ))}
                    </div>
                  </div>
                  <span className={`shrink-0 px-2 py-0.5 rounded text-xs font-medium ${status.c}`} title={off.length ? `Not on ${hostsText(p, nodes)}` : undefined}>{status.t}</span>
                  {isAdmin && (
                    <div className="flex items-center gap-3">
                      <button onClick={() => startEdit(p)} className="text-xs text-accent hover:underline">Edit</button>
                      {confirmDeleteId === p.id ? (
                        <>
                          <span className="text-xs text-muted">Delete this pin?</span>
                          <button onClick={() => remove(p.id)} className="text-xs text-bad font-medium hover:underline">Yes, delete</button>
                          <button onClick={() => setConfirmDeleteId(null)} className="text-xs text-muted hover:underline">Keep</button>
                        </>
                      ) : (
                        <button onClick={() => setConfirmDeleteId(p.id)} className="text-xs text-bad hover:underline">Delete</button>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
