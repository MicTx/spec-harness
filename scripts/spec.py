#!/usr/bin/env python3
"""CLI for planning, verifying and delivering Spec packages."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import done
import evidence
import gate
import gitx
import local_evidence
import package
import plan
import push
import route


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", default=".")
    result.add_argument("--slug")
    result.add_argument("--json", action="store_true", help="emit the full structured result")
    inherited = argparse.ArgumentParser(add_help=False)
    inherited.add_argument("--root", default=argparse.SUPPRESS)
    inherited.add_argument("--slug", default=argparse.SUPPRESS)
    inherited.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    commands = result.add_subparsers(dest="command", required=True)
    lint = commands.add_parser("plan-lint", parents=[inherited])
    lint.add_argument("--static", action="store_true")
    commands.add_parser("plan-go", parents=[inherited])
    quick = commands.add_parser("quick", parents=[inherited], help="freeze an in-place small fix")
    quick.add_argument("goal")
    quick.add_argument("--done")
    quick.add_argument("--path", action="append", required=True)
    quick.add_argument("--verify", required=True)
    amend = commands.add_parser("plan-amend", parents=[inherited])
    amend.add_argument("--file", required=True)
    amend.add_argument("--revise", action="store_true")
    amend.add_argument("--by", default="")
    for command in ("next", "list", "diff", "done", "push"):
        commands.add_parser(command, parents=[inherited])
    verify = commands.add_parser("verify", parents=[inherited])
    verify.add_argument("tasks", nargs="*")
    verify.add_argument("--all", action="store_true")
    decide = commands.add_parser("decide", parents=[inherited])
    decide.add_argument("text", nargs="?")
    decide.add_argument("--fork")
    decide.add_argument("--by", default="V1")
    decide.add_argument("--park", action="store_true")
    decide.add_argument("--resolve", metavar="P_ID")
    decide.add_argument("--choice", choices=("wait", "resolve", "local-commit"))
    decide.add_argument("--reversal")
    unblock = commands.add_parser("unblock", parents=[inherited])
    unblock.add_argument("task_id")
    unblock.add_argument("--reason", required=True)
    check = commands.add_parser("check", parents=[inherited])
    check.add_argument("--check-only", action="store_true")
    purge = commands.add_parser("purge", parents=[inherited])
    purge.add_argument("--check", action="store_true")
    doctor = commands.add_parser("doctor", parents=[inherited])
    doctor.add_argument("--fix", action="store_true")
    doctor.add_argument("--host", action="append", default=[])
    return result


def append_decision(root: Path, slug: str, args: argparse.Namespace) -> dict:
    if args.resolve or args.choice:
        if not args.resolve or not args.choice or args.park or args.fork:
            raise ValueError("use --resolve and --choice together, without --park or --fork")
        return package.resolve_parked(root, slug, args.resolve, args.choice, args.by.strip(), (args.text or "").strip())
    directory, spec, _tasks = package.read_package(root, slug)
    name, prefix = ("Parked", "P") if args.park else ("Decisions", "D")
    existing = package.parse_ledger(spec, name, prefix)
    identifier = f"{prefix}{1 + max((int(item['id'][1:]) for item in existing), default=0)}"
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
    if f"## {name}" not in spec:
        spec = spec.rstrip() + f"\n\n## {name}\n"
    spec = spec.replace(f"## {name}\n", f"## {name}\n{line}\n", 1)
    (directory / "spec.md").write_text(spec, encoding="utf-8")
    if args.park and package.package_baseline(directory):
        package.append_run(directory, dict(kind="park", id=identifier, ts=package.now()))
    return {"id": identifier, "section": name, "by": args.by}


DELIVERY_ERRORS = (OSError, ValueError, KeyError, subprocess.SubprocessError)


def delivery_check(root: Path, slug: str, args: argparse.Namespace, *, recover: bool = False) -> dict:
    probe = args.command == "check" and args.check_only
    try:
        if recover:
            result = done.recover_archive(root, slug)
            if result is None:
                raise ValueError(f"no interrupted archive to resume: {slug}")
        elif probe:
            result = gate.compute(root, slug)
            gate.record(root, slug, result, probe=True)
        else:
            result = done.finish(root, slug)
    except DELIVERY_ERRORS as exc:
        finding = gate.finding("delivery-error", str(exc), action="inspect-delivery")
        result = gate.add(dict(findings=[], parked=[], tree_fp=None), finding)
        if package.package_dir(root, slug).is_dir():
            try:
                gate.record(root, slug, result, probe=probe)
            except DELIVERY_ERRORS:
                pass
    if not result.get("archive"):
        try:
            result["next"] = route.next_card(root, slug)
        except DELIVERY_ERRORS:
            result["next"] = {"state": "check-failed", "next": "Inspect the package error, then run spec.py check."}
        if probe and result["passed"]:
            result["next"]["next"] = "Preflight passed; run spec.py check for final verification."
        for error in result["errors"]:
            print(f"error: {error}", file=sys.stderr)
        return result
    finish = result["finish"]
    if finish in {"merge", "push"}:
        try:
            result["push"] = push.deliver(root, slug, push_enabled=finish == "push")
            if finish == "merge":
                package.append_run(Path(result["archive"]), {"kind": "handoff", "finish": finish, "ts": package.now()})
        except DELIVERY_ERRORS as exc:
            result["push"] = {"error": str(exc)}
            gate.add(result, gate.finding("handoff-error", str(exc), action="retry-handoff"))
            print("delivery incomplete: retry the authorized handoff with `spec.py push`.", file=sys.stderr)
    result["next"] = route.completion_card(slug, finish, not result["passed"])
    if finish == "commit" and result["requested_finish"] != finish:
        result["next"]["next"] = "Committed locally by decision; no merge or push performed."
    return result


def emit(args: argparse.Namespace, result: dict) -> None:
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    for gap in result.get("gaps", [])[:4]:
        print(f"{gap['rule']}: {gap['detail']}")
    card = result.get("next")
    if args.command == "quick" and result.get("ready"):
        print(f"{result['slug']}: ready\nEdit the scoped files here, then run spec.py check.")
    elif isinstance(card, dict) or result.get("state"):
        data = dict(card if isinstance(card, dict) else result)
        if "worktree" in result:
            data["worktree"] = result["worktree"]
        print(route.render_brief(data, 460))
    elif "packages" in result:
        for entry in result["packages"]:
            print(f"{entry['slug']}: {entry['tasks']['checked']}/{entry['tasks']['total']} tasks; {entry['branch']}")
        if not result["packages"]:
            print("No packages.")
    else:
        status = "ready" if result.get("ready", result.get("passed", True)) else "blocked"
        print(f"{result.get('slug') or args.command}: {status}")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            import doctor

            options = ["--root", args.root] + (["--fix"] if args.fix else [])
            for host in args.host:
                options.extend(("--host", host))
            return doctor.main(options)
        if args.command in {"purge", "quick"}:
            root = gitx.toplevel(Path(args.root))
            if root is None:
                raise ValueError(f"{args.command} requires a Git checkout")
            if args.command == "purge":
                result = local_evidence.purge(root, dry_run=args.check)
            else:
                result = plan.start_quick(root, args.slug, args.goal, args.done or args.goal, args.path, args.verify)
            emit(args, result)
            return 0 if result.get("ready", True) else 1
        root = package.resolve_root(args.root)
        if args.command in {"plan-go", "verify", "check", "done", "push"} and not getattr(args, "check_only", False):
            for retired in gitx.legacy_git_hooks(root, retire=True):
                print(f"spec: retired a legacy Spec Git hook: {retired}", file=sys.stderr)
            if gitx.legacy_git_hooks(root):
                print("spec: a legacy Spec Git hook could not be retired; run spec.py doctor", file=sys.stderr)
        if args.command == "list":
            emit(args, {"packages": package.inventory(root)})
            return 0
        recover = False
        if args.command == "push":
            slug = package.resolve_archived_slug(root, args.slug)
        elif args.command == "next":
            try:
                slug = package.resolve_slug(root, args.slug)
            except ValueError:
                archived = route.archived_card(root) if args.slug is None else None
                if archived is None:
                    raise
                emit(args, archived)
                return 0
        else:
            try:
                slug = package.resolve_slug(root, args.slug)
            except ValueError as exc:
                if args.command != "done" and (args.command != "check" or args.check_only):
                    raise
                if "multiple active packages" in str(exc):
                    raise
                slug = package.resolve_archived_slug(root, args.slug)
                recover = True
        if args.command == "next":
            result = route.next_card(root, slug)
        elif args.command == "diff":
            result = package.revision_summary(root, slug)
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
            result = plan.amend(root, slug, amendment.read_text(encoding="utf-8"), revise=args.revise, by=args.by)
        elif args.command == "verify":
            selected = None if args.all else args.tasks
            result = evidence.run_tasks(root, slug, selected)
        elif args.command == "unblock":
            result = evidence.unblock_task(root, slug, args.task_id, args.reason)
        elif args.command == "decide":
            result = append_decision(root, slug, args)
        elif args.command in {"check", "done"}:
            result = delivery_check(root, slug, args, recover=recover)
        elif args.command == "push":
            archived, spec, _ = package.read_archived_package(root, slug)
            finish, _ = package.effective_finish(archived, spec)
            merge_pending = finish == "merge" and not package.handoff_completed(archived, "merge")
            result = push.deliver(root, slug, push_enabled=not merge_pending)
            if merge_pending:
                package.append_run(archived, {"kind": "handoff", "finish": "merge", "ts": package.now()})
            result["next"] = route.completion_card(slug, "merge" if merge_pending else "push")
        else:
            raise ValueError("unknown command")
        emit(args, result)
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
