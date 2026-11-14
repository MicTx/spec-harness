#!/usr/bin/env python3
"""Single model-side CLI for Spec Harness v3."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import done  # noqa: E402
import evidence  # noqa: E402
import gate  # noqa: E402
import gitx  # noqa: E402
import memory  # noqa: E402
import package  # noqa: E402
import plan  # noqa: E402
import route  # noqa: E402
import ui_review  # noqa: E402
from path_safety import safe_child  # noqa: E402

STALE_EVIDENCE_SUFFIX = "evidence is stale for the current command or tree"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", default=".")
    result.add_argument("--slug")
    commands = result.add_subparsers(dest="command", required=True)
    lint = commands.add_parser("plan-lint")
    lint.add_argument("--static", action="store_true")
    lint.add_argument("--suggest", action="store_true", help="add read-only refinement suggestions")
    commands.add_parser("plan-go")
    repair = commands.add_parser("repair")
    repair.add_argument("--of", required=True, help="archived package slug to repair")
    amend = commands.add_parser("plan-amend")
    amend.add_argument("--file", required=True)
    commands.add_parser("next")
    commands.add_parser("list")
    commands.add_parser("diff")
    card = commands.add_parser("task-card", help="print a ready-to-paste v3 task card")
    card.add_argument("--id", required=True)
    card.add_argument("--title")
    card.add_argument("--boundary", required=True)
    card.add_argument("--verify")
    card.add_argument("--expect", default="green")
    card.add_argument("--depends-on", default="")
    card.add_argument("--test-boundary", default="")
    card.add_argument("--append", action="store_true", help="append to an unfrozen plan after static lint")
    card.add_argument("--criterion", help="acceptance text for each appended card; requires --append")
    card.add_argument(
        "--tdd", action="store_true", help="print a red-first test card plus its green implementation card"
    )
    verify = commands.add_parser("verify")
    verify.add_argument("tasks", nargs="*")
    verify.add_argument("--all", action="store_true")
    decide = commands.add_parser("decide")
    decide.add_argument("text", nargs="?")
    decide.add_argument("--fork")
    decide.add_argument("--by", default="V1")
    decide.add_argument("--park", action="store_true")
    decide.add_argument("--reversal")
    unblock = commands.add_parser("unblock")
    unblock.add_argument("task_id")
    unblock.add_argument("--reason", required=True)
    learn = commands.add_parser("learn")
    learn.add_argument("when")
    learn.add_argument("do")
    finish = commands.add_parser("done")
    finish.add_argument("--lesson-used", action="append", default=[])
    finish.add_argument("--lesson-wrong", action="append", default=[])
    check = commands.add_parser("check")
    check.add_argument("--check-only", action="store_true")
    check.add_argument("--lesson-used", action="append", default=[])
    check.add_argument("--lesson-wrong", action="append", default=[])
    review = commands.add_parser("ui-review")
    review.add_argument("--file", required=True)
    commands.add_parser("push")
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--fix", action="store_true")
    doctor.add_argument("--host", action="append", default=[])
    return result


def append_decision(root: Path, slug: str, args: argparse.Namespace) -> dict:
    directory, spec, _tasks = package.read_package(root, slug)
    section_name = "Parked" if args.park else "Decisions"
    prefix = "P" if args.park else "D"
    existing = package.parse_ledger(spec, section_name, prefix)
    identifier = f"{prefix}{len(existing) + 1}"
    if args.fork:
        fork = next((item for item in package.parse_forks(spec) if item["id"] == args.fork), None)
        if not fork:
            raise ValueError("unknown fork")
        args.by = fork["id"]
        args.text = f"{fork['text'].split('|', 1)[0].strip()} -> {fork['pick']}"
        args.reversal = args.reversal or fork["reversal"] or "checkpoint revert"
    else:
        args.reversal = args.reversal or "checkpoint revert"
    if not args.text or not args.text.strip():
        raise ValueError("decision text is required unless --fork supplies it")
    if not args.by.startswith(("V", "F", "user ")):
        raise ValueError("decision by must cite V<n>, F<n> or user <reference>")
    line = f"- {identifier} | {args.text} | by: {args.by} | reversal: {args.reversal}"
    if f"## {section_name}" not in spec:
        spec = spec.rstrip() + f"\n\n## {section_name}\n"
    spec = spec.replace(f"## {section_name}\n", f"## {section_name}\n{line}\n", 1)
    (directory / "spec.md").write_text(spec, encoding="utf-8")
    return {"id": identifier, "section": section_name, "by": args.by}


def finish_delivery(root: Path, slug: str, lesson_used: list[str], lesson_wrong: list[str]) -> tuple[dict, int]:
    try:
        result = done.finish(root, slug, lesson_used, lesson_wrong)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        # A passing gate that cannot complete delivery must remain a visible
        # repair state instead of looping the next route back to `deliver`.
        if package.package_dir(root, slug).is_dir():
            gate.record(root, slug, {"passed": False, "errors": [f"delivery: {exc}"], "parked": []})
        raise
    if result["finish"] in {"merge", "push"}:
        import push

        try:
            result["push"] = push.deliver(root, slug, push_enabled=result["finish"] == "push")
        except (OSError, ValueError, KeyError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            result["push"] = {"error": str(exc)}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            print(
                f"delivery incomplete: the package is archived at {result['archive']}; "
                "inspect the delivery branch, then rerun `spec.py push` from its spec branch.",
                file=sys.stderr,
            )
            return result, 1
    return result, 0


def task_card_lines(args: argparse.Namespace) -> list[str]:
    # PLAN-013 discipline at generation time: a card with an unlintable id is
    # wasted planning effort, so reject it here with the lint rule's wording.
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", args.id):
        raise ValueError(f"invalid task id: {args.id}; use a stable semantic slug")
    if args.tdd and not re.fullmatch(r"[a-z0-9][a-z0-9-]*", args.id + "-test"):
        raise ValueError(f"invalid task id: {args.id}-test; use a stable semantic slug")
    if args.tdd:
        base = args.id
        test_boundary = args.test_boundary or f"tests/test_{base.removeprefix('task-')}.py"
        verify = args.verify or f"python3 -m pytest {test_boundary} -q"
        return [
            f"- [ ] Write the failing test for {base}",
            f"  - id: {base}-test",
            f"  - boundary: new:{test_boundary}",
            f"  - verify: `{verify}`",
            "  - expect: red",
            "",
            f"- [ ] Implement {base} until green",
            f"  - id: {base}",
            f"  - boundary: {args.boundary}",
            f"  - verify: `{verify}`",
            f"  - expect: {args.expect}",
            f"  - depends-on: {base}-test",
        ]
    title = args.title or args.id
    lines = [
        f"- [ ] {title}",
        f"  - id: {args.id}",
        f"  - boundary: {args.boundary}",
        f"  - verify: `{args.verify or 'true'}`",
        f"  - expect: {args.expect}",
    ]
    if args.depends_on:
        lines.append(f"  - depends-on: {args.depends_on}")
    return lines


def _validate_append_fields(args: argparse.Namespace) -> None:
    for name in ("title", "boundary", "verify", "depends_on", "test_boundary", "criterion"):
        value = getattr(args, name)
        if value is None:
            continue
        if any(char in "\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029\0" for char in value):
            raise ValueError(f"--{name.replace('_', '-')} must be a single line")
        if name not in {"depends_on", "test_boundary"} and not value.strip():
            raise ValueError(f"--{name.replace('_', '-')} must not be empty")
    if args.verify and "`" in args.verify:
        raise ValueError("--verify must not contain backticks in a Markdown task card")
    if args.criterion is not None and "|" in args.criterion:
        raise ValueError("--criterion must not contain the Criteria delimiter '|'")
    if args.expect not in {"red", "green"}:
        raise ValueError("--expect must be red or green")
    if args.tdd and (args.title is not None or args.depends_on):
        raise ValueError("--tdd --append does not support --title or --depends-on; refine the generated cards")


def _append_criteria(text: str, task_ids: list[str], criterion: str | None) -> tuple[str, list[str]]:
    """Insert rows only in Criteria; never renumber existing ids or rebuild prose."""
    headers = list(re.finditer(r"(?m)^## Criteria[ \t]*(?:\r?\n|$)", text))
    if len(headers) > 1:
        raise ValueError("multiple Criteria sections")
    numbers = []
    for line in package.section(text, "Criteria"):
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 3 or not re.fullmatch(r"C[0-9]+", parts[0]):
            raise ValueError("malformed Criteria row; use C<number> | text | task id")
        numbers.append(int(parts[0][1:]))
    if len(numbers) != len(set(numbers)):
        raise ValueError("duplicate Criteria id")
    if criterion is None:
        return text, []
    start = max(numbers, default=0) + 1
    ids = [f"C{start + index}" for index in range(len(task_ids))]
    newline = "\r\n" if "\r\n" in text else "\n"
    rows = "".join(f"- {key} | {criterion} | {task}{newline}" for key, task in zip(ids, task_ids))
    if headers:
        following = re.search(r"(?m)^## ", text[headers[0].end() :])
        offset = headers[0].end() + following.start() if following else len(text)
    else:
        following = re.search(r"(?m)^## (?:Decisions|Parked|Binding)\b", text)
        offset = following.start() if following else len(text)
        rows = "## Criteria" + newline + rows + newline
    prefix = text[:offset]
    if prefix and not prefix.endswith("\n"):
        prefix += newline
    return prefix + rows + text[offset:], ids


def _publish_plan_files(
    directory: Path, before: dict[Path, bytes], after: dict[Path, bytes], modes: dict[Path, int]
) -> list[str]:
    """Per-file replacement with compensation, not a crash-atomic two-file commit."""
    staging = Path(tempfile.mkdtemp(prefix=".append-", dir=directory))
    published: list[Path] = []
    keep_backups = False
    committed = False
    warnings: list[str] = []
    changed = [path for path in before if before[path] != after[path]]
    try:
        for path in changed:
            for suffix, data in (("new", after[path]), ("old", before[path])):
                temporary = staging / f"{path.name}.{suffix}"
                temporary.write_bytes(data)
                temporary.chmod(modes[path])
        # Detect edits made since reading the plan. This is not a process lock.
        for path, data in before.items():
            if path.is_symlink() or path.read_bytes() != data or stat.S_IMODE(path.stat().st_mode) != modes[path]:
                raise ValueError(f"plan file changed during append: {path}; retry from current contents")
        for path in changed:
            if path.is_symlink() or path.read_bytes() != before[path]:
                raise ValueError(f"plan file changed during append: {path}")
            os.replace(staging / f"{path.name}.new", path)
            published.append(path)
        committed = True
    except (OSError, ValueError) as exc:
        failures = []
        for path in reversed(published):
            try:
                if path.is_symlink() or path.read_bytes() != after[path]:
                    raise OSError("file changed after replacement")
                os.replace(staging / f"{path.name}.old", path)
            except OSError as recovery:
                failures.append(f"{path}: {recovery}")
        if failures:
            keep_backups = True
            raise OSError(f"{exc}; rollback failed ({'; '.join(failures)}); backups retained at {staging}") from exc
        raise
    finally:
        if not keep_backups:
            try:
                shutil.rmtree(staging)
            except OSError as cleanup:
                warning = f"append cleanup failed; residual files at {staging}: {cleanup}"
                if committed:
                    warnings.append(warning)
                else:
                    # Do not hide the publish/rollback error with a cleanup error.
                    print(f"warning: {warning}", file=sys.stderr)
    return warnings


def append_task_card(args: argparse.Namespace) -> dict:
    _validate_append_fields(args)
    addition = "\n".join(task_card_lines(args)) + "\n"
    generated = package.parse_tasks(addition)
    for task in generated:
        if task.verify != "`" + task.command + "`":
            raise ValueError("generated verify must contain exactly one complete backticked command")
    root = package.resolve_root(args.root)
    slug = package.resolve_slug(root, args.slug)
    # Validate the slug and all write destinations before reading mutable text.
    package.package_dir(root, slug)
    directory = safe_child(safe_child(root, ".spec/specs"), slug)
    paths = [safe_child(directory, name) for name in ("spec.md", "tasks.md")]
    if any(not path.is_file() for path in paths):
        raise ValueError("append requires regular spec.md and tasks.md files")
    before = {path: path.read_bytes() for path in paths}
    modes = {path: stat.S_IMODE(path.stat().st_mode) for path in paths}
    spec, tasks = (before[path].decode("utf-8") for path in paths)
    if package.package_baseline(directory) or re.search(r"(?m)^- frozen:", spec):
        raise ValueError("package is frozen; use plan-amend instead of --append")
    if re.search(r"(?m)^- branch:|^## Binding\b", spec):
        bound = package.extract_branch(spec)
        if bound != gitx.branch(root):
            raise ValueError(f"package is bound to {bound}, not the current branch")
    existing = {task.id for task in package.parse_tasks(tasks)}
    ids = [task.id for task in generated]
    duplicates = existing.intersection(ids)
    if duplicates:
        raise ValueError("duplicate task id: " + ", ".join(sorted(duplicates)))
    candidate_spec, criterion_ids = _append_criteria(spec, ids, args.criterion)
    newline = "\r\n" if "\r\n" in tasks else "\n"
    separator = (newline if tasks.endswith("\n") else newline * 2) if tasks else ""
    candidate_tasks = tasks + separator + addition.replace("\n", newline)
    checked = plan.lint(root, slug, static=True, contents=(candidate_spec, candidate_tasks))
    result = {
        "slug": slug,
        "ready": checked["ready"],
        "appended": [],
        "criteria_added": [],
        "lint_mode": "static",
        "lint": checked,
    }
    if not checked["ready"]:
        return result
    after = {paths[0]: candidate_spec.encode("utf-8"), paths[1]: candidate_tasks.encode("utf-8")}
    warnings = _publish_plan_files(directory, before, after, modes)
    return {**result, "appended": ids, "criteria_added": criterion_ids, "warnings": warnings}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            import doctor

            return doctor.main(
                [
                    "--root",
                    args.root,
                    *(["--fix"] if args.fix else []),
                    *(item for host in args.host for item in ("--host", host)),
                ]
            )
        if args.command == "task-card":
            if args.append:
                result = append_task_card(args)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result["ready"] else 1
            if args.criterion is not None:
                raise ValueError("--criterion requires --append")
            print("\n".join(task_card_lines(args)))
            return 0
        root = package.resolve_root(args.root)
        if args.command == "repair":
            result = package.scaffold_repair(root, args.of)
            result["next"] = (
                "Edit the scaffolded repair task's boundary and verify, then run plan-lint and plan-go. "
                + route.spark("plan")
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "list":
            print(json.dumps({"packages": package.inventory(root)}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "push":
            slug = package.resolve_archived_slug(root, args.slug)
        elif args.command == "next":
            try:
                slug = package.resolve_slug(root, args.slug)
            except ValueError:
                # A delivered package may be the only lifecycle link left on
                # this branch; surface it instead of losing the push step.
                archived = route.archived_card(root) if args.slug is None else None
                if archived is None:
                    raise
                print(json.dumps(archived, ensure_ascii=False, indent=2))
                return 0
        else:
            slug = package.resolve_slug(root, args.slug)
        if args.command == "next":
            result = route.next_card(root, slug)
        elif args.command == "diff":
            result = package.revision_summary(root, slug)
        elif args.command == "plan-lint":
            result = plan.lint(root, slug, static=args.static, suggest=args.suggest)
        elif args.command == "plan-go":
            result = plan.go(root, slug)
            next_root = Path(result["worktree"]) if result.get("worktree") else root
            result["next"] = route.next_card(next_root, slug)
        elif args.command == "plan-amend":
            amendment = Path(args.file)
            if not amendment.is_absolute():
                amendment = root / amendment
            result = plan.amend(root, slug, amendment.read_text(encoding="utf-8"))
        elif args.command == "verify":
            selected = None if args.all else args.tasks
            if selected is None and not args.all:
                raise ValueError("choose task ids or --all")
            result = evidence.run_tasks(root, slug, selected)
        elif args.command == "unblock":
            result = evidence.unblock_task(root, slug, args.task_id, args.reason)
        elif args.command == "decide":
            result = append_decision(root, slug, args)
        elif args.command == "learn":
            result = {"lesson": memory.lessons_add(root, args.when, args.do)}
        elif args.command == "check":
            result = gate.compute(root, slug)
            gate.record(root, slug, result)
            # Stale-evidence errors are curable: done's final re-verify refreshes
            # them. Any other probe error, parked work, or --check-only returns
            # the gate result without attempting delivery.
            curable_only = all(e.endswith(STALE_EVIDENCE_SUFFIX) for e in result["errors"])
            if not args.check_only and curable_only and not result["parked"]:
                delivered, code = finish_delivery(root, slug, args.lesson_used, args.lesson_wrong)
                result.update(delivered)
                result.update({"passed": code == 0, "errors": [], "parked": []})
                result["next"] = route.completion_card(
                    slug,
                    str(result.get("finish", "commit")),
                    bool(isinstance(result.get("push"), dict) and result["push"].get("error")),
                )
                if code:
                    return code
            else:
                result["next"] = route.next_card(root, slug)
                if args.check_only and result.get("passed"):
                    result["next"]["next"] = (
                        "Delivery gate passed; run spec.py check to archive and deliver. " + route.spark("deliver")
                    )
        elif args.command == "ui-review":
            result = ui_review.write_record(root, slug, Path(args.file))
        elif args.command == "done":
            result, code = finish_delivery(root, slug, args.lesson_used, args.lesson_wrong)
            result["next"] = route.completion_card(
                slug,
                str(result.get("finish", "commit")),
                bool(isinstance(result.get("push"), dict) and result["push"].get("error")),
            )
            if code:
                return code
        elif args.command == "push":
            import push

            result = push.deliver(root, slug, push_enabled=True)
            result["next"] = route.completion_card(slug, "push")
        else:
            raise ValueError("unknown command")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "next":
            return 0
        if args.command == "check" and args.check_only:
            return 0 if result.get("passed", False) else 1
        return 0 if result.get("ready", result.get("passed", True)) else 1
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
