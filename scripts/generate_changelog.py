#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Generate standardized Chinese changelog from Git commits.

Converts commit messages to concise, standardized Chinese changelog entries.
Designed for spec workflow integration during done/push stages.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


def git_stdout(root: Path, *args: str) -> str:
    """Run git command and return stdout."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"error: git {' '.join(args)} timed out within 60s") from exc
    return result.stdout.strip()


def parse_commit_message(message: str) -> dict[str, str]:
    """
    Parse commit message into type, scope, and description.

    Supports formats:
    - Conventional: feat(scope): description
    - Simple: description
    - Spec footer: includes Spec: slug
    """
    # Extract spec slug if present
    spec_slug = None
    spec_match = re.search(r"^Spec:\s*(\S+)", message, re.MULTILINE)
    if spec_match:
        spec_slug = spec_match.group(1)

    # Get first line (subject)
    subject = message.split("\n")[0].strip()

    # Try conventional commit format
    conventional = re.match(r"^(\w+)(?:\(([^)]+)\))?: (.+)$", subject)
    if conventional:
        commit_type = conventional.group(1)
        scope = conventional.group(2) or ""
        description = conventional.group(3)
        return {
            "type": commit_type,
            "scope": scope,
            "description": description,
            "spec_slug": spec_slug,
        }

    # Fallback to simple format
    return {
        "type": "change",
        "scope": "",
        "description": subject,
        "spec_slug": spec_slug,
    }


def translate_type(commit_type: str) -> str:
    """Translate commit type to Chinese."""
    type_map = {
        "feat": "新增",
        "fix": "修复",
        "docs": "文档",
        "style": "样式",
        "refactor": "重构",
        "perf": "性能",
        "test": "测试",
        "build": "构建",
        "ci": "CI",
        "chore": "杂项",
        "revert": "回退",
        "change": "变更",
    }
    return type_map.get(commit_type.lower(), "变更")


def format_changelog_entry(commit_info: dict[str, str], show_spec: bool = True) -> str:
    """
    Format a single changelog entry in Chinese.

    Format: - 【类型】作用域：描述 (spec_slug)
    """
    type_cn = translate_type(commit_info["type"])
    scope = commit_info["scope"]
    description = commit_info["description"]
    spec_slug = commit_info["spec_slug"]

    # Build entry
    if scope:
        entry = f"- 【{type_cn}】{scope}：{description}"
    else:
        entry = f"- 【{type_cn}】{description}"

    # Add spec slug if present and requested
    if show_spec and spec_slug:
        entry += f" `({spec_slug})`"

    return entry


def generate_changelog(
    root: Path,
    from_ref: str = "",
    to_ref: str = "HEAD",
    show_spec: bool = True,
    filter_spec_prefix: bool = False,
) -> list[str]:
    """
    Generate changelog entries from Git history.

    Args:
        root: Repository root
        from_ref: Start reference (exclusive), empty for all history
        to_ref: End reference (inclusive)
        show_spec: Include spec slug in entries
        filter_spec_prefix: Only include commits from spec/* branches

    Returns:
        List of changelog entry strings
    """
    # Build git log command. Use the ASCII record separator (%x1e) so commit
    # messages containing any printable sentinel can never corrupt parsing.
    if from_ref:
        revision_range = f"{from_ref}..{to_ref}"
    else:
        revision_range = to_ref

    # Get commit list with messages. Read raw output: str.strip() would eat
    # the \x1e record separators (Python classifies them as whitespace).
    log_format = "%H%x1e%s%x1e%b%x1e"
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "log", revision_range, f"--format={log_format}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit("error: git log timed out within 60s") from exc
    commits_text = completed.stdout

    # Parse commits: fields arrive as (sha, subject, body) triplets. Drop only
    # the single trailing newline git appends per record, then the one empty
    # field produced by the trailing separator.
    entries = []
    stream = commits_text[:-1] if commits_text.endswith("\n") else commits_text
    if not stream:
        return []
    fields = stream.split("\x1e")
    if fields and fields[-1] == "":
        fields.pop()
    if len(fields) % 3 != 0:
        raise ValueError("git log output is not a well-formed record stream")

    for index in range(0, len(fields), 3):
        commit_sha = fields[index].strip()
        subject = fields[index + 1].strip()
        body = fields[index + 2].strip()
        if not commit_sha or not subject:
            continue
        message = subject if not body else f"{subject}\n{body}"
        # Filter by branch if requested
        if filter_spec_prefix:
            # Check if commit is in a spec/* branch
            try:
                branches_output = subprocess.run(
                    ["git", "-C", str(root), "branch", "--contains", commit_sha, "--format=%(refname:short)"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except subprocess.TimeoutExpired:
                branches_output = None
            if branches_output is not None and branches_output.returncode == 0:
                branches = branches_output.stdout.strip().split("\n")
                has_spec_branch = any(b.startswith("spec/") for b in branches if b)
                if not has_spec_branch:
                    continue

        # Parse and format
        commit_info = parse_commit_message(message)
        entry = format_changelog_entry(commit_info, show_spec=show_spec)
        entries.append(entry)

    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate standardized Chinese changelog from Git commits")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository root (default: current directory)",
    )
    parser.add_argument(
        "--from",
        dest="from_ref",
        default="",
        help="Start reference (exclusive), empty for all history",
    )
    parser.add_argument(
        "--to",
        dest="to_ref",
        default="HEAD",
        help="End reference (inclusive, default: HEAD)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output file (default: stdout)",
    )
    parser.add_argument(
        "--no-spec",
        action="store_true",
        help="Don't include spec slug in entries",
    )
    parser.add_argument(
        "--spec-only",
        action="store_true",
        help="Only include commits from spec/* branches",
    )
    parser.add_argument(
        "--prepend",
        action="store_true",
        help="Prepend to existing output file instead of replacing",
    )

    args = parser.parse_args()

    try:
        entries = generate_changelog(
            root=args.root,
            from_ref=args.from_ref,
            to_ref=args.to_ref,
            show_spec=not args.no_spec,
            filter_spec_prefix=args.spec_only,
        )

        if not entries:
            print("No commits found in range", file=sys.stderr)
            return 0

        # Format output
        output_text = "\n".join(entries) + "\n"

        # Write or print
        if args.output:
            if args.prepend and args.output.exists():
                existing = args.output.read_text(encoding="utf-8")
                output_text = output_text + "\n" + existing
            args.output.write_text(output_text, encoding="utf-8")
            print(f"Changelog written to {args.output}", file=sys.stderr)
        else:
            print(output_text, end="")

        return 0

    except subprocess.CalledProcessError as e:
        print(f"Git command failed: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
