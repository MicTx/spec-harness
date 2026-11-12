#!/usr/bin/env python3
"""Single model-side CLI for Spec Harness v3."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import done  # noqa: E402
import evidence  # noqa: E402
import gate  # noqa: E402
import memory  # noqa: E402
import package  # noqa: E402
import plan  # noqa: E402
import route  # noqa: E402

STALE_EVIDENCE_SUFFIX = "evidence is stale for the current command or tree"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", default=".")
    result.add_argument("--slug")
    commands = result.add_subparsers(dest="command", required=True)
    lint = commands.add_parser("plan-lint")
    lint.add_argument("--static", action="store_true")
    commands.add_parser("plan-go")
    amend = commands.add_parser("plan-amend")
    amend.add_argument("--file", required=True)
    commands.add_parser("next")
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
    result = done.finish(root, slug, lesson_used, lesson_wrong)
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
        root = package.resolve_root(args.root)
        if args.command == "push":
            slug = package.resolve_archived_slug(root, args.slug)
        else:
            slug = package.resolve_slug(root, args.slug)
        if args.command == "next":
            result = route.next_card(root, slug)
        elif args.command == "plan-lint":
            result = plan.lint(root, slug, static=args.static)
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
            # Stale-evidence errors are curable: done's final re-verify refreshes
            # them. Any other probe error, parked work, or --check-only returns
            # the gate result without attempting delivery.
            curable_only = all(e.endswith(STALE_EVIDENCE_SUFFIX) for e in result["errors"])
            if not args.check_only and curable_only and not result["parked"]:
                delivered, code = finish_delivery(root, slug, args.lesson_used, args.lesson_wrong)
                result.update(delivered)
                result.update({"passed": code == 0, "errors": [], "parked": []})
                if code:
                    return code
        elif args.command == "done":
            result, code = finish_delivery(root, slug, args.lesson_used, args.lesson_wrong)
            if code:
                return code
        elif args.command == "push":
            import push

            result = push.deliver(root, slug, push_enabled=True)
        else:
            raise ValueError("unknown command")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "next":
            return 0
        return 0 if result.get("ready", result.get("passed", True)) else 1
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
