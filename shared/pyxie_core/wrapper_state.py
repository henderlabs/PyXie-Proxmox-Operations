"""One plain-language state for a host's maintenance wrapper, from the readiness check. Pure.

states: no_credential (the PyXie-side key pair does not exist yet), not_set_up (no trusted connection yet: the wrapper may or may not be installed,
PyXie cannot tell until the host's key is confirmed), not_answering (set up before, not answering now), outdated, ready.
"""


def wrapper_state(status: dict) -> dict:
    if not status.get("credential_configured"):
        return {"state": "no_credential", "headline": "The host key pair has not been created yet.",
                "next": "Create the key pair once (Setup guide, Connect each host), then come back here."}
    if not status.get("management_ip_known") or not status.get("host_key_pinned") or not status.get("provisioned"):
        return {"state": "not_set_up", "headline": "No trusted connection to the maintenance wrapper yet.",
                "next": "Add the wrapper: PyXie cannot patch or reboot this host, or clear stale locks, until it is set up and its fingerprint confirmed."}
    if not status.get("reachable"):
        return {"state": "not_answering", "headline": "The wrapper is set up but is not answering.", "next": status.get("error") or "Check the host is up and reachable from PyXie.", "error": status.get("error")}
    if status.get("wrapper_outdated"):
        return {"state": "outdated", "headline": f"Wrapper {status.get('wrapper_version')}; this PyXie ships {status.get('kit_wrapper_version')}.", "next": "Paste the one-line update in a root shell on this host (the Update the wrapper button shows it)."}
    return {"state": "ready", "headline": f"Wrapper {status.get('wrapper_version')}, host fingerprint trusted.", "next": ""}


def missing_wrapper_hint(node_name: str) -> str:
    """Appended to a blocked operation's reason when the wrapper connection failed."""
    return f"Add or repair the maintenance wrapper for {node_name} from its host page (Maintenance wrapper, Add the wrapper button), then try again."
