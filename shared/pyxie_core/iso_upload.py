"""Upload an ISO from the person's computer into a Proxmox storage (streamed through PyXie, never kept on PyXie's disk or held in memory).

Not an approval-flow operation (the bytes arrive in one long request, not a worker job), so it has its own checks first -- `preflight_upload` -- and is written to
the audit log when it starts and when it ends. Proxmox's upload API takes a multipart form: `content=iso`, optional `checksum` / `checksum-algorithm`, then the
file as the form field `filename`; the file part must come last. Proxmox answers with a task that moves the temporary file into the storage.
"""

import uuid

from .discovery import build_pve_client
from .iso_workflow import ALGORITHMS, FILENAME_RE, STORAGE_RE, check_space
from .models import Cluster, Node, PveTarget
from .pve_permissions import missing_privileges, permission_reason
from .pve_write_client import _mutations_enabled

MAX_BYTES = 64 << 30  # one upload may not exceed 64 GiB
GIB = 1 << 30


def build_multipart(filename: str, size: int, *, checksum: str | None = None, algorithm: str | None = None) -> tuple[str, bytes, bytes, int]:
    """(content-type header, bytes before the file, bytes after the file, total body length) for Proxmox's upload form. Pure."""
    b = "pyxie" + uuid.uuid4().hex
    pre = f'--{b}\r\nContent-Disposition: form-data; name="content"\r\n\r\niso\r\n'
    if checksum:
        pre += f'--{b}\r\nContent-Disposition: form-data; name="checksum-algorithm"\r\n\r\n{(algorithm or "sha256").lower()}\r\n'
        pre += f'--{b}\r\nContent-Disposition: form-data; name="checksum"\r\n\r\n{checksum}\r\n'
    pre += f'--{b}\r\nContent-Disposition: form-data; name="filename"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'
    post = f"\r\n--{b}--\r\n"
    pb, qb = pre.encode(), post.encode()
    return f"multipart/form-data; boundary={b}", pb, qb, len(pb) + size + len(qb)


def preflight_upload(db, cluster: Cluster, *, node: str, storage: str, filename: str, size: int, checksum: str | None = None, algorithm: str | None = None) -> tuple[list[str], bool]:
    """(reasons, ok): everything that can be checked before a single byte is sent."""
    reasons: list[str] = []
    ok = True
    if not FILENAME_RE.match(filename or ""):
        reasons.append("the file name must end in .iso and use letters, digits and - _ . + ( )")
        ok = False
    if not STORAGE_RE.match(storage or "") or not node:
        reasons.append("choose a storage and a host")
        ok = False
    if not isinstance(size, int) or size <= 0:
        reasons.append("the file is empty or its size is unknown")
        ok = False
    elif size > MAX_BYTES:
        reasons.append(f"the file is {size / GIB:.0f} GiB; the limit for one upload is {MAX_BYTES // GIB} GiB")
        ok = False
    if checksum:
        a = (algorithm or "sha256").lower()
        if a not in ALGORITHMS or len(checksum) != ALGORITHMS[a] or any(c not in "0123456789abcdefABCDEF" for c in checksum):
            reasons.append(f"a {a} checksum is {ALGORITHMS.get(a, '?')} hex digits")
            ok = False
    if not _mutations_enabled():
        reasons.append("Proxmox writes are switched off in PyXie's settings")
        ok = False
    if not ok:
        return reasons, False
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    n = db.query(Node).filter(Node.cluster_id == cluster.id, Node.name == node, Node.is_missing.is_(False)).first()
    if n is None or n.status != "online":
        return reasons + [f"{node} is not an online host of this cluster"], False
    try:
        client, _cred = build_pve_client(db, target)
        with client:
            row = next((r for r in client.storage_list(node) or [] if r.get("storage") == storage), None)
            if row is None or not row.get("active", 1):
                reasons.append(f"storage '{storage}' is not active on {node}")
                ok = False
            elif "iso" not in str(row.get("content") or ""):
                reasons.append(f"storage '{storage}' is not set up to hold ISO images: switch ISO images on for it first")
                ok = False
            else:
                for p in check_space(row, size):
                    reasons.append(p)
                    ok = False
                if any(c.get("volid") == f"{storage}:iso/{filename}" for c in client.storage_content(node, storage, "iso") or []):
                    reasons.append(f"{storage} already has {filename}: delete it first or rename the file")
                    ok = False
        lacking = missing_privileges(db, target, 0, ["Datastore.AllocateTemplate"], path=f"/storage/{storage}")
        if lacking:
            reasons.append(permission_reason(lacking, f"add ISO images to {storage}"))
            ok = False
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"could not check the storage: {str(exc)[:160]}")
        ok = False
    if ok:
        reasons.insert(0, f"uploads {filename} ({size / GIB:.2f} GiB) from this computer into {storage} through PyXie to {node}; nothing is kept on PyXie")
    return reasons, ok
