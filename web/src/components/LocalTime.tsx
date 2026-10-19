"use client";

import { useEffect, useState } from "react";

/** A time shown in the viewer's own time zone (the same one the header clock uses). Server pages render in UTC, so they hand the ISO time to this. */
export default function LocalTime({ iso, dateOnly = false }: { iso: string | null | undefined; dateOnly?: boolean }) {
  const [text, setText] = useState("");
  useEffect(() => {
    setText(iso ? (dateOnly ? new Date(iso).toLocaleDateString() : new Date(iso).toLocaleString()) : "—");
  }, [iso, dateOnly]);
  return <span suppressHydrationWarning>{text}</span>;
}
