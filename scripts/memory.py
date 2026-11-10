"""Hook-owned session snapshots and compact cross-package lessons."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from package import latest_runs, now, runs


def snapshot_write(package: Path, event: dict, brief: dict) -> None:
    task = brief.get("task") or {}
    task_id = task.get("id") if isinstance(task, dict) else ""
    attempts = len(
        [
            record
            for record in runs(package)
            if record.get("kind", "run") == "run" and task_id and record.get("task") == task_id
        ]
    )
    data = {
        **snapshot_read(package),
        "updated": now(),
        "task": task or None,
        "attempts": attempts,
        "last_failures": last_failures(package),
        "progress": brief.get("progress"),
        "pending": event.get("pending", []),
    }
    (package / "state.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def snapshot_read(package: Path) -> dict:
    try:
        value = json.loads((package / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def progress_tuple(package: Path, head: str) -> tuple[int, int, str]:
    records = runs(package)
    latest = latest_runs(package)
    done = len([record for record in latest.values() if record.get("exit") == 0])
    signatures = len({record["sig"] for record in records if record.get("exit") not in (None, 0) and record.get("sig")})
    return done, signatures, head


def last_failures(package: Path) -> list[str]:
    latest = latest_runs(package)
    return [
        f"{task}: {record.get('sig') or 'verify failed without a signature'}"
        for task, record in latest.items()
        if record.get("exit") not in (None, 0)
    ][-3:]


def lessons_add(root: Path, when: str, do: str) -> str:
    path = root / ".spec" / "lessons.md"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else ["# Spec Lessons"]
    used = [int(match.group(1)) for line in lines if (match := re.match(r"- L(\d+)", line))]
    lesson_id = f"L{max(used, default=0) + 1}"
    lines.append(f"- {lesson_id} | when: {when.strip()} | do: {do.strip()} | hits: 0")
    entries = lines[:1] + lines[1:][-49:]
    path.write_text("\n".join(entries) + "\n", encoding="utf-8")
    return lesson_id


def lessons_top(root: Path, paths: list[str] | None = None) -> list[str]:
    path = root / ".spec" / "lessons.md"
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("- L")]
    if paths:
        relevant = [line for line in lines if any(item and item in line for item in paths)]
        lines = relevant or lines
    return lines[-5:]


def lessons_adjust(root: Path, used: list[str], wrong: list[str]) -> None:
    path = root / ".spec" / "lessons.md"
    if not path.exists():
        return
    output = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"- (L\d+).*hits: (\d+)", line)
        if not match or match.group(1) in wrong:
            if match and match.group(1) in wrong:
                continue
            output.append(line)
            continue
        hits = int(match.group(2)) + (1 if match.group(1) in used else 0)
        output.append(re.sub(r"hits: \d+", f"hits: {hits}", line))
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def lesson_proposals(root: Path) -> list[str]:
    path = root / ".spec" / "lessons.md"
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.search(r"hits:\s*(\d+)", line)
        if match and int(match.group(1)) >= 3:
            result.append(line.removeprefix("- "))
    return result


def seen(package: Path, event: dict) -> bool:
    path = package / "state.json"
    state = snapshot_read(package)
    key = json.dumps(event, sort_keys=True, default=str)
    current = time.time()
    duplicate = state.get("seen_key") == key and current - float(state.get("seen_at", 0)) < 2
    if duplicate:
        return True
    state.update({"seen_key": key, "seen_at": current})
    path.write_text(json.dumps(state) + "\n", encoding="utf-8")
    return False
