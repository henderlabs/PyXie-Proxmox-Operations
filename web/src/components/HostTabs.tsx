import Link from "next/link";

export type HostTab = "overview" | "config" | "shell";

/** Overview / Config / Shell tabs on a host page, laid out like the workload page. They sit
 * directly under the live tiles, so a tab click never needs to scroll. */
export default function HostTabs({ nodeId, tab, showShell }: { nodeId: string; tab: HostTab; showShell: boolean }) {
  const base = `/infrastructure/nodes/${nodeId}`;
  const item = (key: HostTab, label: string, href: string) => (
    <Link
      scroll={false}
      href={href}
      className={`pb-1.5 ${tab === key ? "text-text border-b-2 border-accent" : "text-muted hover:text-text"}`}
    >
      {label}
    </Link>
  );
  return (
    <div className="flex gap-4 border-b border-border mb-3 text-sm">
      {item("overview", "Overview", base)}
      {item("config", "Config", `${base}?tab=config`)}
      {showShell && item("shell", "Shell", `${base}?tab=shell`)}
    </div>
  );
}
