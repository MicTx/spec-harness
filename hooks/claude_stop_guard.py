#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Spec-NonCommercial
"""Claude Code Stop hook: keep the agent working while the active Spec
package bound to the current integration branch has not converged.

Protocol (https://code.claude.com/docs/en/hooks.md, Stop event):
- stdin JSON carries common fields (session_id, transcript_path, cwd,
  permission_mode, hook_event_name) plus stop_hook_active and
  last_assistant_message.
- stdout JSON ``{"decision": "block", "reason": ...}`` prevents the stop;
  exit 0 with no output allows it.

Behavior: locate the nearest ancestor of ``cwd`` that has a ``.spec/specs``
tree, select the package bound to the current branch (or the sole package
outside Git), run ``scripts/check_spec_package.py --format json``, and block
with the gap list when ``converged`` is false. Ambiguous bindings fail closed;
projects without a Spec tree are never touched.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = (Path(__file__).resolve().parent.parent / "scripts").resolve()
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from spec_package_support import active_package_slugs, resolve_specs_root, select_active_package  # noqa: E402

CHECK_SCRIPT = (SCRIPTS_DIR / "check_spec_package.py").resolve()
SPECS_DIR_NAME = ".spec"
CHECK_TIMEOUT_SECONDS = 30


def load_input() -> dict[str, object]:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def find_spec_project_root(start: Path) -> Path | None:
    """Nearest ancestor (including start) that has an active .spec/specs tree."""
    current = start.resolve(strict=False)
    for candidate in (current, *current.parents):
        if (candidate / SPECS_DIR_NAME / "specs").is_dir():
            return candidate
    return None


def active_slug_for_worktree(project_root: Path) -> tuple[str | None, str]:
    """Select the package bound to this worktree; never guess by newest date."""
    try:
        specs_root = resolve_specs_root(project_root, SPECS_DIR_NAME)
        packages = active_package_slugs(specs_root / "specs")
        selection = select_active_package(project_root, specs_root, packages)
    except (OSError, UnicodeError, ValueError) as exc:
        return None, f"无法绑定当前工作分支的任务包：{exc}"
    if selection.slug:
        return selection.slug, ""
    return None, selection.warning


def run_convergence_check(project_root: Path, slug: str) -> tuple[bool, list[str], str]:
    """Return (checkable, converged, gaps-or-error-detail)."""
    if not CHECK_SCRIPT.is_file():
        return False, False, f"check script missing: {CHECK_SCRIPT}"
    command = [
        sys.executable,
        str(CHECK_SCRIPT),
        "--root",
        str(project_root),
        "--slug",
        slug,
        "--format",
        "json",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=CHECK_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, False, f"checker error: {exc}"
    # Exit code reflects the overall gate, not convergence; parse the JSON
    # body either way and trust only the converged/gaps fields.
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, ValueError):
        detail = (completed.stderr or completed.stdout).strip()
        return False, False, f"checker produced no json (exit {completed.returncode}): {detail[:400]}"
    if not isinstance(payload, dict) or not isinstance(payload.get("converged"), bool):
        return False, False, "checker json missing boolean converged field"
    gaps = payload.get("gaps")
    if not isinstance(gaps, list) or not all(isinstance(gap, str) for gap in gaps):
        return False, False, "checker json gaps field is not a string list"
    return True, payload["converged"], gaps


def emit_stop_block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason, "systemMessage": reason}, ensure_ascii=False))


def main() -> int:
    payload = load_input()

    event_name = payload.get("hook_event_name")
    if isinstance(event_name, str) and event_name and event_name != "Stop":
        # Wired for Stop; anything else (e.g. SubagentStop) is not ours to gate.
        return 0
    if payload.get("stop_hook_active") is True:
        # Claude Code is already continuing because of a stop hook; staying
        # silent here keeps one enforcement round per turn instead of a loop.
        return 0

    cwd_value = payload.get("cwd")
    cwd = Path(cwd_value) if isinstance(cwd_value, str) and cwd_value else Path.cwd()

    project_root = find_spec_project_root(cwd)
    if project_root is None:
        return 0
    slug, binding_error = active_slug_for_worktree(project_root)
    if slug is None:
        if binding_error:
            emit_stop_block(binding_error)
        return 0

    checkable, converged, detail = run_convergence_check(project_root, slug)
    if not checkable:
        # A Spec project whose convergence checker cannot run is treated as
        # not converged: block once with the failure so the agent surfaces it.
        emit_stop_block(
            "Spec 收敛守卫无法运行收敛检查"
            f"（slug {slug}）：{detail}。"
            f"请修复检查环境或运行 python3 {CHECK_SCRIPT} --root {project_root} --slug {slug} --format json 排查。"
        )
        return 0
    if converged:
        # Converged packages are never blocked; terminal assignment state is
        # already part of the convergence gate via check_spec_package.
        return 0

    gaps = "；".join(detail)
    emit_stop_block(
        f"Spec 收敛门禁未通过（slug {slug}）：差距：{gaps}。"
        f"请继续完成上述未收敛项后再次结束，或运行 "
        f"python3 {CHECK_SCRIPT} --root {project_root} --slug {slug} 查看详情。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
