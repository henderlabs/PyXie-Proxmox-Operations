# Add the maintenance wrapper to a host (first time, step by step)

PyXie can update, reboot and clear stuck locks on a Proxmox host only through a small, locked-down helper called the
**maintenance wrapper**. It has to be installed on each host once. **PyXie never installs it for you**: you paste one script
into that host's root shell. This page is the plain version; the technical reference is [host-kit.md](host-kit.md).

You need: the PyXie page open in one browser tab, and the Proxmox web page for the host in another.

## The quick way (from the host page)

1. In PyXie open **Hosts & Clusters**, click the host, and find the **Maintenance wrapper** box near the top. It says **Not set up**,
   **Not answering**, **Outdated** or **Ready**. If a PyXie action was blocked because the wrapper is missing, the same box appears
   inside the blocked card.
2. Click **Add the wrapper…** (**Update the wrapper…** or **Repair the wrapper…** for the other states).
   - If it says **Needs a key pair**, open **Settings > Credentials** first and create the host-maintenance key pair. This is once per cluster.
3. **Open a root shell on the host.** In the Proxmox web page click the host in the left tree, then **Shell** (top right). The
   prompt should end in `#`. (Over SSH: `ssh root@<host address>`.)
4. In PyXie press **Make the script**, then **Copy**, then paste it into the shell and press **Enter**.
   - It downloads the installer from your PyXie and **refuses to run if the file does not match** the checksum PyXie shows.
   - It adds one locked-down user, one SSH key and a short list of exact commands PyXie may run. It reboots nothing.
   - **Success** ends with `OK: <host> is provisioned for PyXie.` (on a host that already had it: `was already set up; the wrapper is now upgraded`).
5. Back in PyXie press **It finished: get the fingerprint**. PyXie shows the fingerprint the host presents.
6. In the same root shell run `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` and compare the two. **If they are the same**, press
   **They match: trust this host**. If they differ, do not trust it: something other than your host is answering at that address.
7. The box turns **Ready**. Patching, reboots and clearing stale locks are now available for the host.

An update of a host that is already trusted skips steps 5 and 6: PyXie notices the new version by itself.

## If something goes wrong

| You see | It means | Do this |
|---|---|---|
| `curl: (60) SSL certificate problem` | The host does not trust PyXie's certificate (normal with PyXie's own certificate) | Tick *My host does not trust PyXie's certificate* and copy the script again, or trust the PyXie root certificate on the host (see [docker-caddy.md](docker-caddy.md)) |
| `curl: (6) Could not resolve host` | The host cannot look up PyXie's name | Fix the host's DNS (the host's Config tab shows it) |
| `404`, or the link is gone | The 30-minute link ran out | Press *make a new one* |
| `FAILED` after `sha256sum` / checksum mismatch | The file changed on the way: the safety check worked | Do not run it; make a new script. If it repeats, stop and investigate |
| `Run as root` | You are not root in that shell | Run `sudo -i`, then paste again |
| `sudo is missing and apt cannot refresh` | No Proxmox subscription but the enterprise repository is enabled | Disable that repository as the script explains, then paste again |
| *could not reach* when you ask for the fingerprint | PyXie cannot open SSH to the host's address | Check the address PyXie has for the host and any firewall |

## Prefer to read it first?

Press **Download the installer** in the box (a single `pyxie-host-kit.sh`), read it, copy it to the host and run `bash pyxie-host-kit.sh`
as root. The classic `.tar.gz` kit is still on **Settings > Credentials**. Nothing here changes what the wrapper can do: seven fixed commands
behind a forced-command SSH key and an exact sudo allowlist.
