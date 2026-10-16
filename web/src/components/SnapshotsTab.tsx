"use client";

import { useCallback, useEffect, useState } from "react";
import type { Operation } from "@/lib/api";
import { Card, CardTitle } from "@/components/Card";
import OpPreview from "@/components/OpPreview";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { formatRelativeTime } from "@/lib/format";
import { useMe } from "@/lib/useMe";

type Snap = { name: string; description: string | null; snaptime: number | null; vmstate: boolean; parent: string | null };
type SnapData = { snapshots: Snap[]; can_snapshot: boolean | null; suggested_name: string; supported: boolean; error: string | null };
const WARN_DAYS = 7;
const CRIT_DAYS = 30;

function ageDays(t: number | null): number | null {
  return t ? Math.floor((Date.now() / 1000 - t) / 86400) : null;
}

/** Order snapshots as Proxmox chains them: oldest first, each indented under its parent. */
function ordered(snaps: Snap[]): { snap: Snap; depth: number }[] {
  const out: { snap: Snap; depth: number }[] = [];
  const kids = (p: string | null) => snaps.filter((s) => (s.parent ?? null) === p).sort((a, b) => (a.snaptime ?? 0) - (b.snaptime ?? 0));
  const walk = (p: string | null, depth: number) => kids(p).forEach((s) => { out.push({ snap: s, depth }); walk(s.name, depth + 1); });
  walk(null, 0);
  const seen = new Set(out.map((o) => o.snap.name));
  snaps.filter((s) => !seen.has(s.name)).forEach((s) => out.push({ snap: s, depth: 0 }));
  return out;
}

/** Snapshots tab: list straight from Proxmox, take / roll back / delete through the usual preview-and-approve flow. */
export default function SnapshotsTab({ workloadId, workloadName, running, disabled, container }: { workloadId: string; workloadName: string; running: boolean; disabled: boolean; container?: boolean }) {
  const me = useMe();
  const admin = me === undefined || me?.is_admin === true;
  const [data, setData] = useState<SnapData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [ram, setRam] = useState(false);
  const [op, setOp] = useState<{ op: Operation; kind: "create" | "delete" | "rollback"; snap: string } | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`/api/workloads/${workloadId}/snapshots`);
      const d = await r.json();
      if (!r.ok) { setLoadError(d.error || "Could not read snapshots"); return; }
      setData(d); setLoadError(null);
      setName((n) => n || d.suggested_name);
    } catch (e) { setLoadError((e as Error).message); }
  }, [workloadId]);
  useEffect(() => { load(); }, [load]);

  async function preview(kind: "create" | "delete" | "rollback", snap: string) {
    setPending(true); setError(null);
    try {
      const r = await fetch("/api/operations/workload-snapshots/dry-run", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workload_id: workloadId, action: kind, name: snap, description: kind === "create" ? description : "", include_ram: kind === "create" && ram }) });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Preview failed"); return; }
      setOp({ op: d, kind, snap }); notifyOperationsChanged();
    } finally { setPending(false); }
  }

  function close() { setOp(null); setName(""); setDescription(""); setRam(false); load(); }

  if (disabled) return <Card><CardTitle>Snapshots</CardTitle><div className="text-sm text-muted">Snapshots are not available for a guest Proxmox does not currently see.</div></Card>;
  const rows = data ? ordered(data.snapshots) : [];
  const busy = !!op;
  return (
    <div className="space-y-4">
      <Card>
        <CardTitle>Snapshots{data ? ` · ${data.snapshots.length}` : ""}</CardTitle>
        {loadError && <div className="text-bad text-sm mb-2">{loadError}</div>}
        {data?.error && <div className="text-warn text-sm mb-2">Could not read the list from Proxmox: {data.error}</div>}
        {data && rows.length === 0 && !data.error && <div className="text-sm text-muted">No snapshots. {workloadName} is running on its live disks only.</div>}
        {rows.length > 0 && (
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-muted"><th className="py-1 pr-3">Name</th><th className="pr-3">Taken</th><th className="pr-3">Memory</th><th className="pr-3">Description</th><th /></tr></thead>
            <tbody>
              {rows.map(({ snap, depth }) => {
                const age = ageDays(snap.snaptime);
                const tone = age == null ? "" : age >= CRIT_DAYS ? "text-bad" : age >= WARN_DAYS ? "text-warn" : "";
                return (
                  <tr key={snap.name} className="border-t border-border align-top">
                    <td className="py-1.5 pr-3 text-text font-medium" style={{ paddingLeft: depth * 16 }}>{depth > 0 ? "└ " : ""}{snap.name}</td>
                    <td className={`pr-3 whitespace-nowrap ${tone}`}>{snap.snaptime ? formatRelativeTime(new Date(snap.snaptime * 1000).toISOString()) : "—"}{age != null && age >= WARN_DAYS ? ` (${age} d)` : ""}</td>
                    <td className="pr-3">{snap.vmstate ? "included" : "disk only"}</td>
                    <td className="pr-3 text-muted">{snap.description || "—"}</td>
                    <td className="text-right whitespace-nowrap">
                      {admin && <>
                        <button disabled={busy || pending} onClick={() => preview("rollback", snap.name)} className="text-xs text-accent hover:underline mr-3 disabled:opacity-40">Roll back</button>
                        <button disabled={busy || pending} onClick={() => preview("delete", snap.name)} className="text-xs text-bad hover:underline disabled:opacity-40">Delete</button>
                      </>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {rows.length > 0 && <div className="text-xs text-muted mt-2">Snapshots older than {WARN_DAYS} days are flagged: a long chain slows the disk and holds space. Remove them once they have served their purpose.</div>}
      </Card>

      {admin && (
        <Card>
          <CardTitle>Take a snapshot</CardTitle>
          {data?.can_snapshot === false && <div className="text-warn text-sm mb-2">Proxmox says this VM cannot be snapshotted: one of its disks is on storage without snapshot support. The preview will explain.</div>}
          {op ? (
            <OpPreview
              op={op.op}
              danger={op.kind !== "create"}
              confirmLabel={op.kind === "create" ? "Confirm & take snapshot" : op.kind === "delete" ? "Confirm & delete snapshot" : "Confirm & roll back"}
              progressText={op.kind === "create" ? `Taking snapshot ${op.snap}` : op.kind === "delete" ? `Deleting snapshot ${op.snap}` : `Rolling ${workloadName} back to ${op.snap}`}
              doneText={op.kind === "create" ? `Snapshot ${op.snap} taken and found in Proxmox.` : op.kind === "delete" ? `Snapshot ${op.snap} is gone.` : `${workloadName} is back at ${op.snap}. Check its power state.`}
              onFinished={load}
              onClose={close}
            />
          ) : (
            <div className="space-y-2 max-w-md text-sm">
              <div>
                <label className="block text-xs text-muted mb-1">Name</label>
                <input value={name} onChange={(e) => setName(e.target.value)} className="bg-surface border border-border rounded px-2 py-1.5 w-full" placeholder="before-update" />
              </div>
              <div>
                <label className="block text-xs text-muted mb-1">Description (optional)</label>
                <input value={description} onChange={(e) => setDescription(e.target.value)} className="bg-surface border border-border rounded px-2 py-1.5 w-full" placeholder="why this snapshot exists" />
              </div>
              <label className={`flex items-center gap-2 text-xs ${running ? "text-text" : "text-muted"}`}>
                <input type="checkbox" checked={ram && running} disabled={!running} onChange={(e) => setRam(e.target.checked)} />
                Include memory (the VM resumes exactly where it was on rollback; pauses it briefly){container ? " — containers have no memory snapshot" : !running ? " — VM is stopped" : ""}
              </label>
              {error && <div className="text-bad text-xs">{error}</div>}
              <button disabled={!name || pending} onClick={() => preview("create", name)} className="px-2 py-1 rounded bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50 text-xs">{pending ? "Checking…" : "Preview"}</button>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
