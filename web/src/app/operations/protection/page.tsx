import { apiFetch } from "@/lib/api";
import { Card, CardTitle, PageHeader } from "@/components/Card";
import BackupPlans from "@/components/BackupPlans";
import BackupStorage from "@/components/BackupStorage";
import ProtectionTabs from "@/components/ProtectionTabs";
import ProtectionGuests, { type GuestRow, type PlansByCluster } from "@/components/ProtectionGuests";
import RemovedGuests, { type RemovedRow, type UnknownRow } from "@/components/RemovedGuests";
import { ShieldIcon } from "@/components/Icons";

type Scorecard = { plans: PlansByCluster; summary: { ok: number; partial: number; none: number; total: number }; has_backup_server?: boolean; rows: GuestRow[]; removed?: RemovedRow[]; unknown?: UnknownRow[]; stores: { name: string }[]; errors: string[] };

export default async function ProtectionPage() {
  const [card] = await Promise.all([
    apiFetch<Scorecard>("/api/protection/scorecard"),
  ]);
  return (
    <div>
      <PageHeader title="Protection" subtitle="One backup plan says how every guest is backed up, copied, kept and checked. Exceptions are picked per guest below, where you also restore." icon={<ShieldIcon className="w-5 h-5" />} />
      <ProtectionTabs />
      {card.errors.map((e) => <div key={e} className="text-warn text-sm mb-2">{e}</div>)}

      {card.has_backup_server === false && (
        <Card className="mb-4">
          <CardTitle>No backup server is connected</CardTitle>
          <div className="text-sm text-muted space-y-2">
            <p>PyXie tracks backups through a Proxmox Backup Server. Without one it cannot see any backup, so it shows no protection status here instead of calling every guest unprotected.</p>
            <p>Guests may still be backed up by another product, for example Commvault: PyXie does not see those backups. To track backups in PyXie, add a Proxmox Backup Server under <a href="/platform/credentials" className="text-accent hover:underline">Credentials</a> (see <a href="/platform/providers" className="text-accent hover:underline">the Setup guide, step 6</a>).</p>
          </div>
        </Card>
      )}

      {card.has_backup_server !== false && (
        <>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
        <Card className="!p-3"><div className="text-xs text-muted" title="Enough fresh copies on enough kinds of storage, one offsite. It does not prove a restore works or that every disk is in the backup.">3-2-1 copies met</div><div className="text-2xl font-semibold text-good leading-tight">{card.summary.ok}<span className="text-xs text-muted font-normal"> of {card.summary.total} guests</span></div></Card>
        <Card className="!p-3"><div className="text-xs text-muted">Partly protected</div><div className="text-2xl font-semibold text-warn leading-tight">{card.summary.partial}<span className="text-xs text-muted font-normal"> a copy missing or old</span></div></Card>
        <Card className="!p-3"><div className="text-xs text-muted">Not protected</div><div className="text-2xl font-semibold text-bad leading-tight">{card.summary.none}<span className="text-xs text-muted font-normal"> no recent backup</span></div></Card>
        <BackupStorage variant="compact" />
      </div>

      <BackupPlans />

      <Card className="mb-4">
        <CardTitle>Guests</CardTitle>
        <div className="text-xs text-muted mb-2">Pick a plan on the row to change it. Guests that need attention open by themselves; open any guest to see its plan in detail and every restore point on site and offsite, with what pruning will keep or remove, and to restore from one.</div>
        <ProtectionGuests rows={card.rows} stores={card.stores.map((s) => s.name)} plans={card.plans ?? {}} />
      </Card>
      <RemovedGuests removed={card.removed ?? []} unknown={card.unknown ?? []} />
        </>
      )}
    </div>
  );
}
