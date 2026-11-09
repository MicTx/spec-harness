#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
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
outside Git), and run ``scripts/check_spec_package.py --format json``.
Block only while tasks are unfinished or an open ``[decision]`` awaits the
user; other gate gaps stay in the reason as advisory. Ambiguous bindings fail
closed; projects without a Spec tree are never touched.
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
        return None, f"Cannot bind a task package to the current worktree branch: {exc}"
    if selection.slug:
        return selection.slug, ""
    return None, selection.warning


def run_convergence_check(project_root: Path, slug: str) -> tuple[dict[str, object] | None, str]:
    """Return the checker's convergence payload, or an error detail."""
    if not CHECK_SCRIPT.is_file():
        return None, f"check script missing: {CHECK_SCRIPT}"
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
        return None, f"checker error: {exc}"
    # Exit code reflects the overall gate, not convergence; parse the JSON
    # body either way and trust only the structured convergence fields.
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, ValueError):
        detail = (completed.stderr or completed.stdout).strip()
        return None, f"checker produced no json (exit {completed.returncode}): {detail[:400]}"
    if not isinstance(payload, dict) or not isinstance(payload.get("converged"), bool):
        return None, "checker json missing boolean converged field"
    gaps = payload.get("gaps")
    if not isinstance(gaps, list) or not all(isinstance(gap, str) for gap in gaps):
        return None, "checker json gaps field is not a string list"
    decisions = payload.get("openDecisions")
    if not isinstance(decisions, list) or not all(isinstance(item, str) for item in decisions):
        return None, "checker json openDecisions field is not a string list"
    if not isinstance(payload.get("tasksDone"), bool):
        return None, "checker json missing boolean tasksDone field"
    return payload, ""


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

    state, detail = run_convergence_check(project_root, slug)
    if state is None:
        # A Spec project whose convergence checker cannot run is treated as
        # not converged: block once with the failure so the agent surfaces it.
        emit_stop_block(
            f"Spec convergence guard could not run the check (slug {slug}): {detail}. "
            f"Fix the check environment or run python3 {CHECK_SCRIPT} --root {project_root} "
            f"--slug {slug} --format json for details."
        )
        return 0
    if state["converged"] is True:
        return 0
    # Block only on unfinished/blocked tasks or an open [decision] that needs
    # user authority; every other gate gap is advisory in the reason list.
    if state["tasksDone"] is True and not state["openDecisions"]:
        return 0

    gaps = state["gaps"]
    emit_stop_block(
        f"Spec convergence gate failed (slug {slug}). Gaps: {'; '.join(gaps)}. "
        "Resolve the unconverged items before stopping, or run "
        f"python3 {CHECK_SCRIPT} --root {project_root} --slug {slug} for details."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
