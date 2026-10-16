"use client";

import { useEffect, useRef, useState } from "react";

const toGb = (mib: string) => (mib.trim() === "" || isNaN(Number(mib)) ? "" : String(Math.round((Number(mib) / 1024) * 100) / 100));

/** A memory box that shows gigabytes while the value is kept in MiB (what Proxmox stores): type 2 and it holds 2048. */
export default function GbInput({ value, onChange, className, min = 0, step = 0.25 }: { value: string; onChange: (mib: string) => void; className?: string; min?: number; step?: number }) {
  const [text, setText] = useState(toGb(value));
  const editing = useRef(false);
  useEffect(() => { if (!editing.current) setText(toGb(value)); }, [value]);
  return (
    <input
      type="number" min={min} step={step} className={className} value={text}
      onFocus={() => { editing.current = true; }}
      onBlur={() => { editing.current = false; setText(toGb(value)); }}
      onChange={(e) => { setText(e.target.value); const n = parseFloat(e.target.value); onChange(e.target.value !== "" && !isNaN(n) ? String(Math.round(n * 1024)) : ""); }}
    />
  );
}
