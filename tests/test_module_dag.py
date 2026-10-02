import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EDGE_PATTERN = re.compile(r"^\s*([A-Za-z0-9_]+)\s*-->\s*([A-Za-z0-9_]+)\b")


def _module_dag_edges() -> list[tuple[str, str]]:
    content = (ROOT / ".spec" / "architecture" / "module-dag.mmd").read_text(encoding="utf-8")
    edges: list[tuple[str, str]] = []
    for line in content.splitlines():
        match = EDGE_PATTERN.match(line)
        if match:
            edges.append((match.group(1), match.group(2)))
    return edges


def test_module_dag_has_edges():
    assert _module_dag_edges()


def test_module_dag_is_acyclic():
    graph: dict[str, list[str]] = {}
    for source, target in _module_dag_edges():
        graph.setdefault(source, []).append(target)
        graph.setdefault(target, [])

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, path: tuple[str, ...]) -> None:
        if node in visiting:
            cycle = " -> ".join((*path, node))
            raise AssertionError(f"cycle detected in module DAG: {cycle}")
        if node in visited:
            return
        visiting.add(node)
        for target in graph[node]:
            visit(target, (*path, node))
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node, ())
