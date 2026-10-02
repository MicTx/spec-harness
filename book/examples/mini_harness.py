#!/usr/bin/env python3
"""A single-writer teaching harness. Commands never execute Markdown as code."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

FILES = ("spec.md", "tasks.md", "checklist.md")
LIMIT = 1024 * 1024
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
ITEM = re.compile(r"^- \[([ x])\] (\S.*)$")
FIELD = re.compile(r"^  - (id|depends-on|boundary|verify|evidence): (\S.*)$")


class Invalid(ValueError):
    pass


def package(root: Path, slug: str) -> Path:
    if not SLUG.fullmatch(slug):
        raise Invalid("slug must be lowercase words separated by hyphens")
    base = root / ".mini-harness"
    path = base / slug
    if base.is_symlink() or path.is_symlink():
        raise Invalid("package paths must not be symlinks")
    return path


def documents(path: Path) -> dict[str, str]:
    result = {}
    for name in FILES:
        p = path / name
        if p.is_symlink() or not p.is_file():
            raise Invalid(f"missing regular file: {name}")
        if p.stat().st_size > LIMIT:
            raise Invalid(f"file exceeds 1 MiB: {name}")
        result[name] = p.read_text(encoding="utf-8")
    return result


def parse_tasks(text: str) -> list[dict]:
    tasks: list[dict] = []
    for line in text.splitlines():
        match = ITEM.fullmatch(line)
        if match:
            tasks.append({"done": match[1] == "x", "title": match[2]})
            continue
        field = FIELD.fullmatch(line)
        if field:
            if not tasks or field[1] in tasks[-1]:
                raise Invalid("orphan or duplicate task field")
            tasks[-1][field[1]] = field[2]
        elif line.startswith("  - ") or line.startswith("- ["):
            raise Invalid(f"malformed task line: {line}")
        elif line.strip() and not line.startswith("#"):
            raise Invalid("tasks.md accepts headings and task records only")
    if not tasks:
        raise Invalid("tasks.md has no tasks")
    ids = [t.get("id", "") for t in tasks]
    if len(set(ids)) != len(ids) or any(not SLUG.fullmatch(i) for i in ids):
        raise Invalid("task ids must be valid and unique")
    by_id = dict(zip(ids, tasks))
    for t in tasks:
        if not t.get("boundary") or not t.get("verify"):
            raise Invalid(f"{t['id']}: boundary and verify required")
        t["deps"] = [s.strip() for s in t.get("depends-on", "").split(",") if s.strip()]
        if len(set(t["deps"])) != len(t["deps"]) or any(d not in by_id for d in t["deps"]):
            raise Invalid(f"{t['id']}: duplicate or missing dependency")
    # Iterative topological validation avoids recursion limits on long chains.
    resolved: set[str] = set()
    while len(resolved) < len(tasks):
        batch = {t["id"] for t in tasks if t["id"] not in resolved and set(t["deps"]) <= resolved}
        if not batch:
            raise Invalid("task dependency cycle")
        resolved.update(batch)
    for t in tasks:
        if t["done"] and any(not by_id[d]["done"] for d in t["deps"]):
            raise Invalid(f"{t['id']}: completed before its dependency")
        if t["done"] and not t.get("evidence"):
            raise Invalid(f"{t['id']}: completed without evidence")
    return tasks


def single_field(text: str, name: str) -> str:
    values = re.findall(rf"^{re.escape(name)}: (.+)$", text, re.M)
    if len(values) != 1:
        raise Invalid(f"exactly one {name} field required")
    return values[0].strip()


def state(docs: dict[str, str]) -> tuple[str, list[dict]]:
    status = single_field(docs["spec.md"], "Status")
    if status not in {"draft", "active"}:
        raise Invalid("Status must be draft or active")
    tasks = parse_tasks(docs["tasks.md"])
    checklist = docs["checklist.md"]
    acceptance = single_field(checklist, "Acceptance")
    if acceptance not in {"pending", "passed"}:
        raise Invalid("Acceptance must be pending or passed")
    checks = []
    for line in checklist.splitlines():
        match = ITEM.fullmatch(line)
        if match:
            checks.append(match[1] == "x")
        elif line.startswith("- ["):
            raise Invalid("malformed checklist item")
    if not checks:
        raise Invalid("checklist must contain checks")
    complete = all(t["done"] for t in tasks)
    if acceptance == "passed" and (status != "active" or not complete or not all(checks)):
        raise Invalid("acceptance contradicts task or checklist state")
    if status == "draft":
        return "plan", tasks
    if not complete:
        return "execute", tasks
    if acceptance != "passed" or not all(checks):
        return "review", tasks
    if not single_field(checklist, "Evidence"):
        raise Invalid("acceptance evidence is empty")
    return "ready-to-archive", tasks


def hashes(docs: dict[str, str]) -> dict[str, str]:
    return {n: hashlib.sha256(t.encode()).hexdigest() for n, t in docs.items()}


def init(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()  # Never overwrite an existing package.
    content = {
        "spec.md": (
            "# Photo migration\n\n"
            "Status: draft\n\n"
            "## Goal\n"
            "Move storage behind an adapter, preserving API behavior.\n\n"
            "## Non-goals\n"
            "No cache refactor or production data deletion.\n"
        ),
        "tasks.md": (
            "# Tasks\n\n"
            "- [ ] Implement storage adapter\n"
            "  - id: adapter\n"
            "  - boundary: app/storage.py, tests/test_storage.py\n"
            "  - verify: python3 -m unittest discover -s tests\n"
        ),
        "checklist.md": (
            "# Acceptance\n\n"
            "- [ ] Tests cover failure and retry\n"
            "- [ ] Diff stays inside approved boundary\n\n"
            "Acceptance: pending\n"
        ),
    }
    for name, text in content.items():
        (path / name).write_text(text, encoding="utf-8")
    print(f"created {path}; edit draft before execution")


def verify(path: Path, root: Path, command: list[str], timeout: int) -> int:
    if not command:
        raise Invalid("verify requires an operator-approved argv after --")
    before = documents(path)
    if state(before)[0] != "ready-to-archive":
        raise Invalid("finish structural check before final verification")
    # Output goes to temporary files, not an unbounded in-memory pipe.
    # This demo has no disk quota; run noisy tools in a quota-limited workspace.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(command, cwd=root, stdout=out, stderr=err, start_new_session=(os.name == "posix"))
        timed_out = False
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait()
            code = 124
        except KeyboardInterrupt:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait()
            raise
        summaries = {}
        for name, stream in (("stdout", out), ("stderr", err)):
            size = stream.tell()
            stream.seek(0)
            summaries[name] = {
                "bytes": size,
                "truncated": size > 65536,
                "text": stream.read(65536).decode("utf-8", errors="replace"),
            }
    unchanged = hashes(before) == hashes(documents(path))
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "argv": command,
        "cwd": str(root),
        "returncode": code,
        "timeout": timed_out,
        "documents": hashes(before),
        "documentsUnchanged": unchanged,
        **summaries,
    }
    # Atomic replace prevents a torn evidence file, but is not a multi-writer lock.
    fd, temporary = tempfile.mkstemp(dir=path, prefix=".evidence-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path / "evidence.json")
    finally:
        Path(temporary).unlink(missing_ok=True)
    print(f"verify returncode={code} documentsUnchanged={unchanged}; {path / 'evidence.json'}")
    return 0 if code == 0 and unchanged else 1


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    command: list[str] = []
    if "--" in raw:
        index = raw.index("--")
        raw, command = raw[:index], raw[index + 1 :]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "route", "check", "verify"))
    parser.add_argument("slug")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args(raw)
    if not 1 <= args.timeout <= 3600:
        parser.error("timeout must be 1..3600 seconds")
    if args.action != "verify" and command:
        parser.error("only verify accepts a command")
    try:
        root = Path.cwd().resolve()
        path = package(root, args.slug)
        if args.action == "init":
            init(path)
            return 0
        if args.action == "verify":
            return verify(path, root, command, args.timeout)
        stage, tasks = state(documents(path))
        print(stage)
        if args.action == "check":
            return 0 if stage == "ready-to-archive" else 1
        if stage == "execute":
            done = {t["id"] for t in tasks if t["done"]}
            for t in tasks:
                if not t["done"] and set(t["deps"]) <= done:
                    print(
                        f"{t['id']}: {t['title']}\n  boundary: {t['boundary']}\n  verify (not executed): {t['verify']}"
                    )
        return 0
    except KeyboardInterrupt:
        print("cancelled; no successful verification recorded", file=sys.stderr)
        return 130
    except (OSError, UnicodeError, Invalid) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
