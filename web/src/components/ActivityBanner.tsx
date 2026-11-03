"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import type { Operation } from "@/lib/api";
import { operationTypeLabel } from "@/components/OperationCard";
import { pageFor, subjectFor } from "@/components/TaskPanel";
import { onOperationsChanged } from "@/lib/operationsBus";
import { useMe } from "@/lib/useMe";

const FAIL_WINDOW_MS = 30 * 60_000; // a failure stays (until dismissed) for this long

/** The bar under the header, kept for the two things that need you: a task waiting for your approval and a task that failed.
 * Everything else (running, queued, finished, Cancel) lives in the Tasks panel, which reads the same operations feed. It stops short of the right edge so the user box never covers it. */
export default function ActivityBanner() {
  const pathname = usePathname();
  const me = useMe();
  const [ops, setOps] = useState<Operation[]>([]);
  const [now, setNow] = useState(Date.now());
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (pathname === "/login") return;
    const load = () => fetch("/api/operations?limit=100").then((r) => (r.ok ? r.json() : null)).then((d) => Array.isArray(d) && setOps(d)).catch(() => {});
    load();
    const poll = setInterval(load, 5000);
    const tick = setInterval(() => setNow(Date.now()), 15000);
    const off = onOperationsChanged(load);
    return () => { clearInterval(poll); clearInterval(tick); off(); };
  }, [pathname]);
  if (pathname === "/login" || me?.activity_banner === false) return null;

  const top = ops.filter((o) => !o.parent_operation_id && !o.dismissed && !hidden.has(o.id));
  const waiting = top.filter((o) => o.status === "awaiting_approval");
  const failed = top.filter((o) => o.status === "failed" && now - new Date(o.completed_at || o.updated_at).getTime() < FAIL_WINDOW_MS).slice(0, 3);
  if (waiting.length + failed.length === 0) return null;

  const label = (o: Operation) => `${operationTypeLabel(o)} ${subjectFor(o)}`;
  function dismiss(o: Operation) {
    setHidden((h) => new Set(h).add(o.id));
    fetch(`/api/operations/${o.id}/dismiss`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dismissed: true }) }).catch(() => {});
  }
  const bar = "rounded border px-3 py-1.5 mb-1.5 text-xs flex flex-wrap items-center gap-2";

  return (
    <div className="mb-3 lg:mr-[var(--topbar-w,20rem)]">
      {waiting.map((o) => (
        <div key={o.id} className={`${bar} border-warn/50 bg-warn/10`}>
          <span>Waiting for approval:</span><span className="font-medium text-text">{label(o)}</span>
          <Link href={pageFor(o.operation_type_id, o.context, o.workload_id)} className="ml-auto text-accent hover:underline">Review</Link>
        </div>
      ))}
      {failed.map((o) => (
        <div key={o.id} className={`${bar} border-bad/50 bg-bad/10`}>
          <span className="text-bad">✕</span><span className="font-medium text-text">{label(o)}</span><span className="text-bad">failed{o.error ? `: ${o.error.slice(0, 120)}` : ""}</span>
          <span className="ml-auto flex gap-3"><Link href={pageFor(o.operation_type_id, o.context, o.workload_id)} className="text-accent hover:underline">Open task</Link><button type="button" onClick={() => dismiss(o)} className="text-muted hover:text-text">Dismiss</button></span>
        </div>
      ))}
    </div>
  );
}
