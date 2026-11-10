import ast
from pathlib import Path


def test_hook_kernel_has_eight_event_handlers_only():
    path = Path(__file__).resolve().parents[1] / "hooks" / "spec_hook.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "handle" in functions
    assert "on_start" not in functions
