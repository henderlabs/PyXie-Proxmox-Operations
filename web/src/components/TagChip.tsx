"use client";

import { colorFor, useTagColors } from "@/lib/tagColors";

/** One tag in the color Proxmox shows it in (its own override, or the automatic color when none is set). */
export function TagChip({ tag, clusterId, children }: { tag: string; clusterId: string | null | undefined; children?: React.ReactNode }) {
  const data = useTagColors();
  const c = colorFor(data, clusterId, tag);
  return (
    <span
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-medium"
      style={{ background: `#${c.bg}`, color: `#${c.fg}` }}
      title={c.auto ? "Automatic color: none is set for this tag in Proxmox" : "Color set in Proxmox"}
    >
      {tag}{children}
    </span>
  );
}

export default function TagChips({ tags, clusterId }: { tags: string[] | null | undefined; clusterId: string | null | undefined }) {
  if (!tags || tags.length === 0) return <span className="text-muted">—</span>;
  return <span className="inline-flex flex-wrap gap-1">{tags.map((t) => <TagChip key={t} tag={t} clusterId={clusterId} />)}</span>;
}
