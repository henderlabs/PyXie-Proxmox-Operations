"""The dedicated host-shell token: what 'valid' means and how a failure is put in words.

Proxmox lets a token open the node terminal only with Sys.Console on the node, and a privilege-separated
token needs it on BOTH the token and its user. PyXie asks Proxmox what the token may do on each host
(GET /access/permissions?path=/nodes/<host>), so no terminal is opened to test it.
"""


def missing_console(perms_by_node: dict) -> list:
    """Hosts where the token does not hold Sys.Console. perms_by_node: {host: {privilege: 1, ...}}."""
    return sorted(n for n, p in perms_by_node.items() if not (p or {}).get("Sys.Console"))


def explain(missing: list, total: int) -> str:
    if total == 0:
        return "Proxmox listed no hosts for this cluster, so the shell permission could not be checked."
    names = ", ".join(missing[:6]) + (f" and {len(missing) - 6} more" if len(missing) > 6 else "")
    return (f"The token lacks Sys.Console on {names} ({len(missing)} of {total} hosts). Proxmox needs the right on both "
            "the token and its user: run the shell part of the account script again (Settings > Setup guide).")
