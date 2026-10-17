"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/operations/protection", label: "Backup plan & guests", help: "The plan, every guest, restore points and restore" },
  { href: "/operations/protection/servers", label: "Backup servers", help: "Datastores, pruning, garbage collection, verification, storage used" },
];

/** The two tabs of Protection: the plan and guests, and the backup servers. */
export default function ProtectionTabs() {
  const path = usePathname();
  return (
    <div className="flex gap-1 border-b border-border mb-4">
      {TABS.map((t) => {
        const on = t.href === "/operations/protection" ? path === t.href : path.startsWith(t.href);
        return <Link key={t.href} href={t.href} title={t.help} className={`px-4 py-2 text-sm -mb-px border-b-2 ${on ? "border-proxmox text-text" : "border-transparent text-muted hover:text-text"}`}>{t.label}</Link>;
      })}
    </div>
  );
}
