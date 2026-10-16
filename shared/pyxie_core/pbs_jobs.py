"""Pure helpers for Proxmox Backup Server sync, prune and verify jobs: validation, change diff, display.
No I/O here; pbs_job_workflow does the PBS calls."""

import re

JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$")
SCHEDULE_RE = re.compile(r"^[A-Za-z0-9 ,:*/.\-]{1,64}$")
STORE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")
KEEP = ("keep-last", "keep-hourly", "keep-daily", "keep-weekly", "keep-monthly", "keep-yearly")

# kind -> field -> type ("str" | "int" | "bool")
FIELDS = {
    "prune": {"store": "str", "schedule": "str", "comment": "str", "disable": "bool", **{k: "int" for k in KEEP}},
    "verify": {"store": "str", "schedule": "str", "comment": "str", "ignore-verified": "bool", "outdated-after": "int"},
    "sync": {"store": "str", "schedule": "str", "comment": "str", "remote": "str", "remote-store": "str",
             "remove-vanished": "bool", "owner": "str", "transfer-last": "int"},
}
REQUIRED = {"prune": ("store",), "verify": ("store",), "sync": ("store", "remote-store")}
# What a user can leave empty to clear (sent to PBS as "delete").
CLEARABLE = {"prune": ("schedule", "comment", *KEEP), "verify": ("schedule", "comment", "outdated-after"),
             "sync": ("schedule", "comment", "owner", "transfer-last", "remote")}
MAX_INT = 100000


def validate_job(kind: str, action: str, job_id: str, params: dict, stores: list[str], remotes: list[str]) -> tuple[dict, list[str]]:
    """Returns (clean params, errors). `params` holds only what the caller wants set; an empty string or None on a
    clearable field means clear it. For action create, required fields must be present."""
    errors: list[str] = []
    if kind not in FIELDS:
        return {}, [f"unknown job kind {kind!r}"]
    if action not in ("create", "update", "delete", "run"):
        return {}, [f"unknown action {action!r}"]
    if not JOB_ID_RE.match(job_id or ""):
        errors.append("job id must be 3-32 characters: letters, digits, dot, dash, underscore, starting with a letter or digit")
    if action in ("delete", "run"):
        return {}, errors
    clean: dict = {}
    spec = FIELDS[kind]
    for key, val in (params or {}).items():
        if key not in spec:
            errors.append(f"{key} is not a {kind} job setting PyXie manages")
            continue
        if val is None or val == "":
            if key in CLEARABLE[kind] and action == "update":
                clean[key] = None
            elif key in REQUIRED[kind] or action == "create":
                if key in REQUIRED[kind]:
                    errors.append(f"{key} is required")
            continue
        typ = spec[key]
        if typ == "int":
            try:
                n = int(val)
            except (TypeError, ValueError):
                errors.append(f"{key} must be a whole number")
                continue
            if n < 1 or n > MAX_INT:
                errors.append(f"{key} must be between 1 and {MAX_INT}")
                continue
            clean[key] = n
        elif typ == "bool":
            clean[key] = bool(val) if isinstance(val, bool) else str(val).lower() in ("1", "true", "yes", "on")
        else:
            v = str(val).strip()
            if key == "schedule" and not SCHEDULE_RE.match(v):
                errors.append("schedule has characters PBS calendar events never use")
                continue
            if key in ("store", "remote-store") and not STORE_RE.match(v):
                errors.append(f"{key} is not a valid datastore name")
                continue
            if len(v) > 200 or any(c in v for c in "\n\r\0"):
                errors.append(f"{key} is too long or has control characters")
                continue
            clean[key] = v
    if action == "create":
        for req in REQUIRED[kind]:
            if not clean.get(req):
                if f"{req} is required" not in errors:
                    errors.append(f"{req} is required")
    if clean.get("store") and stores and clean["store"] not in stores:
        errors.append(f"datastore {clean['store']!r} does not exist on this PBS")
    if kind == "sync" and clean.get("remote") and remotes and clean["remote"] not in remotes:
        errors.append(f"remote {clean['remote']!r} is not configured on this PBS")
    if kind == "prune" and action == "create" and not any(clean.get(k) for k in KEEP):
        errors.append("a prune job needs at least one keep-* setting, or it would keep everything")
    if kind == "prune" and action == "update":
        pass
    return clean, errors


def normalize(job: dict, kind: str) -> dict:
    """The settings of an existing job that PyXie compares and shows (drops ids, run state and unknown keys)."""
    spec = FIELDS[kind]
    out = {}
    for k in spec:
        if k in job and job[k] not in (None, ""):
            out[k] = job[k]
    return out


def diff(before: dict | None, clean: dict, kind: str) -> list[dict]:
    """[{field, before, after}] of what an update or create would change. None after = cleared."""
    before = before or {}
    out = []
    for k, v in clean.items():
        b = before.get(k)
        if v is None:
            if b is not None:
                out.append({"field": k, "before": b, "after": None})
        elif b != v:
            out.append({"field": k, "before": b, "after": v})
    return out


def split_update(clean: dict) -> tuple[dict, list[str]]:
    """(values to set, fields to delete) for PBS's PUT."""
    return {k: v for k, v in clean.items() if v is not None}, [k for k, v in clean.items() if v is None]


def describe(kind: str, job: dict) -> str:
    if kind == "prune":
        parts = [f"{k[5:]} {job[k]}" for k in KEEP if job.get(k)]
        return "keep " + ", ".join(parts) if parts else "keep all"
    if kind == "verify":
        return "re-verify after " + (f"{job['outdated-after']} days" if job.get("outdated-after") else "never") + (", skip verified" if job.get("ignore-verified") else "")
    return f"from {job.get('remote') or 'this server'}:{job.get('remote-store', '?')}" + (", remove vanished" if job.get("remove-vanished") else "")


def job_matches(kind: str, actual: dict, wanted: dict) -> list[str]:
    """Fields of `wanted` (cleaned, None = must be absent) that `actual` does not show; empty list = read-back OK."""
    bad = []
    for k, v in wanted.items():
        a = actual.get(k)
        if v is None:
            if a not in (None, "", False):
                bad.append(k)
        elif isinstance(v, bool):
            if bool(a) != v:
                bad.append(k)
        elif str(a) != str(v):
            bad.append(k)
    return bad
