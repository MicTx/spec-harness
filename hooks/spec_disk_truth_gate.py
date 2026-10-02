#!/usr/bin/env python3
"""Enforce Spec disk truth before irreversible claims and Git handoff."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

CHECK_ALL_SCRIPT = (Path(__file__).resolve().parent.parent / "scripts" / "check_all_spec_packages.py").resolve()
GIT_OPTIONS_WITH_VALUE = {
    "-C",
    "-c",
    "--config-env",
    "--exec-path",
    "--git-dir",
    "--namespace",
    "--work-tree",
}
COMMIT_OPTIONS_WITH_VALUE = {
    "-m",
    "--message",
    "-F",
    "--file",
    "-C",
    "--reuse-message",
    "-c",
    "--reedit-message",
    "--author",
    "--date",
    "--cleanup",
    "--fixup",
    "--squash",
    "--trailer",
}
IMPLICIT_STAGE_LONG_OPTIONS = {
    "--all",
    "--include",
    "--only",
    "--pathspec-from-file",
}
NON_TERMINAL_MESSAGE_PATTERN = re.compile(
    r"(?:进行中|尚未完成|未完成|待修复|待确认|阻塞|暂停|需要(?:用户|你)(?:确认|输入|选择)|"
    r"in\s+progress|not\s+(?:done|complete)|incomplete|blocked|paused|"
    r"waiting\s+for|need(?:s|ed)?\s+(?:user\s+)?(?:input|confirmation))",
    re.IGNORECASE,
)
DEVELOPMENT_RECORD_SLUG_PATTERN = re.compile(r"\b\d{4}-\d{2}-\d{2}_[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?\b")
DEVELOPMENT_RECORD_REFERENCE_PATTERN = re.compile(
    r"(?P<slug>\d{4}-\d{2}-\d{2}_[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?)#(?P<anchor>[A-Za-z0-9_.-]+)"
)


class GitInvocation(NamedTuple):
    root: Path
    subcommand: str
    args: tuple[str, ...]
    unsafe_global_options: bool


def load_input() -> dict[str, object]:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_direct_git(command: str, cwd: Path) -> GitInvocation | None:
    """Return metadata only for one direct Git command."""
    command = re.sub(r"\\\r?\n", "", command)
    if "$(" in command or "`" in command:
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return None
    if not tokens or Path(tokens[0]).name != "git":
        return None
    if any(token and set(token) <= set(";&|<>") for token in tokens):
        return None

    effective_cwd = cwd
    unsafe_global_options = False
    index = 1
    while index < len(tokens) and tokens[index].startswith("-"):
        raw_option = tokens[index]
        option, separator, inline_value = raw_option.partition("=")
        index += 1
        value = inline_value if separator else None
        if option in GIT_OPTIONS_WITH_VALUE and value is None:
            if index >= len(tokens):
                return None
            value = tokens[index]
            index += 1
        if option == "-C" and value:
            candidate = Path(value)
            effective_cwd = (candidate if candidate.is_absolute() else effective_cwd / candidate).resolve(strict=False)
        elif option in {"--git-dir", "--work-tree", "-c", "--config-env"}:
            unsafe_global_options = True

    if index >= len(tokens):
        return None
    subcommand = tokens[index]
    return GitInvocation(
        effective_cwd,
        subcommand,
        tuple(tokens[index + 1 :]),
        unsafe_global_options,
    )


def commit_changes_index(args: tuple[str, ...]) -> bool:
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            return index + 1 < len(args)
        option, separator, _ = arg.partition("=")
        if option in IMPLICIT_STAGE_LONG_OPTIONS:
            return True
        if arg.startswith("-") and not arg.startswith("--"):
            flags = arg[1:]
            if any(flag in flags for flag in "aoi"):
                return True
            if flags and flags[0] in "mFCc":
                index += 1
                if len(flags) == 1:
                    index += 1
                continue
        if option in COMMIT_OPTIONS_WITH_VALUE:
            index += 1
            if not separator:
                index += 1
            continue
        if not arg.startswith("-"):
            return True
        index += 1
    return False


def find_project_root(start: Path) -> Path:
    current = start.resolve(strict=False)
    specs_dirs = configured_specs_dirs()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists() or any((candidate / name / "specs").is_dir() for name in specs_dirs):
            return candidate
    return current


def clean_git_env() -> dict[str, str]:
    # Same blacklist strategy as push_spec_package: strip only the
    # directory/index state variables that redirect the repository itself,
    # preserving GIT_SSH_COMMAND, GIT_CONFIG_*, etc.
    blocked_git_vars = {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    }
    return {
        key: value
        for key, value in os.environ.items()
        if key not in blocked_git_vars and not key.startswith("GIT_CONFIG_") and key != "SSH_ASKPASS"
    }


def git_alias_value(root: Path, subcommand: str) -> str | None:
    project_root = find_project_root(root)
    completed = subprocess.run(
        ["git", "config", "--get", f"alias.{subcommand}"],
        cwd=project_root,
        env=clean_git_env(),
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return completed.stdout.strip()
    return None


def run_disk_check(
    root: Path,
    *,
    index: bool = False,
    require_archived: bool = False,
    slugs: list[str] | None = None,
    specs_dirs: list[str] | None = None,
) -> tuple[bool, str]:
    root = find_project_root(root)
    if not CHECK_ALL_SCRIPT.is_file():
        return False, f"checker missing: {CHECK_ALL_SCRIPT}"
    command = [sys.executable, str(CHECK_ALL_SCRIPT), "--root", str(root)]
    if index:
        command.append("--index")
    if require_archived:
        command.append("--require-archived")
    for specs_dir in specs_dirs or []:
        command.extend(["--specs-dir", specs_dir])
    for slug in slugs or []:
        command.extend(["--slug", slug])
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"checker error: {exc}"
    if completed.returncode == 0:
        return True, ""
    detail = (completed.stdout or completed.stderr).strip()
    return False, detail or f"checker exit {completed.returncode}"


def touched_spec_slugs(root: Path) -> list[str] | None:
    """Return active Spec package slugs changed on the current branch vs main.

    Returns None when detection fails (detached HEAD, no main, etc.) so the
    caller can fall back to the conservative all-packages gate.
    """
    project_root = find_project_root(root)
    env = clean_git_env()

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=project_root,
            env=env,
            capture_output=True,
            text=True,
        )

    branch = run("branch", "--show-current")
    if branch.returncode != 0 or not branch.stdout.strip():
        return None
    current = branch.stdout.strip()

    # Find the default/main branch to diff against.
    main_ref = None
    for candidate in ("main", "master"):
        check = run("show-ref", "--verify", f"refs/heads/{candidate}")
        if check.returncode == 0:
            main_ref = f"refs/heads/{candidate}"
            break
    if main_ref is None:
        return None

    base = run("merge-base", main_ref, f"refs/heads/{current}")
    if base.returncode != 0:
        return None
    diff = run("diff", "--name-only", base.stdout.strip(), f"refs/heads/{current}")
    if diff.returncode != 0:
        return None

    slugs: set[str] = set()
    for line in diff.stdout.splitlines():
        parts = Path(line.strip()).parts
        if len(parts) < 3 or parts[0] not in {".spec", ".trae"} or parts[1] != "specs":
            continue
        slug = parts[2]
        if slug == "archive":
            if len(parts) < 4:
                continue
            slug = parts[3]
        if slug:
            slugs.add(slug)
    return sorted(slugs)


def configured_specs_dirs() -> list[str]:
    values = [".spec", ".trae"]
    raw = os.environ.get("SPEC_SPECS_DIRS", "")
    values.extend(part.strip() for part in raw.split(",") if part.strip())
    return list(dict.fromkeys(values))


def non_terminal_message_is_recorded(root: Path, message: str) -> tuple[bool, str]:
    project_root = find_project_root(root)
    references = sorted(set(DEVELOPMENT_RECORD_REFERENCE_PATTERN.findall(message)))
    referenced_slugs = {slug for slug, _anchor in references}
    bare_slugs = set(DEVELOPMENT_RECORD_SLUG_PATTERN.findall(message)) - referenced_slugs
    if bare_slugs:
        return False, "非终态消息包含未锚定 Development Record slug：" + ", ".join(sorted(bare_slugs))
    if not references:
        return False, "非终态消息必须引用 `<Development Record slug>#<task-or-issue-id>`"
    failures: list[str] = []
    scripts_dir = CHECK_ALL_SCRIPT.parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        from issue_closure_support import extract_issue_closure, parse_task_records
        from spec_package_support import resolve_specs_root
    except ImportError as exc:
        return False, f"问题闭环校验器不可用：{exc}"

    specs_dirs = configured_specs_dirs()
    try:
        specs_roots = [(name, resolve_specs_root(project_root, name)) for name in specs_dirs]
    except ValueError as exc:
        return False, f"可信 Spec 根配置无效：{exc}"

    for slug, anchor in references:
        matches: list[tuple[str, Path, bool]] = []
        for name, specs_root in specs_roots:
            active = specs_root / "specs" / slug
            archived = specs_root / "specs" / "archive" / slug
            if active.is_dir() and not active.is_symlink():
                matches.append((name, active, False))
            if archived.is_dir() and not archived.is_symlink():
                matches.append((name, archived, True))
        if not matches:
            failures.append(f"{slug}: package not found")
            continue
        if len(matches) != 1:
            roots = ", ".join(name for name, _path, _archived in matches)
            failures.append(f"{slug}: package anchor is ambiguous across Spec roots: {roots}")
            continue
        specs_name, package, is_archived = matches[0]
        if not is_archived:
            tasks = package / "tasks.md"
            try:
                records = parse_task_records(tasks.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                failures.append(f"{slug}: active tasks.md unreadable")
                continue
            task = records.get(anchor)
            if task is None:
                failures.append(f"{slug}: task anchor not found: {anchor}")
            elif task["completed"]:
                failures.append(f"{slug}: task anchor is already completed: {anchor}")
            continue
        passed, detail = run_disk_check(
            project_root,
            require_archived=True,
            slugs=[slug],
            specs_dirs=specs_dirs,
        )
        if not passed:
            failures.append(f"{slug}: archived package invalid under {specs_name}: {detail}")
            continue
        try:
            summary = (package / "completion-summary.md").read_text(encoding="utf-8")
            payload = extract_issue_closure(summary)
        except (OSError, UnicodeError, ValueError) as exc:
            failures.append(f"{slug}: archived closure unreadable: {exc}")
            continue
        issues = payload.get("issues", []) if isinstance(payload, dict) else []
        issue = next(
            (issue for issue in issues if isinstance(issue, dict) and issue.get("id") == anchor),
            None,
        )
        if issue is None:
            failures.append(f"{slug}: issue anchor not found: {anchor}")
        elif issue.get("disposition") != "external_blocked":
            failures.append(f"{slug}: archived issue is not an external blocker: {anchor}")
    return (not failures, "; ".join(failures))


def failure_reason(root: Path, detail: str) -> str:
    return (
        "Spec 磁盘真源门禁未通过，禁止提交、推送或宣称完成。"
        f"目标仓库：{root}。检查结果：{detail}。"
        f"请运行：python3 {CHECK_ALL_SCRIPT} --root {root}"
    )


def emit_pretool_block(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            },
            ensure_ascii=False,
        )
    )


def emit_stop_block(reason: str) -> None:
    print(
        json.dumps(
            {"decision": "block", "reason": reason, "systemMessage": reason},
            ensure_ascii=False,
        )
    )


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    payload = load_input()
    cwd_value = payload.get("cwd")
    cwd = Path(cwd_value) if isinstance(cwd_value, str) and cwd_value else Path.cwd()

    if mode == "pretool":
        tool_input = payload.get("tool_input")
        command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        if not isinstance(command, str):
            return 0
        invocation = parse_direct_git(command, cwd)
        if invocation is None:
            return 0
        if invocation.subcommand not in {"commit", "push"}:
            alias = git_alias_value(invocation.root, invocation.subcommand)
            if alias is not None:
                emit_pretool_block(
                    f"Spec Git 门禁禁止 Git alias `{invocation.subcommand}`（{alias}）；请使用显式 Git 子命令。"
                )
            return 0
        if "--no-verify" in invocation.args or "-n" in invocation.args:
            emit_pretool_block("Spec Git 门禁禁止 commit/push 使用 `--no-verify` / `-n` 跳过 Git hooks。")
            return 0
        if invocation.unsafe_global_options:
            emit_pretool_block(
                "Spec Git 门禁禁止 commit/push 使用 `--git-dir`、`--work-tree` 或 `-c` "
                "重定向仓库；请从目标仓库直接运行显式命令。"
            )
            return 0
        if invocation.subcommand == "commit" and commit_changes_index(invocation.args):
            emit_pretool_block(
                "Spec Git 门禁禁止 `git commit -a/--all/--only/--include` 或 commit pathspec；"
                "请先单独执行 `git add`，再运行纯 `git commit`，以确保 index 真源已被校验。"
            )
            return 0
        if invocation.subcommand == "push":
            # Touched-package mode: gate only Spec packages changed on this
            # branch. Detection failure falls back to all-packages so a broken
            # gate cannot be bypassed by an ambiguous repository state.
            slugs = touched_spec_slugs(invocation.root)
            if slugs is not None and not slugs:
                return 0
            passed, detail = run_disk_check(
                invocation.root,
                require_archived=True,
                slugs=slugs,
            )
            if not passed:
                emit_pretool_block(failure_reason(invocation.root, detail))
            return 0
        passed, detail = run_disk_check(invocation.root)
        if not passed:
            emit_pretool_block(failure_reason(invocation.root, detail))
            return 0
        index_passed, index_detail = run_disk_check(invocation.root, index=True)
        if not index_passed:
            emit_pretool_block(
                failure_reason(
                    invocation.root,
                    f"Git index snapshot failed: {index_detail}",
                )
            )
        return 0

    if mode != "stop":
        return 0
    if payload.get("stop_hook_active") is True:
        return 0
    message = payload.get("last_assistant_message", "")
    if not isinstance(message, str):
        return 0
    if NON_TERMINAL_MESSAGE_PATTERN.search(message):
        recorded, detail = non_terminal_message_is_recorded(cwd, message)
        if not recorded:
            emit_stop_block(
                "Spec 问题闭环门禁未通过：" + detail + "。请先回写当前 tasks.md，或完成并引用后续 Development Record。"
            )
        return 0
    passed, detail = run_disk_check(cwd, require_archived=True)
    if not passed:
        emit_stop_block(failure_reason(cwd, detail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
