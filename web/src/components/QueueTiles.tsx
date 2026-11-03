import { apiFetch } from "@/lib/api";
import { StatTile } from "@/components/Card";

type Lane = { waiting: number; running: number; oldest_waiting_seconds: number | null; longest_wait_seconds: number | null };
type Status = { profile: string; quick_lane_running: boolean; workers: { queues: string[]; heartbeat_seconds: number | null }[]; lanes: { quick: Lane; bulk: Lane } };

const dur = (s: number | null) => (s == null ? "none yet" : s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);
const laneValue = (l: Lane) => (l.waiting === 0 && l.running === 0 ? "idle" : `${l.running} running${l.waiting ? `, ${l.waiting} waiting` : ""}`);

/** Health page: how busy the operation lanes are and how long approved tasks waited in the last 7 days (the numbers that justify a second worker). */
export default async function QueueTiles() {
  let st: Status | null = null;
  try { st = await apiFetch<Status>("/api/workers"); } catch { st = null; }
  if (!st) return null;
  const split = st.quick_lane_running;
  const alive = st.workers.length;
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
      {split ? (
        <>
          <StatTile label="Quick lane" value={laneValue(st.lanes.quick)} sub={`longest wait, last 7 days: ${dur(st.lanes.quick.longest_wait_seconds)}`} />
          <StatTile label="Bulk lane" value={laneValue(st.lanes.bulk)} sub={`longest wait, last 7 days: ${dur(st.lanes.bulk.longest_wait_seconds)}`} />
        </>
      ) : (
        <>
          <StatTile label="Operations queue" value={laneValue({ waiting: st.lanes.quick.waiting + st.lanes.bulk.waiting, running: st.lanes.quick.running + st.lanes.bulk.running, oldest_waiting_seconds: null, longest_wait_seconds: null })} sub="Single worker: every task shares one lane" />
          <StatTile label="Longest wait, last 7 days" value={dur(Math.max(st.lanes.quick.longest_wait_seconds ?? 0, st.lanes.bulk.longest_wait_seconds ?? 0) || null)} sub={`quick-type tasks: ${dur(st.lanes.quick.longest_wait_seconds)}`} />
        </>
      )}
      <StatTile label="Workers" value={`${alive} alive`} sub={alive ? `last heartbeat ${Math.min(...st.workers.map((w) => w.heartbeat_seconds ?? 999))} s ago` : "no operations worker seen"} />
    </div>
  );
}
