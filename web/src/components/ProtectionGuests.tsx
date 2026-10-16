"use client";

import Link from "next/link";
import { Table } from "@/components/Table";
import RestorePoints from "@/components/RestorePoints";

type Copy = { store: string; offsite: boolean; last: number | null; age_text: string; fresh: boolean };
export type GuestRow = { id?: string; workload_id: string; vmid: number; name: string; type: string; status: "ok" | "partial" | "none"; copies: Copy[]; problems: string[]; covered_by: string[] };

const statusText = { ok: "3-2-1 met", partial: "Partly protected", none: "Not protected" } as const;
const statusColor = { ok: "text-good", partial: "text-warn", none: "text-bad" } as const;
const order = { none: 0, partial: 1, ok: 2 } as const;

/** Every guest with its 3-2-1 result; open a row for all of its restore points and to restore from one. */
export default function ProtectionGuests({ rows, stores }: { rows: GuestRow[]; stores: string[] }) {
  return (
    <Table<GuestRow & { id: string }>
      rows={rows.map((r) => ({ ...r, id: r.workload_id }))}
      emptyMessage="No guests."
      storageKey="protection-guests"
      singleExpand
      renderDetail={(r) => <RestorePoints workloadId={r.workload_id} name={r.name} vmid={r.vmid} />}
      columns={[
        {
          header: "Guest",
          render: (r) => (
            <span className="flex items-center gap-1.5">
              <Link href={`/infrastructure/workloads/${r.workload_id}`} className="text-accent hover:underline">{r.name}</Link>
              <span className="text-xs text-muted">{r.vmid}</span>
              <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-surface2 text-muted">{r.type === "lxc" ? "CT" : "VM"}</span>
            </span>
          ),
          sortValue: (r) => r.name,
        },
        { header: "Result", render: (r) => <span className={statusColor[r.status]}>{statusText[r.status]}</span>, sortValue: (r) => order[r.status] },
        ...stores.map((s) => ({
          header: s,
          render: (r: GuestRow & { id: string }) => {
            const c = r.copies.find((x) => x.store === s);
            return <span className={c?.fresh ? "text-text" : c?.last ? "text-warn" : "text-muted"}>{c?.age_text ?? "never"}</span>;
          },
          sortValue: (r: GuestRow & { id: string }) => r.copies.find((x) => x.store === s)?.last ?? 0,
        })),
        { header: "Job", render: (r) => (r.covered_by.length ? <span className="text-xs">{r.covered_by.join(", ")}</span> : <span className="text-warn text-xs">none</span>), sortValue: (r) => r.covered_by.join(",") },
        { header: "What is missing", render: (r) => <span className="text-xs text-muted">{r.problems.join("; ") || "Nothing"}</span> },
      ]}
    />
  );
}
