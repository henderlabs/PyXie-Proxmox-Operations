import { PageHeader } from "@/components/Card";
import DefaultsManager from "@/components/DefaultsManager";
import { SlidersIcon } from "@/components/Icons";

export default function DefaultsPage() {
  return (
    <div>
      <PageHeader title="Defaults & templates" subtitle="Workloads · what PyXie suggests for new and existing VMs, per cluster, operating system, tag or pool, with a plain-language explanation of every choice" icon={<SlidersIcon className="w-5 h-5" />} />
      <DefaultsManager />
    </div>
  );
}
