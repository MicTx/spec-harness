"""Startup-validity gate for GitHub Actions workflow files.

GitHub validates a workflow file before dispatching any job: an
expression that references a context outside its allowed fields fails
the whole run at startup with "No jobs were run" (observed 2026-10-05:
public runs 37254342812/37254344520 failed in 0s after a job-level
``env:`` block used ``${{ runner.temp }}``). This module encodes the
context-availability rules for the fields where violations are silent
until GitHub rejects the file, plus the minimum structure a workflow
needs to dispatch at all.

The check is deliberately dependency-free (the offline CI toolchain
ships only pytest and ruff) and line-based rather than a YAML parse:
it targets the repository's own 2-space block style and reports
nothing for constructs it does not model (block scalars under ``env``
or ``if``, tabs, anchors). Unknown leading functions in expressions
(``format(...)``, ``hashFiles(...)``) are ignored; only references to
known contexts are evaluated.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

KNOWN_CONTEXTS = (
    "github",
    "env",
    "vars",
    "secrets",
    "inputs",
    "needs",
    "strategy",
    "matrix",
    "job",
    "runner",
    "steps",
)

# Contexts GitHub allows in each checked field (availability table,
# with matrix/strategy tolerated in job-level ``if`` to avoid false
# positives on the broader documented behavior).
WORKFLOW_ENV_ALLOWED = frozenset({"github", "vars", "secrets", "inputs"})
JOB_ENV_ALLOWED = frozenset({"github", "needs", "strategy", "matrix", "vars", "secrets", "inputs"})
JOB_IF_ALLOWED = frozenset({"github", "needs", "vars", "inputs", "matrix", "strategy"})

_EXPRESSION_RE = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
_MAPPING_KEY_RE = re.compile(r"^(\s*)([A-Za-z_][\w-]*)\s*:\s*(.*)$")
_JOB_KEY_RE = re.compile(r"^  (\S+?):\s*(?:\S.*)?$")
_JOB_ENV_HEADER_RE = re.compile(r"^    env:\s*$")
_WORKFLOW_ENV_HEADER_RE = re.compile(r"^env:\s*$")
_JOB_IF_RE = re.compile(r"^    if:\s*(.*)$")
_JOB_RUNS_ON_RE = re.compile(r"^    runs-on:\s*\S")
_TOP_KEY_RE = re.compile(r"^(\S+):\s*(?:\S.*)?$")


def _strip_comment(value: str) -> str:
    return value.split("#", 1)[0]


def _contexts_in(expression: str) -> set[str]:
    used: set[str] = set()
    for name in KNOWN_CONTEXTS:
        if re.search(rf"\b{name}(?:\.|\[)", expression):
            used.add(name)
    return used


def _violations_for(value: str, allowed: frozenset[str], field: str) -> list[str]:
    problems: list[str] = []
    for match in _EXPRESSION_RE.finditer(value):
        used = _contexts_in(match.group(1))
        illegal = sorted(used - allowed)
        if illegal:
            problems.append(
                f"{field} 引用了该字段不允许的上下文: {', '.join(illegal)}（允许: {', '.join(sorted(allowed))}）"
            )
    return problems


def lint_workflow_text(name: str, text: str) -> list[str]:
    """Return a list of startup-validity problems for one workflow file."""
    errors: list[str] = []
    lines = text.splitlines()

    jobs_seen = False
    job_names: list[str] = []
    current_job: str | None = None
    current_job_has_runs_on = False
    env_state: tuple[int, frozenset[str], str] | None = None  # (entry indent, allowed, label)

    for lineno, raw in enumerate(lines, start=1):
        stripped = raw.rstrip()
        if not stripped.strip() or stripped.lstrip().startswith("#"):
            continue

        if env_state is not None:
            indent, allowed, label = env_state
            if len(raw) - len(raw.lstrip(" ")) > indent and raw.startswith(" " * (indent + 1)):
                mapping = _MAPPING_KEY_RE.match(raw)
                if mapping and mapping.group(2) != "env":
                    value = _strip_comment(mapping.group(3))
                    for problem in _violations_for(value, allowed, label):
                        errors.append(f"{name}:{lineno}: {problem}")
                continue
            env_state = None  # block ended

        if _WORKFLOW_ENV_HEADER_RE.match(raw):
            env_state = (0, WORKFLOW_ENV_ALLOWED, "workflow 级 env")
            continue
        if _JOB_ENV_HEADER_RE.match(raw):
            env_state = (4, JOB_ENV_ALLOWED, "job 级 env")
            continue

        job_if = _JOB_IF_RE.match(raw)
        if job_if:
            value = _strip_comment(job_if.group(1))
            for problem in _violations_for(value, JOB_IF_ALLOWED, "job 级 if"):
                errors.append(f"{name}:{lineno}: {problem}")
            continue

        top_key = _TOP_KEY_RE.match(raw)
        if top_key:
            if top_key.group(1) == "jobs":
                jobs_seen = True
            elif current_job is not None:
                if not current_job_has_runs_on:
                    errors.append(f"{name}:{lineno}: job「{current_job}」缺少 runs-on（无 job 可派发）")
                current_job = None
            continue

        job_key = _JOB_KEY_RE.match(raw)
        if job_key and jobs_seen:
            if current_job is not None and not current_job_has_runs_on:
                errors.append(f"{name}:{lineno}: job「{current_job}」缺少 runs-on（无 job 可派发）")
            current_job = job_key.group(1)
            current_job_has_runs_on = False
            job_names.append(current_job)
            continue

        if _JOB_RUNS_ON_RE.match(raw):
            current_job_has_runs_on = True

    if current_job is not None and not current_job_has_runs_on:
        errors.append(f"{name}: job「{current_job}」缺少 runs-on（无 job 可派发）")
    if not jobs_seen or not job_names:
        errors.append(f"{name}: 缺少 jobs 或 jobs 下没有任何 job（无 job 可派发）")
    return errors


def lint_workflow_file(path: Path) -> list[str]:
    return lint_workflow_text(path.as_posix(), path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", help="workflow 文件或目录（默认 .github/workflows）")
    args = parser.parse_args(argv)

    targets: list[Path] = []
    for item in args.paths or [Path(".github/workflows")]:
        path = Path(item)
        if path.is_dir():
            targets.extend(sorted(path.glob("*.yml")))
            targets.extend(sorted(path.glob("*.yaml")))
        else:
            targets.append(path)

    errors: list[str] = []
    for target in targets:
        if not target.is_file():
            errors.append(f"{target}: 文件不存在")
            continue
        errors.extend(lint_workflow_file(target))

    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print(f"workflow startup validity: {len(targets)} 个文件通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
