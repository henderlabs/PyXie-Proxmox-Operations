import Link from "next/link";
import { apiFetch } from "@/lib/api";
import type { Workload } from "@/lib/api";
import { Card, CardTitle, PageHeader } from "@/components/Card";
import CreateVmWizard from "@/components/CreateVmWizard";
import CreateCtForm from "@/components/CreateCtForm";
import { WorkloadIcon } from "@/components/Icons";
import { formatBytes } from "@/lib/format";

export default async function NewPage({ searchParams }: { searchParams: { kind?: string } }) {
  const ct = searchParams.kind === "ct";
  const all = ct ? [] : await apiFetch<Workload[]>("/api/workloads").catch(() => [] as Workload[]);
  const templates = all.filter((w) => w.is_template && !w.is_missing);
  const tab = (active: boolean) => `px-3 py-2 text-sm -mb-px border-b-2 ${active ? "border-accent text-accent" : "border-transparent text-muted hover:text-text"}`;
  return (
    <div>
      <PageHeader title={ct ? "New container" : "New VM"} subtitle={ct ? "An unprivileged container from a template: everything is checked against the cluster before anything is created" : "One disk, one network device, optional ISO: everything is checked against the cluster before anything is created"} icon={<WorkloadIcon className="w-5 h-5" />} />
      <div className="mb-3 text-sm"><Link href="/infrastructure/workloads" className="text-muted hover:text-text">← Workloads</Link></div>
      <div className="flex gap-1 border-b border-border mb-4">
        <Link href="/infrastructure/workloads/new" className={tab(!ct)}>Virtual machine</Link>
        <Link href="/infrastructure/workloads/new?kind=ct" className={tab(ct)}>Container</Link>
      </div>
      {ct ? <CreateCtForm /> : (
        <>
          {templates.length > 0 && (
            <Card>
              <CardTitle>Or start from a template</CardTitle>
              <ul className="text-sm divide-y divide-border">
                {templates.map((t) => (
                  <li key={t.id} className="py-1.5 flex items-center justify-between gap-3">
                    <span className="text-text">{t.name || `VM ${t.vmid}`} <span className="text-muted text-xs">VM {t.vmid} · {t.cpu_cores ?? "?"} vCPU · {formatBytes(t.memory_bytes)}</span></span>
                    <Link href={`/infrastructure/workloads/${t.id}?clone=1`} className="text-xs text-accent hover:underline">Clone it…</Link>
                  </li>
                ))}
              </ul>
            </Card>
          )}
          <div className="mt-4"><CreateVmWizard /></div>
        </>
      )}
    </div>
  );
}
