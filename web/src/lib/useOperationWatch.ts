"use client";

import { useEffect, useRef } from "react";
import { onOperationsChanged } from "@/lib/operationsBus";
import { isTerminal } from "@/lib/operationStatus";

type Watched = { id: string; status: string };

/** Keeps a panel's copy of an operation in step with the server, so approving, cancelling or dismissing it somewhere else (the Tasks
 * panel, another tab, another person) shows in the panel too, instead of the panel offering to approve something that already started.
 *
 * While the operation is not finished it is re-read every few seconds, and at once whenever something in the app signals an operations
 * change. `onUpdate` gets the new copy when its status, stage, progress or error changed; `onGone` runs when it was dismissed or
 * cancelled (the panel should close); `onSettled` runs once when it reaches a final state. Paused while the tab is hidden. */
export function useOperationWatch<T extends Watched>(
  op: T | null,
  handlers: { onUpdate: (next: T) => void; onGone: () => void; onSettled?: (next: T) => void },
  everyMs = 3000,
) {
  const h = useRef(handlers);
  h.current = handlers;
  const seen = useRef("");
  const id = op?.id ?? null;
  const live = !!op && !isTerminal(op.status);

  useEffect(() => {
    if (!id || !live) return;
    let stopped = false;
    seen.current = "";
    const tick = async () => {
      if (stopped || document.hidden) return;
      let res: Response;
      try { res = await fetch(`/api/operations/${id}`); } catch { return; }
      if (!res.ok || stopped) return;
      const next = (await res.json()) as T & { dismissed?: boolean; stage?: string; progress?: unknown; error?: string | null };
      if (stopped) return;
      if (next.dismissed || next.status === "cancelled") { stopped = true; h.current.onGone(); return; }
      const key = JSON.stringify([next.status, next.stage, next.progress, next.error]);
      if (key === seen.current) return;
      seen.current = key;
      h.current.onUpdate(next);
      if (isTerminal(next.status)) { stopped = true; h.current.onSettled?.(next); }
    };
    const iv = setInterval(tick, everyMs);
    const off = onOperationsChanged(tick);
    return () => { stopped = true; clearInterval(iv); off(); };
  }, [id, live, everyMs]);
}
