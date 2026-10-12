"use client";

import { useState } from "react";
import type { Node, Workload } from "@/lib/api";
import AffinityPlacement from "@/components/AffinityPlacement";
import AffinityRuleManager, { type AffinityRule } from "@/components/AffinityRuleManager";
import PinRuleManager from "@/components/PinRuleManager";
import type { PinRule } from "@/lib/pins";

/** Affinity Rules page: where every guest sits (full width, rules and pins drawn on it), then affinity rules (left) and pinning (right). */
export default function AffinityPinWorkspace({ initialRules, initialPins, workloads, nodes }: { initialRules: AffinityRule[]; initialPins: PinRule[]; workloads: Workload[]; nodes: Node[] }) {
  const [rules, setRules] = useState(initialRules);
  const [pins, setPins] = useState(initialPins);
  return (
    <>
      <AffinityPlacement rules={rules} pins={pins} workloads={workloads} nodes={nodes} />
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 items-start">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-wider text-muted mb-2">Affinity rules</div>
          <AffinityRuleManager rules={rules} setRules={setRules} workloads={workloads} nodes={nodes} />
        </div>
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-wider text-muted mb-2">Pinning</div>
          <PinRuleManager pins={pins} setPins={setPins} workloads={workloads} nodes={nodes} />
        </div>
      </div>
    </>
  );
}
