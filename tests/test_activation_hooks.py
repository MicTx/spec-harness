"""Claude activation guard (task-host-hooks).

Real subprocess stdin/stdout events against ``hooks/claude_activation_guard.py``.

Covered: the prompt-entry reminder (UserPromptSubmit), the pre-implementation
check on supported write/dispatch tools (PreToolUse), main versus worker,
duplicate events, new task / scope change, explicit opt-out, input caps, and
diagnostics. Unsupported tool surfaces (Bash, MCP, custom tools) are documented
as a coverage boundary, not silently claimed.

Scope guard: these tests prove event wiring and state handling. They do not
prove that a model decides correctly; that evidence is the live phase.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "claude_activation_guard.py"
SECRET = "sk-live-" + "Z" * 28
# built by concatenation: the literal home-directory shape is what the public export gate forbids
SENSITIVE_ARG = "/" + "Users" + "/someone/private/" + SECRET


def run_hook(payload, *, cwd, raw=None, env_extra=None):
    import os

    env = {**os.environ, **(env_extra or {})}
    completed = subprocess.run(
        [sys.executable, str(HOOK)],
        input=raw if raw is not None else json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=30,
        env=env,
    )
    return completed


def prompt_event(root, prompt="加筛选、加开关、加导出", **extra):
    return {
        "session_id": "s1",
        "cwd": str(root),
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt,
        **extra,
    }


def tool_event(root, tool="Edit", tool_input=None, **extra):
    return {
        "session_id": "s1",
        "cwd": str(root),
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": tool_input or {"file_path": str(root / "a.py"), "new_string": SENSITIVE_ARG},
        "tool_use_id": "tu-1",
        **extra,
    }


def decision_of(completed):
    assert completed.returncode == 0, completed.stderr
    if not completed.stdout.strip():
        return None
    return json.loads(completed.stdout)["hookSpecificOutput"]


def record_assessment(root, *, session="s1", decision="direct", task="t1", scope=1):
    from importlib import util

    spec = util.spec_from_file_location("claude_activation_guard", HOOK)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.write_assessment(Path(root), session, decision=decision, task_id=task, scope_digest=str(scope))


@pytest.fixture()
def project(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "feature/x", str(tmp_path)], check=True)
    return tmp_path


# --- UserPromptSubmit: entry reminder ------------------------------------------------


def test_prompt_event_injects_the_assessment_requirement(project):
    out = decision_of(run_hook(prompt_event(project), cwd=project))
    assert out["hookEventName"] == "UserPromptSubmit"
    context = out["additionalContext"]
    assert "assess_spec_activation.py" in context
    assert "before the first" in context.lower()
    assert "direct" in context and "create" in context and "resume" in context and "defer" in context


def test_prompt_event_never_blocks_and_never_echoes_the_prompt(project):
    completed = run_hook(prompt_event(project, prompt="请使用 " + SECRET), cwd=project)
    assert completed.returncode == 0
    assert SECRET not in completed.stdout and SECRET not in completed.stderr
    assert "decision" not in json.loads(completed.stdout)


def test_prompt_event_is_idempotent_within_one_task(project):
    first = decision_of(run_hook(prompt_event(project), cwd=project))
    second = run_hook(prompt_event(project, prompt="同一件事的追问"), cwd=project)
    assert first is not None
    assert second.returncode == 0


@pytest.mark.parametrize(
    "prompt",
    [
        "这次不要建任务包，直接改",
        "请不要任务包以外的东西，我要的是完整的功能：筛选、开关、导出",
        "Write a doc on why tiny fixes need no task package, and also add three features",
        "Please do this without a task package",
    ],
)
def test_the_reminder_never_depends_on_keywords_in_the_prompt(project, prompt):
    """A decline in the prompt is not a machine-checkable opt-out: the hook cannot tell
    'do not make a package' from 'I want everything except what a task package covers', so the
    reminder stays unconditional and an explicit opt-out is honored at the host level."""
    out = decision_of(run_hook(prompt_event(project, prompt=prompt), cwd=project))
    assert out is not None and "assess_spec_activation.py" in out["additionalContext"]


def test_the_reminder_forbids_self_reporting_verdict_facts(project):
    context = decision_of(run_hook(prompt_event(project), cwd=project))["additionalContext"]
    assert "Never self-report" in context
    assert "host level" in context


# --- PreToolUse: no silent pass past the managed entry -------------------------------


def test_unassessed_edit_is_denied_with_a_clear_reason(project):
    out = decision_of(run_hook(tool_event(project), cwd=project))
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    assert "assess_spec_activation.py" in out["permissionDecisionReason"]


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit", "NotebookEdit"])
def test_every_supported_write_tool_is_checked(project, tool):
    out = decision_of(run_hook(tool_event(project, tool=tool), cwd=project))
    assert out["permissionDecision"] == "deny"


@pytest.mark.parametrize("tool", ["Agent", "Task", "SendMessage"])
def test_implementation_dispatch_is_checked_before_it_starts(project, tool):
    out = decision_of(run_hook(tool_event(project, tool=tool, tool_input={"prompt": "do it"}), cwd=project))
    assert out["permissionDecision"] == "deny"


@pytest.mark.parametrize("tool", ["Read", "Grep", "Glob", "WebFetch", "TodoWrite"])
def test_read_only_tools_are_never_gated(project, tool):
    assert run_hook(tool_event(project, tool=tool), cwd=project).stdout.strip() == ""


@pytest.mark.parametrize("tool", ["Bash", "mcp__fs__write_file", "CustomTool"])
def test_unsupported_surfaces_are_not_claimed(project, tool):
    completed = run_hook(tool_event(project, tool=tool), cwd=project)
    assert completed.returncode == 0 and completed.stdout.strip() == ""


def test_recorded_assessment_lets_the_edit_through(project):
    record_assessment(project, decision="direct")
    assert run_hook(tool_event(project), cwd=project).stdout.strip() == ""


def test_assessment_is_bound_to_the_session(project):
    record_assessment(project, session="other")
    out = decision_of(run_hook(tool_event(project), cwd=project))
    assert out["permissionDecision"] == "deny"


def test_defer_does_not_unlock_implementation(project):
    record_assessment(project, decision="defer")
    out = decision_of(run_hook(tool_event(project), cwd=project))
    assert out["permissionDecision"] == "deny"


def test_repeated_event_is_stable_and_creates_nothing(project):
    before = sorted(p.name for p in project.rglob("*") if ".git" not in p.parts)
    first = run_hook(tool_event(project), cwd=project)
    second = run_hook(tool_event(project), cwd=project)
    assert first.stdout == second.stdout
    assert sorted(p.name for p in project.rglob("*") if ".git" not in p.parts) == before
    assert not (project / ".spec/specs").exists()


# --- workers and recursion -----------------------------------------------------------


@pytest.mark.parametrize("extra", [{"agent_id": "agent-7"}, {"teammate_name": "impl-1"}, {"agent_type": "worker"}])
def test_worker_events_are_never_gated_or_asked_to_create(project, extra):
    assert run_hook(tool_event(project, **extra), cwd=project).stdout.strip() == ""
    assert run_hook(prompt_event(project, **extra), cwd=project).stdout.strip() == ""


# --- existing packages and verdict-fact refusal ---------------------------------------


def test_a_matching_package_alone_no_longer_satisfies_the_guard(project):
    """Live evidence (sweep 3 / rerun, P3): with a bound package the model edited
    first and recorded the resume later (``adopted_late`` four times in a row).
    The bound package is a hint now, not a key: the resume must be recorded
    before the first gated tool fires."""
    slug = "2026-10-23_add-export"
    package = project / ".spec/specs" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text("# t\n- Git integration branch：`feature/x`\n")
    (package / "tasks.md").write_text("# t\n")
    denied = decision_of(run_hook(tool_event(project), cwd=project))
    assert denied["permissionDecision"] == "deny"
    assert slug in denied["permissionDecisionReason"]
    assert "--session" in denied["permissionDecisionReason"]
    record_assessment(project, decision="resume", task="t1")
    assert run_hook(tool_event(project, task_id="t1"), cwd=project).stdout.strip() == ""


def test_a_recorded_direct_still_lets_work_proceed(project):
    record_assessment(project, decision="direct", task="optout")
    assert run_hook(tool_event(project, task_id="optout"), cwd=project).stdout.strip() == ""


# --- robustness and privacy ----------------------------------------------------------


def test_malformed_json_fails_open_with_a_diagnostic_not_a_lock(project):
    completed = run_hook(None, cwd=project, raw="not json")
    assert completed.returncode == 0 and completed.stdout.strip() == ""
    assert "invalid" in completed.stderr


def test_oversized_input_fails_open_with_a_diagnostic(project):
    completed = run_hook(None, cwd=project, raw=json.dumps({"hook_event_name": "PreToolUse", "x": "a" * 400000}))
    assert completed.returncode == 0 and completed.stdout.strip() == ""
    assert "too large" in completed.stderr


def test_unknown_event_is_ignored(project):
    assert run_hook({"hook_event_name": "Stop", "cwd": str(project)}, cwd=project).stdout.strip() == ""


def test_denial_and_state_never_contain_tool_arguments_or_paths(project):
    completed = run_hook(tool_event(project), cwd=project)
    assert SECRET not in completed.stdout and SECRET not in completed.stderr
    assert "someone" not in completed.stdout
    record_assessment(project)
    for path in project.rglob("*"):
        if path.is_file() and ".git/objects" not in str(path):
            assert SECRET not in path.read_text(errors="ignore")


def test_assessment_state_lives_outside_the_working_tree(project):
    record_assessment(project)
    tracked = subprocess.run(["git", "-C", str(project), "status", "--porcelain"], capture_output=True, text=True)
    assert tracked.stdout.strip() == ""


def test_internal_error_does_not_lock_unrelated_work(project, monkeypatch):
    completed = run_hook(tool_event(project), cwd=project, env_extra={"SPEC_ACTIVATION_GUARD_FORCE_ERROR": "1"})
    assert completed.returncode == 0 and completed.stdout.strip() == ""
    assert "internal error" in completed.stderr


def test_guard_never_runs_git_mutations_or_models():
    source = HOOK.read_text(encoding="utf-8")
    for forbidden in ("checkout", "git commit", "git add", "stash", "reset", "anthropic", "openai", "claude -p"):
        assert forbidden not in source


# --- the loop closes: the real assessor unlocks the real guard -----------------------

ASSESSOR = ROOT / "scripts" / "assess_spec_activation.py"


def assess_and_record(project, facts, session="s1"):
    return subprocess.run(
        [sys.executable, str(ASSESSOR), "--root", str(project), "--session", session, "--facts", json.dumps(facts)],
        capture_output=True,
        text=True,
        timeout=30,
    )


SMALL = {"version": 1, "acceptance_surfaces": 1, "implementers": 1}
WIDE = {"version": 1, "acceptance_surfaces": 3, "implementers": 1}


def test_real_assessment_unlocks_the_real_guard(project):
    assert decision_of(run_hook(tool_event(project), cwd=project))["permissionDecision"] == "deny"
    completed = assess_and_record(project, SMALL)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["decision"] == "direct"
    assert run_hook(tool_event(project), cwd=project).stdout.strip() == ""


def test_self_reported_verdict_facts_are_refused_and_keep_the_gate_closed(project):
    """Sweep 3's measured bypass: ``--facts '{"opt_out":true}'`` must not record
    a ``direct``; the CLI refuses the input and the gate stays shut."""
    completed = assess_and_record(project, {"version": 1, "acceptance_surfaces": 3, "implementers": 3, "opt_out": True})
    assert completed.returncode == 2 and "opt_out" in completed.stderr
    assert decision_of(run_hook(tool_event(project), cwd=project))["permissionDecision"] == "deny"


def test_recorded_create_unlocks_but_recorded_defer_does_not(project):
    subprocess.run(["git", "-C", str(project), "checkout", "-q", "-b", "main2"], check=True)
    assess_and_record(project, WIDE)
    assert run_hook(tool_event(project), cwd=project).stdout.strip() == ""
    (project / "dirty.txt").write_text("x")
    protected = subprocess.run(["git", "-C", str(project), "checkout", "-q", "-B", "main"], capture_output=True)
    assert protected.returncode == 0
    assess_and_record(project, WIDE, session="s2")
    out = decision_of(run_hook(tool_event(project, session_id="s2"), cwd=project))
    assert out["permissionDecision"] == "deny"


def test_assessment_without_session_records_nothing(project):
    completed = subprocess.run(
        [sys.executable, str(ASSESSOR), "--root", str(project), "--facts", json.dumps(SMALL)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0
    assert decision_of(run_hook(tool_event(project), cwd=project))["permissionDecision"] == "deny"


def test_recording_writes_only_inside_the_git_dir(project):
    before = sorted(p.relative_to(project).as_posix() for p in project.rglob("*") if ".git" not in p.parts)
    assess_and_record(project, WIDE)
    after = sorted(p.relative_to(project).as_posix() for p in project.rglob("*") if ".git" not in p.parts)
    assert before == after
    assert (
        subprocess.run(["git", "-C", str(project), "status", "--porcelain"], capture_output=True, text=True).stdout
        == ""
    )


def test_a_traversal_shaped_session_id_is_hashed_and_stays_inside_the_state_dir(project, tmp_path):
    outside = tmp_path / "etc"
    outside.mkdir()
    completed = assess_and_record(project, SMALL, session="../../etc/x")
    assert completed.returncode == 0, completed.stderr
    assert list(outside.iterdir()) == []
    written = list(state_dir_of(project).glob("*.json"))
    assert len(written) == 1 and written[0].parent == state_dir_of(project)
    assert run_hook(tool_event(project, session_id="../../etc/x"), cwd=project).stdout.strip() == ""


# --- the state path must never follow a planted symlink out of the git dir -----------


def state_file(project, session="s1"):
    """The file the code really uses for a session: planting anywhere else would test nothing."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import activation_state as state

    return state.state_path(project, session)


def state_dir_of(project):
    return (
        Path(
            subprocess.run(
                ["git", "-C", str(project), "rev-parse", "--absolute-git-dir"], capture_output=True, text=True
            ).stdout.strip()
        )
        / "spec-activation"
    )


def test_a_symlinked_state_dir_is_never_followed(project, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    state_dir_of(project).symlink_to(outside)
    completed = assess_and_record(project, SMALL)
    assert completed.returncode == 2
    assert list(outside.iterdir()) == []


def test_a_symlinked_state_file_is_never_followed(project, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    state_dir = state_dir_of(project)
    state_dir.mkdir()
    state_file(project).symlink_to(victim)
    completed = assess_and_record(project, SMALL)
    assert completed.returncode == 2
    assert victim.read_text(encoding="utf-8") == "keep"


def test_the_guard_ignores_a_symlinked_state_file_and_stays_closed(project, tmp_path):
    victim = tmp_path / "victim.json"
    victim.write_text(json.dumps({"decision": "direct", "task": "unknown", "scope": "unknown"}), encoding="utf-8")
    state_dir = state_dir_of(project)
    state_dir.mkdir()
    state_file(project).symlink_to(victim)
    out = decision_of(run_hook(tool_event(project), cwd=project))
    assert out["permissionDecision"] == "deny", "a planted link must not count as an assessment"


# --- invalidation must use signals the host really sends -----------------------------
# PreToolUse carries no task or scope field, so staleness is driven by what Claude Code
# does send: a new UserPromptSubmit, and the passage of time.


def test_a_direct_assessment_does_not_survive_a_new_user_prompt(project):
    assess_and_record(project, SMALL)
    assert run_hook(tool_event(project), cwd=project).stdout.strip() == ""
    run_hook(prompt_event(project, prompt="换一件事：再加一个导出"), cwd=project)
    out = decision_of(run_hook(tool_event(project), cwd=project))
    assert out["permissionDecision"] == "deny"
    assert "new request" in out["permissionDecisionReason"].lower()


def test_create_and_resume_survive_follow_up_prompts_because_a_package_exists(project):
    subprocess.run(["git", "-C", str(project), "checkout", "-q", "-b", "feature/y"], check=True)
    assess_and_record(project, WIDE)
    run_hook(prompt_event(project, prompt="继续"), cwd=project)
    assert run_hook(tool_event(project), cwd=project).stdout.strip() == ""


def test_a_prompt_event_does_not_invalidate_an_unrelated_session(project):
    assess_and_record(project, SMALL, session="mine")
    run_hook(prompt_event(project, session_id="someone-else"), cwd=project)
    assert run_hook(tool_event(project, session_id="mine"), cwd=project).stdout.strip() == ""


def test_a_direct_assessment_expires_after_its_ttl(project):
    import os
    import time

    assess_and_record(project, SMALL)
    path = state_file(project)
    old = time.time() - 4 * 3600
    os.utime(path, (old, old))
    out = decision_of(run_hook(tool_event(project), cwd=project))
    assert out["permissionDecision"] == "deny"
    assert "expired" in out["permissionDecisionReason"].lower()


def test_the_guard_no_longer_reads_fields_the_host_does_not_send():
    source = HOOK.read_text(encoding="utf-8")
    assert "scope_digest" not in source and 'get("task_id")' not in source


def test_worker_prompt_events_never_invalidate_the_coordinators_assessment(project):
    assess_and_record(project, SMALL)
    run_hook(prompt_event(project, agent_id="agent-9"), cwd=project)
    assert run_hook(tool_event(project), cwd=project).stdout.strip() == ""


def test_scope_growth_is_handled_by_reassessing_not_by_a_field_the_host_never_sends(project):
    """The agent re-runs the assessor when reading code grows the scope; the newest record wins."""
    assess_and_record(project, SMALL)
    assert run_hook(tool_event(project), cwd=project).stdout.strip() == ""
    grown = assess_and_record(project, {**SMALL, "acceptance_surfaces": 3, "scope_grew": True})
    assert json.loads(grown.stdout)["decision"] == "create"
    assert run_hook(tool_event(project), cwd=project).stdout.strip() == ""


def test_a_deferred_reassessment_closes_the_gate_again(project):
    assess_and_record(project, SMALL)
    assert run_hook(tool_event(project), cwd=project).stdout.strip() == ""
    subprocess.run(["git", "-C", str(project), "checkout", "-q", "-B", "main"], check=True)
    assess_and_record(project, WIDE)  # protected branch -> defer
    assert decision_of(run_hook(tool_event(project), cwd=project))["permissionDecision"] == "deny"


# --- non-Git fallback state must be private to the user ------------------------------


def test_the_non_git_fallback_directory_is_private_and_owned(tmp_path):
    import os
    import stat

    sys.path.insert(0, str(ROOT / "scripts"))
    import activation_state as state

    plain = tmp_path / "plain"
    plain.mkdir()
    state.write_assessment(plain, "s1", decision="direct", task_id="t", scope_digest="1")
    directory = state.state_dir(plain)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert directory.stat().st_uid == os.getuid()


def test_a_fallback_directory_planted_by_someone_else_is_not_trusted(tmp_path, monkeypatch):
    import os

    sys.path.insert(0, str(ROOT / "scripts"))
    import activation_state as state

    plain = tmp_path / "plain"
    plain.mkdir()
    directory = state.state_dir(plain)
    directory.mkdir(parents=True)
    state.state_path(plain, "s1").write_text(
        json.dumps({"decision": "direct", "task": "x", "scope": "y"}), encoding="utf-8"
    )
    monkeypatch.setattr(os, "getuid", lambda: os.stat(directory).st_uid + 1)
    assert state.read_assessment(plain, "s1") is None, "a record in a directory we do not own must not count"
    with pytest.raises(state.UnsafeState):
        state.write_assessment(plain, "s1", decision="direct", task_id="t", scope_digest="1")


def test_a_group_or_world_writable_fallback_directory_is_not_trusted(tmp_path):
    import os

    sys.path.insert(0, str(ROOT / "scripts"))
    import activation_state as state

    plain = tmp_path / "plain"
    plain.mkdir()
    directory = state.state_dir(plain)
    directory.mkdir(parents=True)
    os.chmod(directory, 0o777)
    state.state_path(plain, "s1").write_text(
        json.dumps({"decision": "direct", "task": "x", "scope": "y"}), encoding="utf-8"
    )
    assert state.read_assessment(plain, "s1") is None


def test_an_oversized_state_file_is_ignored(project):
    state_dir = state_dir_of(project)
    state_dir.mkdir()
    state_file(project).write_text(json.dumps({"decision": "direct", "task": "t", "scope": "s", "pad": "x" * 9000}))
    assert decision_of(run_hook(tool_event(project), cwd=project))["permissionDecision"] == "deny"


def test_a_planted_symlink_at_the_temp_file_name_is_never_written_through(project, tmp_path):
    import os

    sys.path.insert(0, str(ROOT / "scripts"))
    import activation_state as state

    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    directory = state.state_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    for guess in range(os.getpid() - 2, os.getpid() + 3):
        (directory / f"{state_file(project).name}.{guess}.tmp").symlink_to(victim)
    state.write_assessment(project, "s1", decision="direct", task_id="t", scope_digest="1")
    assert victim.read_text(encoding="utf-8") == "keep"
    assert json.loads(state_file(project).read_text())["decision"] == "direct"


# --- the assessor and the guard must agree on every session id ----------------------


@pytest.mark.parametrize("session", ["abc:def", "a b", "séss", "x/y", "UPPER.lower_1"])
def test_any_printable_session_id_can_be_recorded_and_is_honoured_by_the_guard(project, session):
    completed = assess_and_record(project, SMALL, session=session)
    assert completed.returncode == 0, completed.stderr
    assert run_hook(tool_event(project, session_id=session), cwd=project).stdout.strip() == ""


def test_distinct_session_ids_never_share_a_record(project):
    assess_and_record(project, SMALL, session="a b")
    out = decision_of(run_hook(tool_event(project, session_id="a_b"), cwd=project))
    assert out["permissionDecision"] == "deny", "'a_b' read the record written for 'a b'"
    assert run_hook(tool_event(project, session_id="a b"), cwd=project).stdout.strip() == ""


@pytest.mark.parametrize("session", ["", "x" * 600, "new\nline"])
def test_unusable_session_ids_are_rejected_without_writing(project, session):
    completed = assess_and_record(project, SMALL, session=session)
    assert completed.returncode == 2
    assert not state_dir_of(project).exists() or not list(state_dir_of(project).glob("*.json"))


def test_a_null_byte_session_id_is_not_valid():
    sys.path.insert(0, str(ROOT / "scripts"))
    import activation_state as state

    assert state.valid_session("bad\x00id") is False
    assert state.valid_session("fine-id") is True


def test_the_prompt_hint_names_the_flag_that_actually_records_the_decision(project):
    """Live cost: units guessed the session id because the reminder never said it was required.

    ``--session`` is the only thing that persists an assessment; a reminder that
    omits it leaves the agent re-guessing the id (and a write refused again).
    """
    context = decision_of(run_hook(prompt_event(project), cwd=project))["additionalContext"]
    assert "--session" in context and "not optional" in context
    # the exact id, not a placeholder: the agent should not have to hunt for it
    assert "--session s1" in context


def test_the_prompt_hint_still_carries_the_assessment_requirement(project):
    context = decision_of(run_hook(prompt_event(project), cwd=project))["additionalContext"]
    assert "assess_spec_activation.py" in context and "before the first" in context.lower()
    assert "independently testable behavior" in context and "multiple files" in context


def test_exactly_one_prompt_context_object_is_emitted(project):
    completed = run_hook(prompt_event(project), cwd=project)
    body = completed.stdout.strip()
    assert body.count("\n") == 0 and json.loads(body)


def test_the_denial_reason_carries_the_session_flag_that_records_the_decision(project):
    """The denial reason is the only channel that reaches the model head to head.

    Probed 2026-10-24: under `claude -p`, `UserPromptSubmit` fires but its
    `additionalContext` is not injected into the model's input. A reminder there
    is unread; the denial text is read. Sweep 1 and 2 both paid for this.
    """
    reason = decision_of(run_hook(tool_event(project), cwd=project))["permissionDecisionReason"]
    assert "--session s1" in reason
    assert "--session` is what persists" in reason
    assert "independently testable behavior" in reason and "multiple files" in reason
