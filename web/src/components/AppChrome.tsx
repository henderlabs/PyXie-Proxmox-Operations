"use client";

import { Suspense, useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import NavProgress from "@/components/NavProgress";
import TopBar from "@/components/TopBar";
import Sidebar from "@/components/Sidebar";
import TaskPanel, { PANEL_WIDTH_PX } from "@/components/TaskPanel";

export default function AppChrome({ children, version, instance }: { children: React.ReactNode; version: string; instance: string }) {
  const pathname = usePathname();
  // The Tasks panel is a fixed, always-open dock on every page, the dashboard
  // included (TaskPanel renders nothing on /login).
  const showTasks = pathname !== "/login";
  // On a narrow window (split screen, phone) the dock would cover the page: show the Tasks panel as a slide-over opened from its edge tab instead.
  const [narrow, setNarrow] = useState(false);
  const [peek, setPeek] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 1100px)");
    const on = () => setNarrow(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  return (
    <>
      <Suspense fallback={null}>
        <NavProgress />
      </Suspense>
      <Sidebar version={version} />
      <main
        className={`relative flex-1 min-w-0 ${narrow ? "p-3" : "p-6"} transition-[margin-right] duration-200 ease-out`}
        style={{ marginRight: showTasks && !narrow ? PANEL_WIDTH_PX : 0 }}
      >
        <TopBar instance={instance} />
        {children}
      </main>
      {showTasks && (narrow
        ? <TaskPanel open={peek} setOpen={setPeek} pinned={false} setPinned={() => {}} locked />
        : <TaskPanel open setOpen={() => {}} pinned setPinned={() => {}} locked />)}
    </>
  );
}
