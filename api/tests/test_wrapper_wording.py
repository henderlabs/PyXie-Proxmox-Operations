"""The wrapper update wording must not send people to a page name that no longer exists, and the in-place update panel must be wired in."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _sources():
    for base, exts in (("shared/pyxie_core", (".py",)), ("api/app", (".py",)), ("web/src", (".ts", ".tsx"))):
        for p in (ROOT / base).rglob("*"):
            if p.suffix in exts and "node_modules" not in p.parts and "tests" not in p.parts:
                yield p


def test_no_text_points_at_the_missing_integrations_page():
    bad = [str(p.relative_to(ROOT)) for p in _sources() if "Integrations > Host wrapper" in p.read_text(errors="ignore")]
    assert bad == []


def test_the_one_line_command_is_wired_into_the_setup_guide_and_health():
    sg = (ROOT / "web/src/components/SetupGuide.tsx").read_text()
    fl = (ROOT / "web/src/components/FindingList.tsx").read_text()
    assert "WrapperUpdatePanel" in sg and "wrapper_update" in sg
    assert "WrapperPanel" in fl
    hs = (ROOT / "web/src/lib/hostScripts.ts").read_text()
    assert "export function hostOneLiner" in hs and "sha256sum -c -" in hs  # the fingerprint check stays in the one line
