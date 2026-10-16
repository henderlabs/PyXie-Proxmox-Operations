"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import type { Operation } from "@/lib/api";
import OpPreview from "@/components/OpPreview";
import TagChips, { TagChip } from "@/components/TagChip";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { colorFor, useTagColors } from "@/lib/tagColors";
import { useMe } from "@/lib/useMe";

/** The Tags row on a workload: colored chips, with an Edit mode to add and remove tags and to set a tag's color. Tags save to Proxmox
 * (so PVE shows them too); a color is Proxmox's datacenter-wide tag color, written back to it, so both always agree. */
export default function TagsEditor({ workloadId, clusterId, tags }: { workloadId: string; clusterId: string; tags: string[] }) {
  const router = useRouter();
  const me = useMe();
  const colors = useTagColors();
  const admin = me === undefined || me?.is_admin === true;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<string[]>(tags);
  const [input, setInput] = useState("");
  const [known, setKnown] = useState<string[]>([]);
  const [op, setOp] = useState<Operation | null>(null);
  const [colorOp, setColorOp] = useState<{ op: Operation; tag: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => { if (!editing) setDraft(tags); }, [tags, editing]);
  useEffect(() => {
    if (!editing) return;
    fetch("/api/workloads/tags").then((r) => (r.ok ? r.json() : [])).then((d: { tag: string }[]) => setKnown(d.map((x) => x.tag))).catch(() => {});
  }, [editing]);

  function add(raw: string) {
    const t = raw.trim();
    if (t && !draft.some((d) => d.toLowerCase() === t.toLowerCase())) setDraft([...draft, t]);
    setInput("");
  }

  async function save() {
    setPending(true); setError(null);
    try {
      const pendingTag = input.trim();
      const wanted = pendingTag && !draft.some((d) => d.toLowerCase() === pendingTag.toLowerCase()) ? [...draft, pendingTag] : draft;
      const r = await fetch("/api/operations/workload-tags/dry-run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workload_id: workloadId, tags: wanted }) });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Could not check the tags"); return; }
      setOp(d); notifyOperationsChanged();
    } finally { setPending(false); }
  }

  /** Preview a color change for one tag (null = back to Proxmox's automatic color). It is a datacenter setting, so it asks for a click. */
  async function previewColor(tag: string, hex: string | null) {
    setPending(true); setError(null);
    try {
      const r = await fetch("/api/operations/cluster-tag-colors/dry-run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cluster_id: clusterId, changes: { [tag]: hex } }) });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Could not check the color"); return; }
      setColorOp({ op: d, tag }); notifyOperationsChanged();
    } finally { setPending(false); }
  }

  function done() { setOp(null); setColorOp(null); setEditing(false); setInput(""); router.refresh(); }

  if (!editing) {
    return (
      <span className="inline-flex flex-wrap items-center gap-1 justify-end">
        <TagChips tags={tags} clusterId={clusterId} />
        {admin && <button type="button" onClick={() => { setDraft(tags); setEditing(true); }} className="text-xs text-accent hover:underline ml-1">Edit</button>}
      </span>
    );
  }
  return (
    <div className="w-full text-left mt-1">
      <div className="flex flex-wrap gap-1.5 mb-2">
        {draft.length === 0 && <span className="text-muted text-xs">no tags</span>}
        {draft.map((t) => {
          const c = colorFor(colors, clusterId, t);
          return (
            <TagChip key={t} tag={t} clusterId={clusterId}>
              <input
                type="color" aria-label={`Color of ${t}`} title="Set this tag's color in Proxmox (datacenter-wide)" value={`#${c.bg.toLowerCase()}`} disabled={pending || !!colorOp}
                ref={(el) => { if (el && !(el as HTMLInputElement & { __bound?: boolean }).__bound) { (el as HTMLInputElement & { __bound?: boolean }).__bound = true; el.addEventListener("change", () => previewColor(t, el.value.replace("#", "").toUpperCase())); } }}
                onChange={() => { /* the colour is taken when the picker closes (native change event), not on every movement */ }}
                className="w-4 h-4 p-0 border-0 bg-transparent cursor-pointer"
              />
              {!c.auto && <button type="button" aria-label={`Reset color of ${t}`} title="Back to the automatic color" disabled={pending || !!colorOp} onClick={() => previewColor(t, null)} className="opacity-70 hover:opacity-100">↺</button>}
              <button type="button" aria-label={`Remove ${t}`} onClick={() => setDraft(draft.filter((x) => x !== t))} className="opacity-70 hover:opacity-100">×</button>
            </TagChip>
          );
        })}
      </div>
      {colorOp ? (
        <OpPreview op={colorOp.op} confirmLabel="Confirm & set color" progressText={`Setting the color of ${colorOp.tag} in Proxmox`} doneText="Color saved in Proxmox and read back." onClose={() => { setColorOp(null); notifyOperationsChanged(); }} />
      ) : op ? (
        <OpPreview op={op} autoApprove confirmLabel="Confirm & save tags" progressText="Saving tags" doneText="Tags saved and read back from Proxmox." onClose={done} onFinished={() => router.refresh()} />
      ) : (
        <>
          <div className="flex gap-2 items-center">
            <input
              list={`tag-suggestions-${workloadId}`} value={input} onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === ";" || e.key === ",") { e.preventDefault(); add(input); } }}
              placeholder="add a tag, press Enter" className="bg-surface border border-border rounded px-2 py-1 text-sm flex-1 min-w-0"
            />
            <datalist id={`tag-suggestions-${workloadId}`}>{known.filter((k) => !draft.includes(k)).map((k) => <option key={k} value={k} />)}</datalist>
            <button type="button" disabled={pending} onClick={save} className="px-2 py-1 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50 text-xs">{pending ? "Checking…" : "Save"}</button>
            <button type="button" onClick={() => setEditing(false)} className="text-xs text-muted hover:text-text">cancel</button>
          </div>
          <div className="text-xs text-muted mt-1">The color square sets the tag&apos;s color in Proxmox for every guest with that tag.</div>
          {error && <div className="text-bad text-xs mt-1">{error}</div>}
        </>
      )}
    </div>
  );
}
