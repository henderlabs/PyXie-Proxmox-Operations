"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMe } from "@/lib/useMe";

/** "Remove host": forgets a host PVE no longer lists. App-side only, nothing on the host or cluster is touched and history is kept.
 * Disabled with the reason for a host PVE still lists as a cluster member. Admins only. */
export default function RemoveHostButton({
  nodeId, nodeName, stillMember, redirectTo,
}: { nodeId: string; nodeName: string; stillMember: boolean; redirectTo?: string }) {
  const me = useMe();
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (me?.is_admin !== true) return null;

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/nodes/${nodeId}/forget`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Could not remove the host");
        return;
      }
      setConfirming(false);
      if (redirectTo) router.push(redirectTo);
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="inline-block">
      <button
        type="button"
        disabled={stillMember}
        onClick={() => setConfirming(true)}
        title={stillMember ? `PVE still lists ${nodeName} as a cluster member. Remove it from the cluster first (pvecm delnode ${nodeName} on a remaining node, once it is powered off and the cluster has quorum).` : "Forget this host in PyXie"}
        className="px-2 py-1 rounded text-[11px] font-medium bg-ink text-on-ink border border-bad hover:bg-bad/10 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        Remove host…
      </button>
      {confirming && (
        <div className="mt-1.5 text-[11px] bg-surface2/60 border border-border rounded p-2 max-w-xl text-left">
          <div className="text-text font-medium">Remove {nodeName} from PyXie?</div>
          <div className="text-muted mt-0.5">PVE no longer lists this host, so PyXie will stop showing it. The host itself is not touched.</div>
          <div className="grid grid-cols-2 gap-3 mt-1.5">
            <div>
              <div className="text-text font-medium">Dropped</div>
              <ul className="list-disc ml-4 text-muted">
                <li>Entry in Hosts &amp; Clusters (tree and table) and the node totals</li>
                <li>Pinned SSH key and wrapper record</li>
                <li>Its rows in the Maintenance and Credentials lists</li>
                <li>Its local storage entries</li>
              </ul>
            </div>
            <div>
              <div className="text-text font-medium">Kept</div>
              <ul className="list-disc ml-4 text-muted">
                <li>Audit log and past operations</li>
                <li>Findings and notification history</li>
                <li>Workload history that mentions it</li>
              </ul>
            </div>
          </div>
          <div className="text-muted mt-1.5">
            If the maintenance wrapper is still installed on that machine, run <code className="text-text">sudo ./uninstall.sh</code> there before wiping it.
            If PVE lists this host again later, PyXie brings it back automatically.
          </div>
          {error && <div className="text-bad mt-1">{error}</div>}
          <div className="flex gap-2 mt-1.5">
            <button type="button" onClick={remove} disabled={busy} className="px-2 py-1 rounded text-[11px] font-medium bg-ink text-on-ink border border-bad hover:bg-bad/10 disabled:opacity-50">
              {busy ? "Removing…" : "Remove host"}
            </button>
            <button type="button" onClick={() => setConfirming(false)} className="text-[11px] text-muted hover:text-text">cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}
