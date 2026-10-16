"use client";

import { useState } from "react";
import type { StorageDefinition, StorageItem } from "@/lib/api";
import { Table } from "@/components/Table";
import { NoInfrastructureHint } from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import { formatBytes } from "@/lib/format";
import { Meter } from "@/components/Gauges";

const HOLDS: Record<string, string> = { images: "VM disks", rootdir: "Container disks", iso: "ISO images", vztmpl: "Container templates", backup: "Backups", snippets: "Snippets", import: "Import" };

export default function StorageTable({ storage, defs = [], nodeNames = {} }: { storage: StorageItem[]; defs?: StorageDefinition[]; nodeNames?: Record<string, string> }) {
  const defOf = (s: StorageItem) => defs.find((d) => d.name === s.name && (!s.cluster_id || d.cluster_id === s.cluster_id));
  // No longer visible to PVE -- kept in the DB rather than hard-deleted,
  // but hidden from the main list by default, same fix as the Workloads
  // and Hosts & Clusters pages' "Show removed" toggle (2026-09-15).
  const missingStorage = storage.filter((s) => s.is_missing);
  const [showRemoved, setShowRemoved] = useState(false);
  const visibleStorage = showRemoved ? storage : storage.filter((s) => !s.is_missing);

  return (
    <div>
      {missingStorage.length > 0 && (
        <label className="flex items-center gap-2 text-xs text-muted mb-2 cursor-pointer w-fit">
          <input type="checkbox" checked={showRemoved} onChange={(e) => setShowRemoved(e.target.checked)} />
          <span>
            Show removed ({missingStorage.length}){" "}
            <span className="text-text/70">-- no longer visible to PVE, kept here for history</span>
          </span>
        </label>
      )}
      <Table
      rows={visibleStorage}
      emptyMessage={<NoInfrastructureHint subject="storage" />}
      storageKey="infrastructure-storage"
      columns={[
        { header: "Name", render: (s) => s.name, sortValue: (s) => s.name },
        { header: "Type", render: (s) => s.type || "—", sortValue: (s) => s.type, optional: true },
        {
          header: "Holds",
          render: (s) => {
            const d = defOf(s);
            if (!d) return <span className="text-muted">—</span>;
            return (
              <span className="flex flex-wrap gap-1">
                {d.content.map((c) => (
                  <span key={c} className={c === "iso" ? "text-[11px] px-1.5 py-0.5 rounded border border-accent/50 text-accent bg-accent/10" : "text-[11px] px-1.5 py-0.5 rounded border border-border text-muted"}>{HOLDS[c] ?? c}</span>
                ))}
              </span>
            );
          },
          sortValue: (s) => (defOf(s)?.content.includes("iso") ? 0 : 1),
        },
        {
          header: "Location",
          render: (s) => { const d = defOf(s); return d?.server ? `${d.server}:${d.export ?? ""}` : d?.path || "—"; },
          sortValue: (s) => defOf(s)?.server ?? defOf(s)?.path ?? "",
          optional: true,
        },
        { header: "Host", render: (s) => (s.node_id ? nodeNames[s.node_id] ?? "—" : "all hosts"), sortValue: (s) => (s.node_id ? nodeNames[s.node_id] ?? "" : "") },
        { header: "Scope", render: (s) => s.scope, sortValue: (s) => s.scope },
        {
          header: "Status",
          render: (s) => <StatusBadge status={s.is_missing ? "unknown" : s.status} />,
          sortValue: (s) => (s.is_missing ? "unknown" : s.status),
        },
        { header: "Capacity", render: (s) => formatBytes(s.capacity_bytes), sortValue: (s) => s.capacity_bytes, optional: true },
        { header: "Used", render: (s) => formatBytes(s.used_bytes), sortValue: (s) => s.used_bytes, optional: true },
        {
          header: "Utilization",
          render: (s) => (
            <Meter value={s.capacity_bytes && s.used_bytes ? (s.used_bytes / s.capacity_bytes) * 100 : null} width={80} />
          ),
          sortValue: (s) => (s.capacity_bytes && s.used_bytes ? (s.used_bytes / s.capacity_bytes) * 100 : null),
        },
      ]}
      />
    </div>
  );
}
