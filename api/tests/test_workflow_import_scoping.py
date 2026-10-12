"""A name imported inside a function becomes a local of the WHOLE function: used on a path that skips the import, it raises
UnboundLocalError mid-run ("cannot access local variable 'Storage'" failed a real maintenance run after it had shut guests down).
Workflow modules must import what they use at module level."""

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "shared" / "pyxie_core"
WORKFLOWS = [
    "maintenance_workflow", "evacuation_workflow", "node_maintenance_workflow", "balance_workflow", "lifecycle_workflow",
    "migration_workflow", "reboot_workflow", "resize_workflow", "host_update_workflow", "protection_workflow", "network_workflow",
]


def test_workflows_do_not_shadow_names_with_function_local_imports():
    offenders = []
    for name in WORKFLOWS:
        path = CORE / f"{name}.py"
        if not path.exists():
            continue
        tree = ast.parse(path.read_text())
        top = {a.asname or a.name for n in tree.body if isinstance(n, ast.ImportFrom) for a in n.names}
        for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            for n in ast.walk(fn):
                if isinstance(n, ast.ImportFrom):
                    for a in n.names:
                        local = a.asname or a.name
                        if local in top:
                            offenders.append(f"{name}.{fn.name}: imports {local} locally but it is also a module-level import")
    assert not offenders, "\n".join(offenders)
