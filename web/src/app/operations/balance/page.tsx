import { apiFetch } from "@/lib/api";
import type { Node, Workload } from "@/lib/api";
import type { PinRule } from "@/lib/pins";
import { PageHeader } from "@/components/Card";
import BalanceLoadWorkspace from "@/components/BalanceLoadWorkspace";
import AutoBalanceCard, { type AutoBalanceView } from "@/components/AutoBalanceCard";
import BalanceStatusStrip from "@/components/BalanceStatusStrip";
import BalanceHistory from "@/components/BalanceHistory";
import { MigrateIcon } from "@/components/Icons";

export default async function BalanceLoadPage({ searchParams }: { searchParams: { node?: string } }) {
  const [nodes, workloads, auto, pins] = await Promise.all([
    apiFetch<Node[]>("/api/nodes"),
    apiFetch<Workload[]>("/api/workloads"),
    apiFetch<AutoBalanceView[]>("/api/auto-balance"),
    apiFetch<PinRule[]>("/api/placement/pin-rules"),
  ]);
  const first = auto[0];
  const autoSummary = first ? { mode: first.config.mode, level: first.config.level, trigger: first.presets[first.config.level].trigger_score } : undefined;
  const pending = auto.find((c) => c.waiting_operation_id)?.waiting_operation_id ?? auto.find((c) => c.pending_operation_id)?.pending_operation_id;
  return (
    <div>
      <PageHeader
        title="Balance Load"
        subtitle="Preview only: nothing moves until you approve. Shows memory per node before and after the plan, and why any move was refused."
        icon={<MigrateIcon className="w-5 h-5" />}
      />
      <BalanceStatusStrip clusters={auto} />
      <BalanceLoadWorkspace nodes={nodes} workloads={workloads} pins={pins} initialNodeId={searchParams.node} pendingOperationId={pending ?? undefined} autoSummary={autoSummary} />
      <div className="mt-4"><AutoBalanceCard clusters={auto} /></div>
      <div className="mt-4"><BalanceHistory /></div>
    </div>
  );
}
