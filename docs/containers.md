# Containers in maintenance and migration

Proxmox runs two kinds of guest: virtual machines (VMs) and containers (CTs, LXC). A VM can move to another host while it keeps running. A container cannot. PyXie shows the difference everywhere it matters.

## How a container moves

Proxmox calls it a **restart migration**: the container is stopped, moved, and started again on the new host. The outage is about as long as a restart when its disk is on shared storage, and longer when the disk has to be copied.

- **Evacuate / Maint Mode, Full Maintenance and Evacuate** list every running container next to the VMs, marked **CT**. Each one has two choices: **Restart migrate (short outage)** (the default) or **Shut down, restart after** (it stays on this host, powered off, and starts again when maintenance ends).
- A container cannot be left running: the host has to be empty to enter maintenance mode, so there is no "leave it" option.
- A container that has a bind mount to a folder on the host cannot be moved by Proxmox. It is listed as blocked with that reason; shut it down in place instead, or remove the bind mount.
- A container disk can only go on a storage that holds container disks (Proxmox: "Disk image / Container" content). PyXie only offers such storages, and the preview refuses any other with a plain reason. Not every shared storage qualifies: an NFS share set up for backups and ISO files only does not.
- Containers are not moved by Balance Load or automatic balancing, and the Bulk Migrate action on the Maintenance page is for VMs (it is greyed out when a container is ticked). Use Evacuate / Maint Mode for containers.

The Maintenance page's **Workloads** panel lists containers too ("25 guests (24 VMs, 1 CT)"), with start and stop actions that work for both.

## Start at boot during maintenance

A stopped guest with **Start at boot** on is powered on by Proxmox when its host reboots, even while PyXie has the host in maintenance mode. Evacuate / Maint Mode and Full Maintenance now offer a per-guest choice:

- **Leave stopped (recommended)**: PyXie turns Start at boot off before anything moves, and **Exit Maintenance Mode puts it back on**. This is the default when the host stays in maintenance mode (or is left empty). It needs the PyXie admin token to have `VM.Config.Options` in Proxmox; without it the choice is off and the row says why.
- **Leave as is**: the guest starts after the reboot. This is the default for a plain patch-and-return run, where the host goes back into service.
- **Move it** (Relocate): moves the stopped guest to another host.

After a Reboot Node or the reboot step of Full Maintenance, PyXie waits for Proxmox to finish its start-at-boot step and lists any guest that **started by itself**, in the task log and on the completed task. If a guest is running on a host that is in maintenance mode, Health shows a warning that closes by itself when the guest stops, moves, or the host exits maintenance mode.
