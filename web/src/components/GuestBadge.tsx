// VM / CT marker shown next to a guest name (plan rows, the Maintenance Workloads panel). Containers are the odd ones out: they cannot move live.
export default function GuestBadge({ type }: { type?: string | null }) {
  const ct = type === "lxc";
  return (
    <span
      className={`ml-1.5 align-middle px-1 py-0 rounded text-[10px] font-bold ${ct ? "bg-purple-500/20 text-purple-300" : "bg-accent/15 text-accent"}`}
      title={ct ? "Container (LXC): cannot move live, it is restarted when it moves" : "Virtual machine"}
    >
      {ct ? "CT" : "VM"}
    </span>
  );
}
