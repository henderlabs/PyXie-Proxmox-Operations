"use client";

import { useEffect, useState } from "react";
import { useMe } from "@/lib/useMe";

type Lane = { waiting: number; running: number; oldest_waiting_seconds: number | null; longest_wait_seconds: number | null };
type Limits = { evac_parallel: number; node_out: number; node_in: number; storage_moves: number; bulk_workers: number };
type Status = {
  profile: "single" | "standard" | "large";
  limits: Limits;
  slots_in_use: { kind: string; resource_key: string }[];
  quick_lane_running: boolean;
  workers: { queues: string[]; state: string; heartbeat_seconds: number | null }[];
  running_now: { id: string; operation_type_id: string }[];
  lanes: { quick: Lane; bulk: Lane };
};

const LONG_RUNNING = new Set(["workload.move_storage", "vm.live_migrate", "node.evacuate", "node.enter_maintenance", "maintenance.run", "cluster.rebalance", "workload.restore", "workload.clone", "host.update"]);

/** Settings card: how many workers run PyXie's tasks. Same text a setup step would show: what each choice does, why more is not always faster, what changing it costs. */
export default function WorkersSettings() {
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;
  const [st, setSt] = useState<Status | null>(null);
  const [choice, setChoice] = useState<"single" | "standard" | "large">("single");
  const [limits, setLimits] = useState<Limits>({ evac_parallel: 1, node_out: 1, node_in: 1, storage_moves: 1, bulk_workers: 1 });
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch("/api/workers").then((r) => (r.ok ? r.json() : null)).then((d: Status | null) => { if (d) { setSt(d); setChoice(d.profile); setLimits({ ...d.limits, bulk_workers: Math.max(2, d.limits.bulk_workers) }); } }).catch(() => {});
  }, []);
  if (!st) return <div className="text-xs text-muted">Reading the worker status…</div>;

  const longRunning = st.running_now.filter((o) => LONG_RUNNING.has(o.operation_type_id)).length;
  const pending = st.profile === "standard" && !st.quick_lane_running ? "Chosen, not running yet: it starts when the worker container restarts."
    : st.profile === "single" && st.quick_lane_running ? "Switched back to a single worker: it takes effect when the worker container restarts."
    : null;

  async function save(what: "profile" | "limits") {
    setBusy(true); setErr(null); setMsg(null);
    try {
      const body = what === "profile"
        ? { profile: choice, ...(choice === "large" ? { limits: { bulk_workers: limits.bulk_workers } } : {}) }
        : { limits: { evac_parallel: limits.evac_parallel, node_out: limits.node_out, node_in: limits.node_in, storage_moves: limits.storage_moves } };
      const r = await fetch("/api/workers", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || d.detail || `Save failed (${r.status})`);
      setMsg(what === "profile" ? `Saved. Applies ${d.profile_applies}. Nothing was restarted.` : "Saved. New tasks use the new limits straight away; tasks already running are not affected.");
      if (what === "profile") setSt({ ...st!, profile: choice });
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  const radio = (value: "single" | "standard" | "large", title: string, body: string) => (
    <label className={`flex items-start gap-2 text-sm `}>
      <input type="radio" name="workers" className="mt-1" disabled={!isAdmin} checked={choice === value} onChange={() => setChoice(value)} />
      <span><b className="text-text">{title}</b><span className="block text-xs text-muted">{body}</span></span>
    </label>
  );

  return (
    <div className="space-y-3">
      <div className="text-sm font-semibold text-text">How should PyXie run its tasks?</div>
      <div className="text-xs text-muted">
        Whenever you ask PyXie to do something in Proxmox (shut down a VM, move a disk, migrate a guest, update a host), that becomes a <b>task</b>. A <b>worker</b> is the part of PyXie that carries tasks out. With one worker, tasks go strictly one after another: if a big disk copy is running, a quick job such as shutting down a VM has to wait until the copy is finished, which can be half an hour or more. This choice decides whether short jobs get a worker of their own.
      </div>
      <div className="space-y-2">
        {radio("single", "One worker (the original behavior)", "Every task waits its turn in a single line, big or small. Nothing else changes. Choose this if you want PyXie to do exactly one thing at a time, or you are not sure.")}
        {radio("standard", "Two workers: one for quick jobs, one for long jobs (recommended)", "Quick jobs (start, shut down, reboot, tags, snapshots, resizing, network and setting edits) go to their own worker and run straight away, even while a long job is running. Long jobs (migrations, moving disks, restores, clones, evacuating a host, host updates) stay in the original line, still one at a time. Uses about 150 MB more memory. A quick job still will not touch a VM or host that a long job is using; it waits its turn for that one.")}
        {radio("large", "Several workers for long jobs", "Same quick-job worker as above, plus 2 to 4 workers for long jobs, so several long jobs can run at the same time (for example moves between different pairs of hosts). The limits below decide how much may really run together on one host or storage; more workers do not make a slow network faster, and several big copies on one shared network just slow each other down. Leave this on the two-worker choice unless you know your network has room.")}
        {choice === "large" && <div className="ml-6 text-xs text-muted">Number of long-job workers: <input type="number" min={2} max={4} value={limits.bulk_workers} onChange={(e) => setLimits({ ...limits, bulk_workers: Math.max(2, Math.min(4, Number(e.target.value) || 2)) })} className="w-14 bg-canvas border border-border rounded px-1 py-0.5 text-text" disabled={!isAdmin} /></div>}
      </div>
      <div className="text-xs text-muted">
        <b>Is it safe to change?</b> Yes. Nothing restarts when you press Save; the change starts the next time PyXie&apos;s worker service restarts (for example when PyXie is updated). A task that is running at that moment carries on from where it was.
        {longRunning > 0 && <span className="text-warn"> {longRunning} long task{longRunning === 1 ? " is" : "s are"} running now, so do not restart the worker service until {longRunning === 1 ? "it has" : "they have"} finished.</span>}
      </div>
      <div className="text-xs text-muted">
        Right now: quick-job worker {st.quick_lane_running ? <span className="text-good">running</span> : "not running"} · long-job worker {st.workers.some((w) => w.queues.includes("operations")) ? <span className="text-good">alive</span> : <span className="text-warn">not seen</span>}
        {pending && <span className="text-warn"> · {pending}</span>}
      </div>
      {err && <div className="text-xs text-bad">{err}</div>}
      {msg && <div className="text-xs text-good">{msg}</div>}
      {isAdmin && <button disabled={busy || (choice === st.profile && !(choice === "large" && limits.bulk_workers !== st.limits.bulk_workers))} onClick={() => save("profile")} className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50">{busy ? "Saving…" : "Save"}</button>}

      <div className="border-t border-border pt-3 mt-3 space-y-2">
        <div className="text-sm font-semibold text-text">How much may PyXie do at the same time?</div>
        <div className="text-xs text-muted">These limits protect your hosts, storage and network. Every one starts at 1, which is how PyXie has always behaved: one thing at a time. Raise one only if you know that part has spare capacity. A task that has to wait for a free place shows &quot;Waiting for&quot; and starts by itself.</div>
        {([
          ["evac_parallel", "Guests moved at once when you empty a host (Evacuate / Maint Mode, Evacuate)", "When you empty a host for maintenance, PyXie moves this many guests at the same time. 1 means one after another. 2 or 3 finish sooner if the hosts and the network between them have room; if they do not, every move gets slower and a live move can fail to catch up. Full Maintenance and Balance Load always move one at a time."],
          ["node_out", "Moves out of one host at once", "A host has one network connection (or a few) to send guests through, and more than one move out of the same host shares it. To move two guests off the same host at the same time this must be 2 or more as well."],
          ["node_in", "Moves into one host at once", "The same for the host that receives the guests."],
          ["storage_moves", "Disk copies per storage at once", "Copying disks to or from the same storage server (a NAS or shared storage) competes for that server's speed."],
        ] as [keyof Limits, string, string][]).map(([key, title, body]) => (
          <div key={key} className="flex items-start gap-3">
            <input type="number" min={1} max={4} value={limits[key]} disabled={!isAdmin} onChange={(e) => setLimits({ ...limits, [key]: Math.max(1, Math.min(4, Number(e.target.value) || 1)) })} className="w-14 bg-canvas border border-border rounded px-1 py-0.5 text-text text-sm mt-0.5" />
            <span className="text-sm"><b className="text-text">{title}</b><span className="block text-xs text-muted">{body}</span></span>
          </div>
        ))}
        <div className="text-xs text-muted"><b>A rule of thumb:</b> about one move at a time for every 1 Gb/s of network that is free for moving guests, minus one if the cluster&apos;s heartbeat shares that network. PyXie cannot measure free bandwidth for you.</div>
        {st.slots_in_use.length > 0 && <div className="text-xs text-muted">In use right now: {st.slots_in_use.length} place{st.slots_in_use.length === 1 ? "" : "s"} (moves or disk copies running).</div>}
        {isAdmin && <button disabled={busy} onClick={() => save("limits")} className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-proxmox hover:bg-proxmox/10 disabled:opacity-50">{busy ? "Saving…" : "Save limits"}</button>}
      </div>
    </div>
  );
}
