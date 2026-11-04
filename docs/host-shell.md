# Host shell

Open a terminal on a Proxmox host from PyXie, like the **Shell** tab in Proxmox. It is the **Shell** tab on a host page (Hosts & Clusters, click a host). It is **off by default**.

## What to know first

- **You sign in at the host's login prompt.** Proxmox only logs in for you when you are `root@pam`, so with PyXie's token the terminal starts at `st-pve101 login:`. Type a Linux account on the host (for example `root`) and its password. PyXie never holds that password and does not record what you type.
- Once you are in, it is a real shell: nothing you type has a preview, an approval or an undo.
- **Two things are needed: Admin and the host shell permission.** The permission is off for everyone, existing admins included. An admin grants it per person on Settings > Users (button **Allow host shell**). People without it do not see the Shell tab. Taking it away, or changing the person to Viewer, closes their open shells within a minute. Granting and removing it is logged (Logging, `auth.user.updated`).
- You type the host name to open it.
- PyXie records **who opened a shell on which host, when it closed and why** (Logging, category "shell"). It does **not** record what is typed or shown (so a typed password is never stored).
- A session closes by itself after the idle time (default 15 minutes, 5 to 60), after 4 hours, when you sign out, when the host leaves maintenance mode (if you chose that setting) or when an admin switches the feature off.
- At most 2 shells per person and 10 in total.

## Turn it on

1. **Create the shell token in Proxmox, from PyXie.** Settings > Setup guide > the Proxmox accounts builder: tick **Host shell token (Sys.Console only)**, keep or change the account name, press **Generate the account script**, and run it once as root on any node. It creates the account, a role holding only `Sys.Console`, and the `shell` token, and grants the right to both the user and the token on every host (Proxmox needs both). It is safe to run again.
2. **Save the token in PyXie.** Settings > Credentials > **+ Add Credential Purpose** > `shell`, paste the secret from `/root/pyxie-tokens.txt`, then press **Test Connection**. PyXie asks Proxmox what the token may do on every host (no terminal is opened) and tells you which host is missing the right, if any. Then delete the secret file on the node.
3. **Switch it on:** Settings > **Allow the host shell**. Optionally tick "Only while the host is in maintenance mode" and set the idle time.
4. **Give yourself the permission:** Settings > Users > **Allow host shell** next to your name (and anyone else who should have it). Only admins can change the shell settings or grant the permission.

There is no fallback: without the shell token there is no shell, and the maintenance token is never used for it. Delete the one `shell` token and the feature stops everywhere.

## Take it away again

Switch it off in Settings (open shells close within a minute), and/or remove the grant:

```
pveum acl delete /nodes --users 'pyxie-shell@pve' --roles PyXieShell
pveum acl delete /nodes --tokens 'pyxie-shell@pve!shell' --roles PyXieShell
```

## How it works (short)

PyXie asks Proxmox for a terminal session with the token, keeps Proxmox's ticket on the server, and gives your browser a one-time 30 second ticket. The browser then connects to `/shell-ws/<ticket>` on PyXie's web proxy, which pipes the terminal to Proxmox. The ticket works once. The cross-site check, the admin check and the settings are re-checked on connect and every minute.

## Limits

- No session recording (off by design: typed passwords would be stored).
- No automatic sign-in. That needs Proxmox's root@pam ticket, which means storing the root password; a later version could use an SSH key held by PyXie instead.
- It reaches hosts through the Proxmox API, so a host Proxmox cannot reach through the API has no shell here.
- Containers and VMs: use the Console tab on the guest.
