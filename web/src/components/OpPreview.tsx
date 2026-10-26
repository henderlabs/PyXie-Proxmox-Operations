"use client";

import { useEffect, useState } from "react";
import type { Operation } from "@/lib/api";
import { notifyOperationsChanged } from "@/lib/operationsBus";
import { isInFlight } from "@/lib/operationStatus";
import { useOperationWatch } from "@/lib/useOperationWatch";

/** The preview -> approve -> follow -> result box every PyXie write uses. The parent makes the dry run and hands the operation in;
 * this shows what will happen, asks for the go-ahead, then follows the operation until it finishes. `autoApprove` skips the
 * second click for small, already-confirmed changes (the user pressed Save). */
export default function OpPreview({
  op: initial, confirmLabel, doneText, progressText, danger = false, autoApprove = false, onFinished, onClose,
}: {
  op: Operation;
  confirmLabel: string;
  doneText: string;
  progressText: string;
  danger?: boolean;
  autoApprove?: boolean;
  onFinished?: () => void;
  onClose: () => void;
}) {
  const [op, setOp] = useState<Operation>(initial);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [started, setStarted] = useState(false);

  async function approve() {
    setPending(true); setError(null);
    try {
      const r = await fetch(`/api/operations/${op.id}/approve`, { method: "POST" });
      const d = await r.json();
      if (!r.ok) { setError(d.error || "Approve failed"); return; }
      setOp(d); notifyOperationsChanged();
    } finally { setPending(false); }
  }

  useEffect(() => {
    if (autoApprove && !started && op.status === "awaiting_approval") { setStarted(true); approve(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Follows the operation whoever approves it: the Tasks panel or another tab can start or dismiss it while this box is open.
  useOperationWatch(op, {
    onUpdate: (next) => setOp(next),
    onGone: () => onClose(),
    onSettled: () => { notifyOperationsChanged(); onFinished?.(); },
  }, 2000);

  function cancel() {
    if (op.status === "awaiting_approval") fetch(`/api/operations/${op.id}/dismiss`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dismissed: true }) }).catch(() => {});
    notifyOperationsChanged();
    onClose();
  }

  const reasons = ((op.dry_run_result as { reasons?: string[] } | null)?.reasons) ?? [];
  const btn = danger ? "border-bad hover:bg-bad/10" : "border-warn hover:bg-warn/10";
  return (
    <div className="border border-border rounded p-3 bg-surface2/40 text-xs">
      {op.status === "blocked" ? (
        <div>
          <div className="text-bad mb-1">Blocked:</div>
          <ul className="text-muted mb-2 list-disc list-inside">{reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
          <button onClick={cancel} className="text-muted hover:text-text">back</button>
        </div>
      ) : isInFlight(op.status) ? (
        <div className="text-text">{progressText}… <span className="text-muted">(also in the Tasks panel)</span></div>
      ) : op.status === "completed" ? (
        <div>
          <div className="text-good mb-2">{doneText}</div>
          <button onClick={onClose} className="text-muted hover:text-text">close</button>
        </div>
      ) : op.status === "failed" ? (
        <div>
          <div className="text-bad mb-2">{op.error || "It failed"}</div>
          <button onClick={onClose} className="text-muted hover:text-text">close</button>
        </div>
      ) : (
        <div>
          <ul className="text-muted mb-2 list-disc list-inside">{reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
          {error && <div className="text-bad mb-2">{error}</div>}
          <div className="flex gap-2 items-center">
            <button disabled={pending} onClick={approve} className={`px-2 py-1 rounded bg-ink text-on-ink border ${btn} disabled:opacity-50`}>{pending ? "Starting…" : confirmLabel}</button>
            <button onClick={cancel} className="text-muted hover:text-text">cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}
