"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Node, Operation, StorageItem, Workload } from "@/lib/api";
import OperationCard from "@/components/OperationCard";
import { Table, type Column } from "@/components/Table";
import { Meter } from "@/components/Gauges";
import { useOperationPolling } from "@/lib/useOperationPolling";
import { notifyOperationsChanged, onOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";

type WorkloadMetric = { cpu_pct?: number; mem_pct?: number; mem_source?: "guest" | "host"; mem_state?: string; mem_note?: string | null };
type WorkloadStorage = { name: string; scope: string | null };

type BulkReviewItem = {
  workload_id: string; vmid: number; name: string | null; power?: string;
  destination_node_id: string; destination_node: string; destination_storage_id: string | null;
  current_storage: string | null; currently_on_shared: boolean; transport: string;
  candidates: { node_id: string; node_name: string; blocked: boolean; blocking_reasons: string[] }[];
  storage_options: { id: string; name: string; scope: string; free_bytes: number | null }[];
  has_tpm?: boolean; tpm_storage?: string;
};
type BulkReviewSkipped = { workload_id: string; vmid: number | null; name: string | null; locked?: string; reasons: string[] };
type BulkBlocker = { id: string; status: string; kind: string; created_by: string | null; created_at: string | null; workloads: number };
type BulkReview = { blocker: BulkBlocker | null; migrate_plan: BulkReviewItem[]; skipped_workloads: BulkReviewSkipped[]; error?: string };
type BulkChoice = { destination_node_id?: string; destination_storage_id?: string };

type RecommendCandidate = {
  node_id: string;
  node_name: string;
  blocked: boolean;
  blocking_reasons: string[];
  score: number;
  reasons: string[];
  recommended_storage: { id: string; name: string; scope: string; reason: string } | null;
};

export default function WorkloadLifecycleForm({
  workloads: initialWorkloads,
  nodes,
  storage,
  metrics: initialMetrics,
  currentStorageByWorkload: initialCurrentStorage,
  selectedNodeIds,
  initialWorkloadId,
}: {
  workloads: Workload[];
  nodes: Node[];
  storage: StorageItem[];
  metrics: Record<string, WorkloadMetric>;
  currentStorageByWorkload: Record<string, WorkloadStorage>;
  selectedNodeIds: string[];
  // Arrived here from a VM/CT name link on the Workloads page: clicking
  // a VM/node name anywhere should jump to its "opposing"
  // page with that item already selected, for quick back-and-forth
  // between the two pages' different per-item actions). Same pattern as
  // NodeActionsForm's initialNodeId/initialActionKey.
  initialWorkloadId?: string;
}) {
  const [metrics, setMetrics] = useState(initialMetrics);
  const [currentStorageByWorkload, setCurrentStorageByWorkload] = useState(initialCurrentStorage);
  const [workloads, setWorkloads] = useState(initialWorkloads);

  // Same live-refresh pattern as the Nodes panel: metrics are a cheap DB
  // read (poll continuously); current-storage reads each VM's live qemu
  // config over the PVE API, so it only refreshes on the operations bus
  // (right after a move/resize/etc. actually happens here) rather than a
  // constant timer -- these need to be live-updating. workloads itself
  // (which node each one is actually ON) was still the one-time
  // server-rendered prop -- after a migration completed, this panel kept
  // showing the VM on its old node until a full page reload, a real gap
  // found live: two guests stayed listed under their old node well after
  // their Live Migration rows said Completed. Poll it the same way.
  useEffect(() => {
    function refreshMetrics() {
      fetch("/api/workloads/latest-metrics")
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => data && setMetrics(data))
        .catch(() => {});
    }
    function refreshStorage() {
      fetch("/api/workloads/current-storage")
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => data && setCurrentStorageByWorkload(data))
        .catch(() => {});
    }
    function refreshWorkloads() {
      fetch("/api/workloads")
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => Array.isArray(data) && setWorkloads(data))
        .catch(() => {});
    }
    refreshMetrics();
    refreshWorkloads();
    const interval = setInterval(refreshMetrics, 5000);
    // A migration/move can run for minutes with nobody clicking anything
    // in this tab -- workloads needs its own timer, not just the bus (the
    // bus only fires from a user action elsewhere on the page, e.g.
    // approving something; it's not a heartbeat).
    const workloadsInterval = setInterval(refreshWorkloads, 5000);
    const unsubscribe = onOperationsChanged(() => {
      refreshMetrics();
      refreshStorage();
      refreshWorkloads();
    });
    return () => {
      clearInterval(interval);
      clearInterval(workloadsInterval);
      unsubscribe();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [filterByNode, setFilterByNode] = useState(true);
  const [action, setAction] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState(120);
  const [justification, setJustification] = useState("");
  const [op, setOp] = useOperationPolling(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;

  // ---- Bulk Migrate ------------------------------------------------------
  // Tick VMs in the table, optionally pick one destination for all of them
  // (blank = best fit per VM), Preview -> one editable line per VM in the
  // same plan card Balance Load uses, approved once. It IS a cluster.rebalance
  // operation underneath, so execution/cancel/resume are the existing ones.
  const [bulkIds, setBulkIds] = useState<Set<string>>(new Set(initialWorkloadId ? [initialWorkloadId] : []));
  // One selection feeds every action: exactly one ticked VM uses the single-VM plan card, several use the combined review.
  const workloadId = bulkIds.size === 1 ? Array.from(bulkIds)[0] : "";
  const [bulkDestId, setBulkDestId] = useState("");
  const [bulkOp, setBulkOp] = useOperationPolling(null);
  const [bulkPending, setBulkPending] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);
  // Review step: what the plan WOULD be, fetched read-only (no operation exists yet), plus the operator's per-VM picks.
  const [bulkReview, setBulkReview] = useState<BulkReview | null>(null);
  const [bulkReviewLoading, setBulkReviewLoading] = useState(false);
  const [bulkChoices, setBulkChoices] = useState<Record<string, BulkChoice>>({});
  const [bulkReviewTick, setBulkReviewTick] = useState(0);

  useEffect(() => {
    if (bulkIds.size === 0 || action !== "move") {
      setBulkReview(null);
      setBulkChoices({});
      return;
    }
    let cancelled = false;
    setBulkReviewLoading(true);
    const t = setTimeout(async () => {
      try {
        const res = await fetch("/api/operations/cluster-rebalance/bulk-review", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ workload_ids: Array.from(bulkIds), destination_node_id: bulkDestId || null }),
        });
        const data = await res.json();
        if (cancelled) return;
        if (!res.ok) {
          setBulkReview({ blocker: null, migrate_plan: [], skipped_workloads: [], error: data.error || "Could not review the selection" });
        } else {
          setBulkReview(data as BulkReview);
        }
        setBulkChoices({});
      } catch (e) {
        if (!cancelled) setBulkReview({ blocker: null, migrate_plan: [], skipped_workloads: [], error: (e as Error).message });
      } finally {
        if (!cancelled) setBulkReviewLoading(false);
      }
    }, 400);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [bulkIds, bulkDestId, bulkReviewTick, action]);

  function setBulkChoice(id: string, patch: BulkChoice) {
    setBulkChoices((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));
  }

  async function dismissBlocker(opId: string) {
    setBulkError(null);
    const res = await fetch(`/api/operations/${opId}/dismiss`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      setBulkError(data.error || "Could not dismiss it");
      return;
    }
    notifyOperationsChanged();
    setBulkReviewTick((n) => n + 1);
  }

  const gbFree = (b: number | null) => (b == null ? "" : `, ${Math.round(b / 1024 ** 3)} GB free`);

  function toggleBulk(id: string) {
    setBulkIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function runBulkPreview() {
    setBulkPending(true);
    setBulkError(null);
    setBulkOp(null);
    try {
      const res = await fetch("/api/operations/cluster-rebalance/dry-run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workload_ids: Array.from(bulkIds), destination_node_id: bulkDestId || null, choices: bulkChoices }),
      });
      const data = await res.json();
      if (!res.ok) {
        setBulkError(data.error || "Bulk Migrate preview failed");
        return;
      }
      setBulkOp(data as Operation);
      notifyOperationsChanged();
    } catch (e) {
      setBulkError((e as Error).message);
    } finally {
      setBulkPending(false);
    }
  }

  async function approveBulk() {
    if (!bulkOp) return;
    setBulkPending(true);
    setBulkError(null);
    try {
      const res = await fetch(`/api/operations/${bulkOp.id}/approve`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        setBulkError(data.error || "Approve failed");
        return;
      }
      setBulkOp(data as Operation);
      setBulkIds(new Set());
      notifyOperationsChanged();
    } finally {
      setBulkPending(false);
    }
  }

  const nodeNameById = useMemo(() => Object.fromEntries(nodes.map((n) => [n.id, n.name])), [nodes]);

  // is_missing excluded here -- this feeds the "Select a VM..." action
  // picker below, not a visibility list. Unlike the Workloads page (which
  // deliberately still shows missing/deleted guests with an "unknown"
  // badge, since visibility of what USED to exist is the point there),
  // offering a deleted VM as a real action target here has no honest
  // outcome: PVE has nothing to act on -- a real gap found live, two
  // already-deleted guests (is_missing=true) were selectable here.
  const vms = useMemo(() => workloads.filter((w) => w.type === "vm" && !w.is_missing), [workloads]);
  const visibleVms = useMemo(
    () => (filterByNode && selectedNodeIds.length > 0 ? vms.filter((w) => selectedNodeIds.includes(w.node_id)) : vms),
    [vms, filterByNode, selectedNodeIds]
  );

  const bulkDestNodes = useMemo(() => {
    const clusterIds = new Set(vms.filter((w) => bulkIds.has(w.id)).map((w) => w.cluster_id));
    return nodes.filter((n) => clusterIds.has(n.cluster_id));
  }, [vms, bulkIds, nodes]);

  const bulkColumns: Column<Workload>[] = isAdmin
    ? [
        {
          header: "Pick",
          render: (w) => (
            <input
              type="checkbox"
              checked={bulkIds.has(w.id)}
              onChange={() => toggleBulk(w.id)}
              onClick={(e) => e.stopPropagation()}
              aria-label={`Select ${w.name || `vmid ${w.vmid}`} for bulk migrate`}
            />
          ),
        },
      ]
    : [];

  // Selections are for the move you are about to make, not a standing choice:
  // once the move has gone (approved from this card OR from the Task Panel),
  // clear the ticked rows; once a single-VM action completes, clear the picker
  // and action too. A failed action keeps its selection so it can be retried.
  // The finished/in-flight operation card itself stays on screen.
  useEffect(() => {
    if (bulkOp && !["awaiting_approval", "pending", "dry_run"].includes(bulkOp.status)) {
      setBulkIds(new Set());
      setBulkDestId("");
      setAction("");
    }
  }, [bulkOp?.status]);
  useEffect(() => {
    if (op?.status === "completed") {
      setBulkIds(new Set());
      setAction("");
    }
  }, [op?.status]);
  // A changed selection or action invalidates an open combined review and any single-VM plan.
  useEffect(() => {
    if (op && op.status === "awaiting_approval") setOp(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bulkIds, action]);

  // Arrived here via a VM name link from the Workloads page -- scroll the
  // now-preselected (and already-highlighted via rowClassName) row into
  // view instead of leaving the user to hunt for it in a scrolling table.
  const tableScrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!initialWorkloadId) return;
    tableScrollRef.current?.querySelector(".pyxie-jump-target")?.scrollIntoView({ behavior: "smooth", block: "center" });
    // Drop ?workload= from the address bar now that it has been applied, so a
    // page refresh starts with nothing selected instead of re-selecting it.
    try {
      const url = new URL(window.location.href);
      if (url.searchParams.has("workload")) {
        url.searchParams.delete("workload");
        window.history.replaceState(null, "", url.toString());
      }
    } catch {
      /* address bar tidy-up only */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function runDryRun() {
    setPending(true);
    setError(null);
    setOp(null);
    notifyOperationsChanged();
    try {
      // Several VMs become ONE plan (one approval); a single VM keeps its own plan.
      const many = bulkIds.size > 1;
      const res = await fetch(many ? "/api/operations/workload-lifecycle/bulk-dry-run" : "/api/operations/workload-lifecycle/dry-run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...(many ? { workload_ids: Array.from(bulkIds) } : { workload_id: workloadId }),
          action,
          timeout_seconds: action === "shutdown" ? timeoutSeconds : undefined,
          justification: action === "force_stop" ? justification : undefined,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Dry run failed");
        return;
      }
      setOp(data);
      notifyOperationsChanged();
    } finally {
      setPending(false);
    }
  }

  async function approve() {
    if (!op) return;
    setPending(true);
    setError(null);
    try {
      const res = await fetch(`/api/operations/${op.id}/approve`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Approve failed");
        return;
      }
      setOp(data);
      notifyOperationsChanged();
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-4 flex flex-col flex-1 min-h-0">
      <div className="flex items-center justify-between">
        <label className="flex items-center gap-2 text-xs text-muted cursor-pointer">
          <input type="checkbox" checked={filterByNode} onChange={(e) => setFilterByNode(e.target.checked)} />
          Filter to selected node(s)
        </label>
        <div className="flex items-center gap-3 text-xs text-muted">
          {isAdmin && (
            <>
              <button
                type="button"
                className="hover:underline"
                onClick={() => setBulkIds(new Set(visibleVms.map((w) => w.id)))}
                title="Tick every VM currently shown in the table (respects the node filter)"
              >
                Select all shown
              </button>
              {bulkIds.size > 0 && (
                <button type="button" className="hover:underline" onClick={() => setBulkIds(new Set())}>
                  Clear ({bulkIds.size})
                </button>
              )}
            </>
          )}
          <span>{visibleVms.length} VM{visibleVms.length === 1 ? "" : "s"}</span>
        </div>
      </div>

      <div className="max-h-[34rem] lg:max-h-none lg:flex-1 lg:basis-0 lg:min-h-[28rem] overflow-y-auto" ref={tableScrollRef}>
        <Table
          rows={visibleVms}
          emptyMessage={filterByNode && selectedNodeIds.length > 0 ? "No VMs on the selected node(s)." : "No VMs."}
          storageKey="maintenance-workloads"
          onRowClick={isAdmin ? (w) => toggleBulk(w.id) : undefined}
          rowClassName={(w) => (bulkIds.has(w.id) ? "bg-accent/10 pyxie-jump-target" : "")}
          columns={[
            ...bulkColumns,
            {
              header: "Name",
              render: (w) => (
                <Link
                  href={`/infrastructure/workloads/${w.id}`}
                  onClick={(e) => e.stopPropagation()}
                  className="text-accent hover:underline"
                  title="Open on the Workloads page"
                >
                  {w.name || `vmid ${w.vmid}`}
                </Link>
              ),
              sortValue: (w) => w.name || `vmid ${w.vmid}`,
            },
            {
              header: "Node",
              render: (w) => (
                <Link
                  href={`/infrastructure/nodes/${w.node_id}`}
                  onClick={(e) => e.stopPropagation()}
                  className="text-accent hover:underline"
                  title="Open on Hosts & Clusters"
                >
                  {nodeNameById[w.node_id] || "?"}
                </Link>
              ),
              sortValue: (w) => nodeNameById[w.node_id],
            },
            { header: "Status", render: (w) => w.status, sortValue: (w) => w.status },
            {
              header: "CPU",
              render: (w) => <Meter value={metrics[w.id]?.cpu_pct ?? null} width={56} />,
              sortValue: (w) => metrics[w.id]?.cpu_pct,
            },
            {
              header: "RAM",
              render: (w) => <Meter value={metrics[w.id]?.mem_pct ?? null} width={56} hostOnly={metrics[w.id]?.mem_source === "host"} memState={metrics[w.id]?.mem_state} memNote={metrics[w.id]?.mem_note} />,
              sortValue: (w) => metrics[w.id]?.mem_pct,
            },
            {
              header: "Storage",
              render: (w) => {
                const s = currentStorageByWorkload[w.id];
                return (
                  <span title={s ? `${s.name} (${s.scope === "node-local" ? "local" : s.scope === "cluster-shared" ? "shared" : "unknown"})` : undefined}>
                    {s?.name || "—"}
                  </span>
                );
              },
              sortValue: (w) => currentStorageByWorkload[w.id]?.name,
            },
          ]}
        />
      </div>

      {isAdmin && bulkIds.size > 0 && action === "move" && (
        <div className="flex flex-wrap items-end gap-3 border border-accent/30 rounded p-3">
          <div className="text-sm">
            <span className="font-medium">{bulkIds.size}</span> VM{bulkIds.size === 1 ? "" : "s"} selected to migrate
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Destination</label>
            <select
              className="bg-surface2 border border-border rounded px-2 py-1.5 text-sm min-w-[240px]"
              value={bulkDestId}
              onChange={(e) => setBulkDestId(e.target.value)}
            >
              <option value="">Auto: best fit for each VM</option>
              {bulkDestNodes.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.name}
                  {n.maintenance_mode ? " (in maintenance -- will be blocked)" : ""}
                </option>
              ))}
            </select>
          </div>
          <div className="w-full space-y-2">
            {bulkReviewLoading && !bulkReview && <div className="text-xs text-muted">Checking the selection…</div>}
            {bulkReview?.blocker && (
              <div className="border border-warn bg-warn/10 rounded p-2 text-sm">
                <span className="font-medium">
                  {bulkReview.blocker.kind} is already {bulkReview.blocker.status.replace(/_/g, " ")}
                </span>
                <span className="text-xs text-muted">
                  {" "}· {bulkReview.blocker.workloads} workload{bulkReview.blocker.workloads === 1 ? "" : "s"}
                  {bulkReview.blocker.created_by ? ` · created by ${bulkReview.blocker.created_by}` : ""}
                </span>
                <div className="text-xs mt-1">
                  Only one Balance Load or Bulk Migrate can be open at a time. Open it in the Tasks panel
                  {bulkReview.blocker.status === "awaiting_approval" && (
                    <>
                      {" "}or{" "}
                      <button type="button" className="underline text-accent" onClick={() => dismissBlocker(bulkReview.blocker!.id)}>
                        dismiss it
                      </button>{" "}
                      (dismissing only removes the plan; nothing has moved)
                    </>
                  )}
                  .
                </div>
              </div>
            )}
            {bulkReview?.error && <div className="text-sm text-bad">{bulkReview.error}</div>}
            {bulkReview && bulkReview.migrate_plan.length + bulkReview.skipped_workloads.length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-muted">
                      <th className="pr-3 py-1">VM</th><th className="pr-3">From → To</th><th className="pr-3">Storage</th><th>Check</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bulkReview.migrate_plan.map((it) => {
                      const ch = bulkChoices[it.workload_id] || {};
                      const destId = ch.destination_node_id || it.destination_node_id;
                      const stor = ch.destination_storage_id || it.destination_storage_id || "";
                      const chosenName = it.storage_options.find((o) => o.id === stor)?.name;
                      const tpmWarn = it.has_tpm && it.power === "running" && !!chosenName && chosenName !== it.tpm_storage;
                      return (
                        <tr key={it.workload_id} className="border-t border-border align-top">
                          <td className="pr-3 py-1">{it.name || `vmid ${it.vmid}`} <span className="text-xs text-muted">{it.power}</span></td>
                          <td className="pr-3">
                            <span className="text-xs text-muted">{nodeNameById[workloads.find((w) => w.id === it.workload_id)?.node_id || ""] || "?"} → </span>
                            <select
                              className="bg-surface2 border border-border rounded px-1 py-0.5 text-xs"
                              value={destId}
                              onChange={(e) => setBulkChoice(it.workload_id, { destination_node_id: e.target.value, destination_storage_id: undefined })}
                            >
                              {it.candidates.map((c) => (
                                <option key={c.node_id} value={c.node_id}>{c.node_name}{c.blocked ? " (blocked)" : ""}</option>
                              ))}
                            </select>
                          </td>
                          <td className="pr-3">
                            <select
                              className="bg-surface2 border border-border rounded px-1 py-0.5 text-xs"
                              value={stor}
                              disabled={!!ch.destination_node_id && ch.destination_node_id !== it.destination_node_id}
                              title={ch.destination_node_id && ch.destination_node_id !== it.destination_node_id ? "Storage options refresh after the plan is created; the recommended one is used." : undefined}
                              onChange={(e) => setBulkChoice(it.workload_id, { destination_storage_id: e.target.value })}
                            >
                              {it.storage_options.map((o) => (
                                <option key={o.id} value={o.id}>
                                  {o.name} ({o.scope === "cluster-shared" ? "shared" : "local"}{gbFree(o.free_bytes)})
                                </option>
                              ))}
                            </select>
                            {it.current_storage && <div className="text-[10px] text-muted">now on {it.current_storage}{it.currently_on_shared ? " (shared)" : " (local)"}</div>}
                          </td>
                          <td className="text-xs">
                            {tpmWarn ? (
                              <span className="text-warn" title="Proxmox cannot move a TPM disk while the VM is running">TPM disk stays on {it.tpm_storage}: Proxmox cannot move it while running</span>
                            ) : (
                              <span className="text-good">{it.transport === "live" ? "live move" : "offline move"}</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                    {bulkReview.skipped_workloads.map((sk) => (
                      <tr key={sk.workload_id} className="border-t border-border opacity-70">
                        <td className="pr-3 py-1">{sk.name || sk.workload_id}</td>
                        <td colSpan={2} className="text-xs text-muted">not in the plan</td>
                        <td className="text-xs text-bad">
                          {sk.locked ? `Locked: ${sk.locked}` : sk.reasons[0]}
                          {sk.locked && <div className="text-muted">Clear the lock in Proxmox, then re-select it.</div>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          <button
            onClick={runBulkPreview}
            disabled={bulkPending || bulkReviewLoading || !!bulkReview?.blocker || !bulkReview || bulkReview.migrate_plan.length === 0}
            title="Builds the editable plan from the choices above -- nothing moves until you review and approve it."
            className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10 disabled:opacity-50"
          >
            {bulkPending ? "Planning…" : `Create plan for ${bulkReview?.migrate_plan.length ?? 0} VM${bulkReview?.migrate_plan.length === 1 ? "" : "s"}`}
          </button>
          {bulkError && <div className="w-full text-sm text-bad">{bulkError}</div>}
        </div>
      )}

      {isAdmin && (
      <div className="flex flex-wrap items-end gap-3 sticky bottom-0 bg-surface py-2 border-t border-border">
        <div className="text-sm pb-1.5">
          <span className="font-medium">{bulkIds.size}</span> selected
        </div>
        <div>
          <label className="block text-xs text-muted mb-1">Action</label>
          <select
            className="bg-surface2 border border-border rounded px-2 py-1.5 text-sm disabled:opacity-50"
            value={action}
            disabled={bulkIds.size === 0}
            onChange={(e) => {
              setAction(e.target.value);
              setOp(null);
            }}
          >
            <option value="">Select an action…</option>
            <option value="move">Migrate to another node</option>
            <option value="shutdown">Graceful Shutdown</option>
            <option value="start">Start</option>
            <option value="force_stop">Force Stop (higher-risk)</option>
          </select>
        </div>
        {action === "shutdown" && (
          <div>
            <label className="block text-xs text-muted mb-1">Timeout (seconds)</label>
            <input
              type="number"
              min={10}
              className="bg-surface2 border border-border rounded px-2 py-1.5 text-sm w-24"
              value={timeoutSeconds}
              onChange={(e) => setTimeoutSeconds(Number(e.target.value))}
            />
          </div>
        )}
        {action === "force_stop" && (
          <div>
            <label className="block text-xs text-muted mb-1">Justification (required)</label>
            <input
              className="bg-surface2 border border-border rounded px-2 py-1.5 text-sm min-w-[240px]"
              value={justification}
              onChange={(e) => setJustification(e.target.value)}
              placeholder="why a forced stop is necessary"
            />
          </div>
        )}
        {action !== "move" && (
          <button
            onClick={runDryRun}
            disabled={bulkIds.size === 0 || !action || pending || (action === "force_stop" && !justification.trim())}
            title="Shows what would happen -- nothing changes until you review the result and approve it."
            className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10 disabled:opacity-50"
          >
            Preview
          </button>
        )}
        {bulkIds.size === 0 ? (
          <p className="text-xs text-muted w-full">Tick one or more VMs above to enable an action.</p>
        ) : !action ? (
          <p className="text-xs text-muted w-full">Select an action to continue.</p>
        ) : action === "move" ? (
          <p className="text-xs text-muted w-full">Choose the destination and storage for each VM above, then create the plan.</p>
        ) : null}
      </div>
      )}

      <p className="text-xs text-muted">
        Force Stop is deliberately never an automatic fallback if a graceful shutdown times out -- it's always its
        own separately-approved action.
      </p>

      {error && <div className="text-sm text-bad">{error}</div>}
      {op && <OperationCard op={op} pending={pending} onApprove={approve} onUpdated={setOp} />}
      {bulkOp && <OperationCard op={bulkOp} pending={bulkPending} onApprove={approveBulk} onUpdated={setBulkOp} />}
    </div>
  );
}
