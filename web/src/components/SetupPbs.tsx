"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { PbsAccountsBuilder, PbsHostWrapperBuilder } from "@/components/HostSetupBuilder";
import PbsTargetForm from "@/components/PbsTargetForm";
import type { Site } from "@/lib/api";

type Server = { id: string; name: string; hostname: string; credentials: Record<string, unknown>; ssh: { generated: boolean; host_key_pinned: boolean; wrapper_version: string | null }; status: { reachable: boolean; version?: string } | null };
/** Setup guide section for a Proxmox Backup Server: accounts, adding the server, tokens and SSH. Optional: PyXie works without one. */
export default function SetupPbs({ sites, tlsMode }: { sites: Site[]; tlsMode: string }) {
  const [servers, setServers] = useState<Server[] | null>(null);
  useEffect(() => { fetch("/api/pbs/servers").then((r) => r.json()).then((d) => setServers(Array.isArray(d) ? d : [])).catch(() => setServers([])); }, []);
  const mark = (ok: boolean) => <span className={ok ? "text-good" : "text-muted"}>{ok ? "✓" : "○"}</span>;
  return (
    <div className="text-sm">
      <p className="text-xs text-muted mb-3">Adds the 3-2-1 backup scorecard, backup job editing and patching for your backup server. Skip it if you do not use Proxmox Backup Server. Three parts, each adds a capability; only the first is needed to watch it.</p>
      <div className="text-sm font-medium text-text mb-1">a) Create the accounts on the backup server</div>
      <div className="mb-4"><PbsAccountsBuilder /></div>
      <div className="text-sm font-medium text-text mb-1">b) Add the server with the read-only token</div>
      <p className="text-xs text-muted mb-2">Form below. Then add the admin token on Credentials if you want PyXie to edit jobs.</p>
      <div className="space-y-2 mb-3">
        {(servers ?? []).map((s) => (
          <div key={s.id} className="flex items-center justify-between bg-surface2 rounded p-2 text-xs">
            <div><span className="text-text font-medium">{s.name}</span> <span className="text-muted">{s.hostname}{s.status?.reachable ? ` · PBS ${s.status.version}` : ""}</span>
              <div className="text-muted">{mark(!!s.credentials.inventory)} read token · {mark(!!s.credentials.administrative)} admin token · {mark(s.ssh.generated && s.ssh.host_key_pinned && !!s.ssh.wrapper_version)} patching</div></div>
            <div className="flex gap-3"><Link href="/platform/credentials" className="text-accent hover:underline">Credentials</Link><Link href={`/operations/protection/servers/${s.id}`} className="text-accent hover:underline">Open</Link></div>
          </div>
        ))}
      </div>
      {sites[0] && <PbsTargetForm siteId={sites[0].id} />}
      <div className="text-sm font-medium text-text mt-5 mb-1">c) Connect it for patching (optional)</div>
      <PbsHostWrapperBuilder servers={(servers ?? []).map((x) => ({ id: x.id, name: x.name }))} tlsMode={tlsMode} />
    </div>
  );
}
