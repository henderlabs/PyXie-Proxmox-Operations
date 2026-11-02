"""Every operation type the code creates must be seeded by a migration, or the first preview fails with "unknown operation_type_id"."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _created_types() -> set[str]:
    out = set()
    for f in (ROOT / "shared" / "pyxie_core").glob("*.py"):
        out |= set(re.findall(r'create_operation\(\s*db,\s*"([a-z_]+\.[a-z_]+)"', f.read_text()))
    return out


def _seeded_text() -> str:
    return "\n".join(f.read_text() for f in (ROOT / "api" / "migrations" / "versions").glob("*.py"))


def test_every_created_operation_type_is_seeded_by_a_migration():
    seeded = _seeded_text()
    missing = sorted(t for t in _created_types() if f'"{t}"' not in seeded and f"'{t}'" not in seeded)
    assert not missing, f"operation types created in code but never seeded: {missing}"
