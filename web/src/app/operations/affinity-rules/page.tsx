import { apiFetch } from "@/lib/api";
import type { Node, Workload } from "@/lib/api";
import { PageHeader } from "@/components/Card";
import AffinityPinWorkspace from "@/components/AffinityPinWorkspace";
import type { AffinityRule } from "@/components/AffinityRuleManager";
import type { PinRule } from "@/lib/pins";
import { LinkIcon } from "@/components/Icons";

export default async function AffinityRulesPage() {
  const [rules, pins, workloads, nodes] = await Promise.all([
    apiFetch<AffinityRule[]>("/api/placement/affinity-rules"),
    apiFetch<PinRule[]>("/api/placement/pin-rules"),
    apiFetch<Workload[]>("/api/workloads"),
    apiFetch<Node[]>("/api/nodes"),
  ]);

  return (
    <div>
      <PageHeader
        title="Affinity Rules & Pinning"
        subtitle="Rules PyXie checks before it places or moves a guest, and the hosts guests are pinned to. Separate from Proxmox's own HA affinity rules."
        icon={<LinkIcon className="w-5 h-5" />}
      />
      <AffinityPinWorkspace initialRules={rules} initialPins={pins} workloads={workloads} nodes={nodes} />
    </div>
  );
}
