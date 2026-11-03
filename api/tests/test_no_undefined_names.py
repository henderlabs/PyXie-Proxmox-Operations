"""A name used but never imported only fails when that path runs (a cancel, a rare error), and the unit tests do not reach every path:
this scans the shared code, the API and the worker for undefined names so such a slip is caught before the lab."""
import pathlib

import pytest

pyflakes_api = pytest.importorskip("pyflakes.api")
from pyflakes.reporter import Reporter  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]


class _Collect:
    def __init__(self):
        self.msgs = []

    def write(self, s):
        self.msgs.append(s)

    def flush(self):
        pass


def test_no_undefined_names_in_python_code():
    out = _Collect()
    rep = Reporter(out, out)
    for sub in ("shared/pyxie_core", "api/app", "worker/worker"):
        for path in sorted((ROOT / sub).rglob("*.py")):
            pyflakes_api.check(path.read_text(), str(path), rep)
    bad = [m.strip() for m in out.msgs if "undefined name" in m]
    assert not bad, "\n".join(bad)
