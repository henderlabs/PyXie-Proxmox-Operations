"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Re-runs the page's server data fetch every few seconds so a page left open stays current, without a reload and
 * without losing anything typed or scrolled (router.refresh keeps client state). Paused while the tab is hidden;
 * catches up at once when the tab becomes visible again. */
export default function AutoRefresh({ seconds = 30 }: { seconds?: number }) {
  const router = useRouter();
  useEffect(() => {
    let last = Date.now();
    const refresh = () => {
      if (document.hidden) return;
      last = Date.now();
      router.refresh();
    };
    const iv = setInterval(refresh, seconds * 1000);
    const onVisible = () => {
      if (!document.hidden && Date.now() - last > seconds * 1000) refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(iv);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [router, seconds]);
  return null;
}
