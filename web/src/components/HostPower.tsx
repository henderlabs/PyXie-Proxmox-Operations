"use client";

import { useState } from "react";
import type { Operation } from "@/lib/api";
import OpPreview from "@/components/OpPreview";
import { useMe } from "@/lib/useMe";

/** Restart and Shut Down for a host. Both are offered only while the host is in maintenance mode (the reason shows when they are off); the preview
 * re-checks that nothing is running and the cluster stays quorate, and the host name is typed to confirm. There is no Start: PyXie cannot power a host on. */
export default function HostPower({ nodeId, nodeName, inMaintenance }: { nodeId: string; nodeName: string; inMaintenance: boolean }) {
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  const [op, setOp] = useState<Operation | null>(null);
  const [kind, setKind] = useState<"restart" | "shutdown">("restart");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!isAdmin) return null;

  async function preview(k: "restart" | "shutdown") {
    setBusy(true); setError(null);
    try {
      const r = await fetch(k === "restart" ? "/api/operations/host-reboots/dry-run" : "/api/operations/host-shutdowns/dry-run", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(k === "restart" ? { node_id: nodeId, require_maintenance: true } : { node_id: nodeId }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `Preview failed (${r.status})`);
      setKind(k); setOp(d as Operation);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const why = inMaintenance ? undefined : "Put the host in maintenance mode first (Evacuate / Maint Mode)";
  const btn = "px-2 py-1 rounded border text-xs disabled:opacity-40";
  return (
    <div className="text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" disabled={!inMaintenance || busy || !!op} title={why} onClick={() => preview("restart")} className={`${btn} border-warn hover:bg-warn/10`}>Restart…</button>
        <button type="button" disabled={!inMaintenance || busy || !!op} title={why} onClick={() => preview("shutdown")} className={`${btn} border-bad text-bad hover:bg-bad/10`}>Shut Down…</button>
        {!inMaintenance && <span className="text-muted">Available while the host is in maintenance mode.</span>}
        {error && <span className="text-bad">{error}</span>}
      </div>
      {op && (
        <div className="mt-2 max-w-xl">
          <OpPreview op={op} confirmName={nodeName}
            confirmLabel={kind === "restart" ? `Restart ${nodeName}` : `Shut down ${nodeName}`} danger
            progressText={kind === "restart" ? `Restarting ${nodeName} and waiting for it to rejoin` : `Shutting down ${nodeName}`}
            doneText={kind === "restart" ? `${nodeName} restarted and rejoined the cluster.` : `${nodeName} is off. It stays off until someone powers it on at the machine.`}
            onClose={() => setOp(null)} />
        </div>
      )}
    </div>
  );
}
