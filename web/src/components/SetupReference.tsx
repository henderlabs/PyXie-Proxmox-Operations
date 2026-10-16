/* Reference material for the Setup guide (formerly the Quick start page): collapsed until needed. */
import { Card } from "@/components/Card";
const PRIVS: [string, string][] = [
  ["Datastore.Allocate", "Create and remove storage-backed resources when a guest or disk moves."],
  ["Datastore.AllocateSpace", "Allocate space on a storage for migrations, disk moves and resizes."],
  ["Datastore.AllocateTemplate", "Add and delete ISO images and container templates."],
  ["Pool.Allocate", "Create and delete resource pools and change which guests belong to them."],
  ["Pool.Audit", "See which pools exist and who is in them (Proxmox hides pools from an account without it)."],
  ["SDN.Use", "Attach a network device to a bridge."],
  ["Sys.Audit", "Read a node's status."],
  ["Sys.Modify", "List pending package updates and refresh a node's package lists (Proxmox requires this, not just Sys.Audit)."],
  ["Sys.PowerMgmt", "Reboot a host."],
  ["VM.Allocate", "Create and delete guests (deleting only through the guarded Delete action)."],
  ["VM.Audit", "Read a guest's state."],
  ["VM.Clone", "Clone a VM or container."],
  ["VM.Backup", "Put VMs into backup jobs (backup policies) and change those jobs."],
  ["VM.Config.CDROM", "Insert, change and eject ISOs in CD/DVD drives."],
  ["VM.Config.CPU", "Resize CPU."],
  ["VM.Config.Memory", "Resize memory and ballooning."],
  ["VM.Config.Disk", "Resize disks."],
  ["VM.Config.HWType", "Change a VM's machine type and CPU type."],
  ["VM.Config.Network", "Add, edit and remove network devices."],
  ["VM.Config.Options", "Change a guest's tags (and other option fields)."],
  ["VM.Migrate", "Live and offline migration, evacuation."],
  ["VM.PowerMgmt", "Start, stop, shut down and reboot guests."],
  ["VM.Snapshot", "Take and delete VM snapshots."],
  ["VM.Snapshot.Rollback", "Roll a VM back to a snapshot."],
  ["VM.Console (optional)", "The embedded console. Added only if you choose it in the script builder."],
];

// In the order you meet them: each Proxmox account (account, token, role), the file the script writes, then each host.
const NAMES: [string, string, string][] = [
  ["Proxmox account (required)", "pyxie-ro@pve", "The read-only account. Holds the inventory token."],
  ["Proxmox token (required)", "pyxie-ro@pve!inventory", "Token ID inventory. PyXie credential purpose: inventory."],
  ["Proxmox role (required)", "PVEAuditor", "Built into Proxmox. Granted at / to the read-only user and its token."],
  ["Proxmox account (required)", "pyxie-admin@pve", "The write-capable account. Holds the maintenance token."],
  ["Proxmox token (required)", "pyxie-admin@pve!maintenance", "Token ID maintenance. PyXie credential purpose: maintenance."],
  ["Proxmox role (required)", "PyXieAdmin", "Custom role the script creates: the privileges listed below. Granted at / to the admin user and its token. Gets VM.Console too if you choose that in the builder."],
  ["On the node you ran it on", "/root/pyxie-tokens.txt", "Where the account script saves the token secrets (root only). Copy them into PyXie, then delete the file with shred -u."],
  ["Proxmox account (optional)", "pyxie-console@pve", "Only if you choose a separate console token. Holds the console token."],
  ["Proxmox token (optional)", "pyxie-console@pve!console", "Token ID console. PyXie credential purpose: console."],
  ["Proxmox role (optional)", "PVEVMConsole", "Built into Proxmox. Only for that optional console token, at /vms."],
  ["Each host", "pyxie-hostmaint", "The SSH identity PyXie uses to patch and reboot a node (no shell, seven fixed commands)."],
  ["Backup server (optional)", "pyxie-pbs-ro@pbs", "The read-only Proxmox Backup Server account. Holds the inventory token. Audit role on /."],
  ["Backup server (optional)", "pyxie-pbs-admin@pbs", "The account that edits sync, prune and verify jobs. Holds the administrative token. Audit on /, DatastoreAdmin on /datastore, RemoteSyncOperator on /remote."],
  ["Each host", "/usr/local/sbin/pyxie-maint", "The wrapper that identity may run. Its sudoers entry is /etc/sudoers.d/pyxie-maint."],
];

function Facts({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="mb-2">
      <div className="text-[11px] uppercase tracking-wide text-muted mb-0.5">{title}</div>
      <ul className="list-disc pl-5 text-xs text-text space-y-0.5">{items.map((i) => <li key={i}>{i}</li>)}</ul>
    </div>
  );
}


export default function SetupReference() {
  return (
    <div id="reference" className="mb-8">
      <h2 className="text-base font-semibold text-text mb-1">Reference</h2>
      <p className="text-xs text-muted mb-3">What each account is for, the names used, and the manual alternatives. Open a section when you need it.</p>
      <details className="border border-border rounded-lg p-3 mb-2"><summary className="cursor-pointer text-sm font-medium text-text">The two Proxmox tokens: what each is for</summary>
      <div className="grid md:grid-cols-2 gap-4 mt-2">
        <Card>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[11px] px-2 py-0.5 rounded bg-good/15 text-good">Token 1 · Read-only</span>
            
          </div>
          <h2 className="text-base font-semibold text-text">Inventory</h2>
          <p className="text-xs text-muted mb-3">Lets PyXie look. It cannot change anything.</p>
          <Facts title="In Proxmox" items={["User pyxie-ro@pve, token ID inventory", "Role PVEAuditor (built in) at /, for the user and for the token", "Privilege separation on"]} />
          <Facts title="PyXie uses it for" items={[
            "Discovery and sync of hosts, VMs and storage",
            "Every preview (dry run) of every action: migration, evacuation, maintenance, reboots, resize, balance",
            "Health findings, right-sizing, capacity, reports and metrics history",
            "Live VM and memory data, and the \"is this VM really responding\" check",
          ]} />
          <Facts title="It cannot" items={["Start, stop, migrate or resize anything", "List pending package updates (Proxmox wants Sys.Modify for that)"]} />
          <Facts title="In PyXie" items={["Setup guide step 3: Connect your cluster", "Token user pyxie-ro@pve, token ID inventory, the secret, then Test connection"]} />
        </Card>

        <Card>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[11px] px-2 py-0.5 rounded bg-warn/15 text-warn">Token 2 · Admin</span>
            
          </div>
          <h2 className="text-base font-semibold text-text">Maintenance (Admin)</h2>
          <p className="text-xs text-muted mb-3">Lets PyXie act, after you preview and approve each action.</p>
          <Facts title="In Proxmox" items={["User pyxie-admin@pve, token ID maintenance", "Role PyXieAdmin (custom, privileges below) at /, for the user and for the token", "Privilege separation on"]} />
          <Facts title="PyXie uses it for" items={[
            "Migration, evacuation and maintenance mode",
            "Start, stop, reboot and resize guests",
            "Host reboots, and pending-update counts and task logs",
            "Optional: the embedded console (adds VM.Console)",
          ]} />
          <Facts title="Safety" items={[
            "Off until Settings > Allow PyXie to write to Proxmox VE is switched on",
            "Every action is previewed, safety-checked and approved by you first",
            "Scoped at the root (/), a documented, accepted trade-off: see the README's Safety Contract",
          ]} />
          <Facts title="In PyXie" items={["Setup guide step 4 (Credentials page): add purpose maintenance (admin)", "Token user pyxie-admin@pve, token ID maintenance, the secret, then Test connection"]} />
        </Card>
      </div>
      </details>
      <details className="border border-border rounded-lg p-3 mb-2"><summary className="cursor-pointer text-sm font-medium text-text">Proxmox Backup Server: the two accounts and the SSH identity</summary>
        <div className="mt-2 grid md:grid-cols-3 gap-4">
          <div><div className="text-[11px] px-2 py-0.5 rounded bg-good/15 text-good inline-block mb-1">Read-only</div><h3 className="text-sm font-semibold text-text">pyxie-pbs-ro@pbs</h3>
            <Facts title="In the backup server" items={["Token name inventory", "Audit at / and RemoteAudit at /remote, for the user and its token (without Remote.Audit PBS hides sync jobs)"]} />
            <Facts title="PyXie uses it for" items={["Status, datastore usage, tasks, pending updates", "The 3-2-1 scorecard: backups per datastore, prune, verify and sync jobs"]} /></div>
          <div><div className="text-[11px] px-2 py-0.5 rounded bg-warn/15 text-warn inline-block mb-1">Admin</div><h3 className="text-sm font-semibold text-text">pyxie-pbs-admin@pbs</h3>
            <Facts title="In the backup server" items={["Token name administrative", "Audit at /, DatastoreAdmin at /datastore, RemoteSyncOperator at /remote, each for the user and its token"]} />
            <Facts title="PyXie uses it for" items={["Creating, changing, deleting and running sync, prune and verify jobs, after you confirm a preview", "It cannot install packages or touch the operating system"]} /></div>
          <div><div className="text-[11px] px-2 py-0.5 rounded bg-surface2 text-muted inline-block mb-1">SSH</div><h3 className="text-sm font-semibold text-text">pyxie-hostmaint</h3>
            <Facts title="On the backup server" items={["Created by the host kit, with the pyxie-maint wrapper and a sudoers entry", "Same restricted identity as on Proxmox hosts: fixed commands, no shell, pinned host key"]} />
            <Facts title="PyXie uses it for" items={["Installing package updates (neither the PVE nor the PBS API can), only when PBS is idle", "It never reboots the server"]} /></div>
        </div>
      </details>
      <details className="border border-border rounded-lg p-3 mb-2"><summary className="cursor-pointer text-sm font-medium text-text">Names used in this guide</summary><div className="mt-2">
        <p className="text-xs text-muted mb-2">Everything the setup creates, in the order you will meet it. These are the default names; they are only labels. You can pick your own in the Script builder, and an existing install may use others (for example pyxie-maint@pve): PyXie works with whatever names you enter on the Credentials page.</p>
        <table className="w-full text-xs">
          <thead><tr className="text-left text-muted"><th className="py-1 pr-3">Where</th><th className="pr-3">Name</th><th>What it is</th></tr></thead>
          <tbody>
            {NAMES.map(([where, name, what]) => (
              <tr key={where + name} className="border-t border-border align-top">
                <td className="py-1.5 pr-3 text-muted whitespace-nowrap">{where}</td>
                <td className="pr-3 font-mono whitespace-nowrap text-text">{name}</td>
                <td className="py-1.5 text-muted">{what}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div></details>

      <details className="border border-border rounded-lg p-3 mb-2"><summary className="cursor-pointer text-sm font-medium text-text">Putting the host kit on a node</summary><div className="mt-2">
        <p className="text-xs text-muted mb-2">The host wrapper script builder (on the Credentials page) writes the script and shows the exact commands with your node address filled in. The three ways, in short:</p>
        <ol className="list-decimal pl-5 text-sm text-text space-y-1.5">
          <li><span className="font-medium">Paste on the node (simplest).</span> <span className="text-muted text-xs">Log in to the node as root, paste the generated script into a file with <code>nano</code>, run it with <code>bash</code>. It downloads one installer from a 30-minute link and refuses to run unless the checksum matches.</span></li>
          <li><span className="font-medium">Copy the installer file.</span> <span className="text-muted text-xs">Download <code>pyxie-host-kit.sh</code>, <code>scp</code> it to the node, compare <code>sha256sum</code> with the checksum PyXie shows, run <code>bash pyxie-host-kit.sh</code>. Nothing to unzip.</span></li>
          <li><span className="font-medium">Copy the classic .tar.gz kit.</span> <span className="text-muted text-xs"><code>scp</code> it over, <code>tar xzf</code> it, <code>cd</code> into the folder and run <code>bash install.sh</code> as root.</span></li>
        </ol>
        <p className="text-xs text-muted mt-2">Then check it on the node with <code>sudo -u pyxie-hostmaint sudo /usr/local/sbin/pyxie-maint version</code>, and pin the node&apos;s SSH host key in PyXie (Credentials): probe, compare the fingerprint with <code>ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub</code> on the node, and only then confirm. Running the same thing again later upgrades the node.</p>
      </div></details>

      <details className="border border-border rounded-lg p-3 mb-2"><summary className="cursor-pointer text-sm font-medium text-text">What the PyXieAdmin role allows, and why</summary><div className="mt-2">
        <p className="text-xs text-muted mb-2">Deliberately narrower than Proxmox&apos;s built-in PVEAdmin: only what PyXie&apos;s write paths call.</p>
        <table className="w-full text-sm">
          <tbody>
            {PRIVS.map(([p, why]) => (
              <tr key={p} className="border-t border-border align-top">
                <td className="py-1.5 pr-3 font-mono text-xs whitespace-nowrap text-text">{p}</td>
                <td className="py-1.5 text-muted text-xs">{why}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div></details>

      <details className="border border-border rounded-lg p-3 mb-2"><summary className="cursor-pointer text-sm font-medium text-text">Doing it by hand in the Proxmox web interface</summary><div className="mt-2">
        <details>
          <summary className="text-sm cursor-pointer text-accent">Show the clicks</summary>
          <ol className="list-decimal pl-5 text-sm text-text space-y-1.5 mt-2">
            <li>Datacenter &gt; Permissions &gt; Users &gt; Add: <code>pyxie-ro</code> and <code>pyxie-admin</code>, realm Proxmox VE authentication server (pve).</li>
            <li>Datacenter &gt; Permissions &gt; Roles &gt; Add: name <code>PyXieAdmin</code>, tick the privileges in the table above.</li>
            <li>Datacenter &gt; Permissions &gt; API Tokens &gt; Add: user <code>pyxie-ro@pve</code>, token ID <code>inventory</code>; user <code>pyxie-admin@pve</code>, token ID <code>maintenance</code>. Keep Privilege Separation ticked, Expire never. Copy each secret now.</li>
            <li>Datacenter &gt; Permissions &gt; Add &gt; User Permission, path <code>/</code>: <code>pyxie-ro@pve</code> with PVEAuditor; <code>pyxie-admin@pve</code> with PyXieAdmin.</li>
            <li>Datacenter &gt; Permissions &gt; Add &gt; API Token Permission, path <code>/</code>: <code>pyxie-ro@pve!inventory</code> with PVEAuditor; <code>pyxie-admin@pve!maintenance</code> with PyXieAdmin. (With privilege separation a token needs its own grant as well as its user.)</li>
          </ol>
        </details>
      </div></details>

      <details className="border border-border rounded-lg p-3 mb-2"><summary className="cursor-pointer text-sm font-medium text-text">Good to know</summary><div className="mt-2">
        <ul className="list-disc pl-5 text-sm text-text space-y-1.5">
          <li>PyXie never sees a Proxmox password. It only holds the token secrets, stored encrypted.</li>
          <li>Two independent gates guard every change: the admin credential and the Settings write switch. Both must allow it, plus your approval on the action.</li>
          <li>Rotate a secret: regenerate it on the token in Proxmox, then paste the new secret in Credentials &gt; Edit.</li>
          <li>Accounts created earlier with other names (for example pyxie-manager@pve and a role called PyXieMaintenanceW1) keep working: those are only labels in Proxmox.</li>
          <li>Run the checks at any time: Setup guide, last step: Check everything.</li>
        </ul>
      </div></details>
    </div>
  );
}
