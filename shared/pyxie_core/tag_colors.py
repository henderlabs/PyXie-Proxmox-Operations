"""Tag colors, kept in step with Proxmox.

Proxmox stores them datacenter-wide in the `tag-style` option (Datacenter > Options > Tag Style Override):
`color-map=tag:BGHEX[:FGHEX];tag2:...` plus `ordering` and `shape`. PyXie keeps no copy of its own: it reads that option live and
writes changes back to it, so a color set in either place is the color in both. A tag with no entry gets an automatic color
(Proxmox derives one from the tag's name; `auto_color` mirrors that so PyXie and Proxmox agree as closely as they can).
"""

import re

HEX_RE = re.compile(r"^[0-9a-fA-F]{6}$")


class TagColorError(Exception):
    pass


def norm_hex(value) -> str | None:
    """'#ff0000' / 'FF0000' -> 'FF0000'; None when not a six-digit hex color. Pure."""
    v = str(value or "").strip().lstrip("#")
    return v.upper() if HEX_RE.match(v) else None


def readable_fg(bg_hex: str) -> str:
    """Black or white text, whichever reads better on this background. Pure."""
    r, g, b = (int(bg_hex[i:i + 2], 16) for i in (0, 2, 4))
    return "000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "FFFFFF"


def auto_color(tag: str) -> str:
    """The background Proxmox's web UI shows for a tag with no override, checked against its UI: a 32-bit hash of the tag name + 'prox'
    (case matters: 'AI' and 'ai' differ), red = low byte, then blended 70/30 with white. Pure."""
    h = 0
    for ch in f"{tag}prox":
        h = (ord(ch) + (((h << 5) & 0xFFFFFFFF) - h)) & 0xFFFFFFFF
    r, g, b = h & 255, (h >> 8) & 255, (h >> 16) & 255
    blend = lambda x: round(0.7 * x + 0.3 * 255)  # noqa: E731
    return f"{blend(r):02X}{blend(g):02X}{blend(b):02X}"


def _entries(color_map) -> list[str]:
    if isinstance(color_map, (list, tuple)):
        return [str(e) for e in color_map]
    return [e for e in str(color_map or "").split(";") if e.strip()]


def parse_tag_style(raw) -> dict:
    """The `tag-style` option from /cluster/options (a property string, or already a dict) as
    {"colors": {tag: (bg, fg|None)}, "ordering", "shape", "case_sensitive", "extra": {unknown keys, kept so a write loses nothing}}. Pure."""
    parts: dict = {}
    if isinstance(raw, dict):
        parts = {str(k): v for k, v in raw.items()}
    elif raw:
        for item in str(raw).split(","):
            if "=" in item:
                k, _, v = item.partition("=")
                parts[k.strip()] = v.strip()
    colors: dict[str, tuple[str, str | None]] = {}
    for entry in _entries(parts.pop("color-map", "")):
        bits = entry.strip().split(":")
        bg = norm_hex(bits[1]) if len(bits) > 1 else None
        if bits[0].strip() and bg:
            colors[bits[0].strip()] = (bg, norm_hex(bits[2]) if len(bits) > 2 else None)
    case = str(parts.pop("case-sensitive", "0")).lower() in ("1", "true", "yes")
    return {"colors": colors, "ordering": parts.pop("ordering", None), "shape": parts.pop("shape", None), "case_sensitive": case, "extra": parts}


def build_tag_style(style: dict) -> str:
    """Back to the property string Proxmox accepts; '' when nothing is left to say (then the option is deleted). Pure."""
    out = []
    if style.get("case_sensitive"):
        out.append("case-sensitive=1")
    if style.get("colors"):
        out.append("color-map=" + ";".join(f"{t}:{bg}" + (f":{fg}" if fg else "") for t, (bg, fg) in sorted(style["colors"].items())))
    if style.get("ordering"):
        out.append(f"ordering={style['ordering']}")
    if style.get("shape"):
        out.append(f"shape={style['shape']}")
    out += [f"{k}={v}" for k, v in (style.get("extra") or {}).items()]
    return ",".join(out)


def _find(colors: dict, tag: str) -> str | None:
    """The key a tag is stored under. Proxmoxs web UI matches a color override by the tags exact spelling (ai does not color AI,
    verified against its UI), so PyXie does the same. Pure."""
    tag = tag.strip()
    return tag if tag in colors else None


def apply_changes(style: dict, changes: dict[str, str | None]) -> dict:
    """A new style with `changes` ({tag: hex, or None to go back to automatic}) applied, tags matched by exact spelling.
    Raises TagColorError on a bad color. Pure."""
    colors = dict(style["colors"])
    for tag, value in changes.items():
        tag = tag.strip()
        key = _find(colors, tag) or tag
        if value is None or value == "":
            colors.pop(key, None)
            continue
        bg = norm_hex(value)
        if not bg:
            raise TagColorError(f"'{value}' is not a color: use six hex digits such as 3366CC")
        colors[key] = (bg, readable_fg(bg))
    return {**style, "colors": colors}


def view(style: dict) -> dict:
    """What the UI needs: {tag: {"bg", "fg"}} for the explicit overrides, plus the shape/ordering. Pure."""
    return {"colors": {t: {"bg": bg, "fg": fg or readable_fg(bg)} for t, (bg, fg) in style["colors"].items()}, "ordering": style["ordering"], "shape": style["shape"]}
