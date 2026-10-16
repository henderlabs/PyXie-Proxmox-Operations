# Managing guests from PyXie

PyXie can create, change, copy and delete VMs and containers, and manage the ISOs and storage they use, without leaving the app. Every one of these goes through the same
Safety Contract as the rest of PyXie: **preview** (checked against the real cluster) -> **approve** -> **revalidate** -> **write** -> **read back from Proxmox** -> **audit**.
Nothing is changed until you press the confirm button on the preview, and each change is checked again just before it runs. It all needs the writes switch on and the
`maintenance` credential, and the Proxmox privileges listed at the end.

Every preview names what is wrong and how to fix it: a missing Proxmox privilege shows the exact `pveum` command, a bridge a host lacks shows which host, and so on.

## Create

**Workloads > + New VM** is a five-step wizard (Basics, System, Disk and media, Network, Review): one disk, one network device, an optional ISO, UEFI and a TPM when needed.
Anything more (extra disks, extra NICs, PCI devices) is added afterwards with the editors below or in Proxmox. Hosts, storages, ISOs, bridges, CPU and machine types are read
live from Proxmox. A host is suggested (online, not in maintenance, allowed by hard tag pins, most free memory). Starting values come from your
[defaults and templates](#defaults-and-templates), and every value shows which layer it came from.

Every choice is a real dropdown with a plain-language name and a one-line explanation of the selected option (for example *Linux (kernel 2.6 or newer)* rather than `l26`), memory is in **GB**, and
a choice someone has set as a default is starred (**★ ... (default)**) and listed first. On the Review step, **Make these choices the default** saves the System, Disk and Network choices as the default for
the cluster, the operating system, a tag you gave the VM, or its pool. **Back up this VM** adds the new VM to one of your Proxmox Backup Server backup jobs once it exists (a second preview and approval).

**Workloads > + New container** creates an **unprivileged** container from an installed template (privileged containers can only be made by `root@pam`, which PyXie does not use).
No password is taken (a secret does not belong in an operation record): add an SSH public key, or open a shell from Proxmox. Templates: **Storage > ISO library > Container
templates** downloads Proxmox's official ones.

## Change a VM

On a workload's page:

| Where | What | Notes |
|---|---|---|
| **Hardware > Disks > Grow** | grow a disk | Disks only grow. Online for VirtIO/SCSI. Extend the partition inside the guest afterwards. Works for container root disks and mount points too. |
| **Hardware > Edit machine & CPU type** | machine type, CPU type | Each choice is checked against **every host**: the real CPU flags and the types its QEMU offers. A type the VM's own host cannot run is blocked; a type another host lacks is a warning (the VM could not move there). `host` warns when the cluster mixes CPU models. Optional **safety snapshot first**, and an optional power cycle to apply it now; otherwise Proxmox keeps it pending until the next shutdown and start. |
| **Hardware > Network** | add, edit, remove a NIC | Model, bridge, VLAN, firewall, MAC, link down. Bridge and VLAN checked on every host; a duplicate MAC is refused; settings PyXie does not manage (rate, MTU, queues) are kept. |
| **Hardware > Disks** | add a disk, change a disk's options (cache, discard, SSD, IO thread, include in backups, read only), detach, attach, move to another VM, delete an unused disk, remove a CD/DVD drive | Detach keeps the data as an *unused* disk; move re-labels the volume to another VM on the same host (no copy); deleting an unused disk needs its name typed and cannot be undone. Detaching a disk and moving an attached one need the VM stopped; an unused disk can be moved or deleted while it runs; adding SCSI/VirtIO/SATA disks works while it runs. PyXie never takes disks from the VM it runs on. |
| **Hardware > Disks > CD/DVD** | insert, change, eject an ISO, add a drive | Optionally boot from it at the next start. |
| **Summary > Tags > Edit** | tags and their colors | See below. |
| **Snapshots tab** | take, roll back, delete | Optional memory. Old snapshots are flagged (7 and 30 days) and counted on the Dashboard. PyXie asks Proxmox first whether the VM can be snapshotted. |
| **Clone...** | full copy, or linked clone of a template | Any online host, new ID and name, target storage, pool, tags. A container must be stopped. |
| **Delete...** | destroy a stopped guest | See below. |

Containers: **Edit resources** (cores, memory, swap, applied live) and the Network card work the same way; **Hardware > Disks** adds a **mount point** (a new volume mounted at a folder you choose), detaches one (the data stays as an unused volume), attaches an unused volume at a folder, or deletes an unused volume (name typed, cannot be undone). The root disk is never detached (grow it instead), and the container must be stopped for mount point changes. start/stop/restart, snapshots, clone, delete and tags work for both kinds.

### Tags and their colors
Tags are saved to Proxmox (so Proxmox shows them too) and keep their spelling (`AI` stays `AI`). Tag **colors are Proxmox's own** (Datacenter > Options > Tag Style): PyXie reads them
live and, when you pick a color, writes it back after a preview, so both always agree. A tag with no color gets the automatic color Proxmox shows. Tick several rows on the Workloads
table and use **Tags...** to add or remove tags on all of them (each guest is previewed and verified separately).

### Delete
The most guarded action: the guest must be **stopped** (checked against the guest's real-time state, not an inventory that can lag), Proxmox **protection** must be off, it must not be
**HA-managed** or the VM PyXie itself runs on, and you must **type its exact name**. It is destroyed with its disks; with the default option it is also removed from backup jobs,
replication and HA configuration. Backups already taken stay in the backup storage. It cannot be undone.

## Defaults and templates
**Workloads > Defaults & templates** sets what PyXie suggests, per **cluster** (or all clusters), per **operating system** (for example every Windows 11 VM), per **tag**, per **Proxmox pool**, or as a named **template** you pick by hand.
Layers combine field by field, lowest to highest: built-in (Proxmox's wizard values) -> all clusters -> cluster -> operating system -> tags (higher priority wins between tags) -> pool -> template.
Every setting has a plain-language explanation next to it (what it does, when to change it), so the page doubles as a reference.
A layer sets only the fields you tick. They prefill the wizards and the hardware dialogs, and the Hardware tab says when a VM differs from defaults you set.

## Storage
**Storage** in the left menu has three pages.
- **Datastores**: every datastore Proxmox defines, what it holds (VM disks, ISO images, backups ...), where it lives (server and export, or path), which host it belongs to, its status and usage. Datastores that hold ISO images are marked.
- **ISO library**: choose an ISO datastore (or all of them) and see its ISOs, the VMs that have each attached, and delete one. Underneath, two cards side by side: **Add by URL** (the Proxmox host downloads it straight into the datastore; give the publisher's checksum and Proxmox verifies it) and **Upload from this computer** (checked first, then streamed through PyXie to the host; nothing is kept on PyXie). Delete is refused while any VM has the ISO attached (checked live). Proxmox has no ISO rename: add it again under the new name and delete the old one. Container templates are managed at the bottom of the page.
- **Storage setup**: add a datastore or remove a definition. Types: **NFS** (the export is scanned through a host first), **iSCSI** (portal and target, no CHAP login), **Directory**, **LVM** (an existing volume group; this is also how a **Fibre Channel** or shared iSCSI LUN is used: present the LUN to every host, create the volume group once on a host, add it here and tick Shared), **LVM-thin** and **ZFS**. Every type is checked against what the host really sees (a volume group, thin pool or ZFS pool that is not there is refused). CIFS/SMB and Proxmox Backup Server datastores need a password, which PyXie does not keep: add those in Proxmox. Removing a definition never deletes the data; it is refused while any guest has a disk, mount point or ISO on it, and you type its name.

## Pools

**Workloads > Pools** manages Proxmox resource pools: create a pool, add guests to it or remove them, and delete an empty pool (you type its name to confirm). A guest can be in one pool only, so adding one that is already in another pool is refused. Each change is previewed, approved, run and read back from Proxmox like every other change, and only membership changes: no disk, power state or setting of a guest. A pool can also carry defaults (see above), and the create-VM wizard and Clone can place the new guest in one. Needs the `Pool.Allocate` and `Pool.Audit` privileges (see below).

## Networks in use

**Infrastructure > Network** has a **Networks in use** card: every guest network interface grouped by bridge and VLAN, with the hosts that could not carry each network (bridge missing, bridge not VLAN-aware, VLAN not allowed) and any guest already running somewhere that cannot carry its VLAN. It is read-only.

## Notifications

Notification rules can now include **Operation failed or stopped** (a change that failed, or one you approved that was then blocked) and **Old snapshots** (a VM whose oldest snapshot is over 7 days, warning, or 30 days, critical). Both appear on Settings > Notifications like the other categories.

## Proxmox privileges
The `PyXieAdmin` role needs, beyond the privileges for migration, power and patching:

`VM.Allocate` (create and delete), `VM.Backup` (putting VMs into backup jobs), `VM.Clone`, `VM.Snapshot`, `VM.Snapshot.Rollback`, `VM.Config.Options` (tags), `VM.Config.HWType` (machine/CPU type), `VM.Config.Network`,
`VM.Config.CDROM`, `SDN.Use` (attach to a bridge), `Datastore.AllocateTemplate` (ISOs and container templates). Pools need `Pool.Allocate` (create, delete, change members; adding a guest also needs `VM.Allocate` on it) and `Pool.Audit` (Proxmox hides a pool from an account without it, so PyXie could not read the pool back).
`VM.Allocate` also allows deleting guests: PyXie only does so through the guarded Delete above.

The script builder (Platform > Setup guide) generates the full list; an existing install adds the new ones on a node with
`pveum role modify PyXieAdmin --privs <list> --append 1`. PyXie checks the privilege before offering each action and shows the missing one in the preview.
