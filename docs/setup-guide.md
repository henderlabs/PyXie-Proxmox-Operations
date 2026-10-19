# Setup guide

Platform > Setup guide (in the left menu; formerly "Integrations") opens with "Set up PyXie, step by step": eight numbered steps with a status from your real data
(Done, Do this next, To do, Waiting, Optional), a progress bar, and a button for each step that jumps to where you do it.

| # | Step | Counts as done when |
|---|---|---|
| 1 | Add a site | at least one site exists |
| 2 | Create the Proxmox accounts | a read-only (inventory) credential exists |
| 3 | Connect your cluster | every target's inventory credential is valid and nodes are discovered |
| 4 | Add the admin credential | the Maintenance (Admin) credential is saved and tested |
| 5 | Connect each host for patching | host key pair generated, and every node has its SSH host key pinned and a current wrapper |
| 6 | Turn features on | the "write to Proxmox" switch is on (console is shown alongside) |
| 7 | Notifications | email relay on, and at least one notification rule that is on has someone to email (its own addresses, or all admins) |
| 8 | Check everything | (button) |

Step 2 is done by running the script from the **Create the Proxmox accounts** section of **Settings > Credentials** on a Proxmox node; PyXie never
creates accounts itself, and a new account does not appear in PyXie on its own: after the script runs, add each token on
the Credentials page (**+ Add Credential Purpose**, purpose `inventory` / `maintenance` / `console`, token user and token
id exactly as the script created them, the secret from `/root/pyxie-tokens.txt`, then `shred -u` that file) and press
Test Connection. The builder opens with nothing ticked. The accounts card works before any cluster is connected; the
**Host wrapper** card (step 5) needs a connected target. You run the wrapper script on each node yourself; applying
updates later can reboot a node.

The "Turn features on" step also says whether the embedded console is off and whether a credential able to open one exists.
Nodes whose host wrapper is outdated raise a warning (category *Host wrapper*, e-mailable) and a Dashboard banner.

"Run check" (the last step) does live, read-only checks and lists problems in plain words: each token works, the console
permission (when the console is on), how many nodes can act as the cluster entry point if one is down (ingress failover),
each host's SSH wrapper (connected, version), the write and console switches, and email alerts.

API: `GET /api/setup/status` (database only, any signed-in user) and `POST /api/setup/check` (admin).

## Reference (bottom of the Setup guide)

The former Quick start page now lives at the bottom of the Setup guide as collapsed sections: the two Proxmox tokens side by side (read-only inventory and Maintenance (Admin)), the Proxmox Backup Server accounts, the names used, the three ways to put the host kit on a node, what each privilege of the `PyXieAdmin` role is for, the by-hand clicks in the Proxmox web interface, and the safety gates. `/platform/quick-start` redirects there. The Dashboard shows a "New here?" banner until a site, the accounts and a connected cluster exist.

## Backup server step (optional)

Step 6 connects a Proxmox Backup Server: create the `pyxie-pbs-ro@pbs` account (and optionally `pyxie-pbs-admin@pbs`), add the server, then connect it for patching exactly like a Proxmox node. See [adding-a-pbs.md](adding-a-pbs.md).
