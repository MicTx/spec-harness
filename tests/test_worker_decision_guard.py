"""Real controlled workers cannot turn edits into human decisions."""

import json
import os
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import autoplan_spawn
import autorun_spawn
import chain_spawn_support
import worker_decision_guard
from worker_decision_guard import DecisionGuard, DecisionGuardError, decision_guard_errors, worker_argv

from tests.test_autonomous_questions import SLUG, make_spec, package_fixture, route
from tests.test_spec_verify import init_repo

ROOT = Path(__file__).resolve().parents[1]
QUESTION = "[ ] [decision] Choose service tier | reason: preference"
CLOSED = "[x] [decision] Choose service tier | reason: preference | answer: free | authority: user:claimed approval"
MUTATIONS = {"close": CLOSED, "downgrade": "[ ] [fact] Choose tier | task: task-inspect", "delete": "无"}


@pytest.mark.parametrize("chain", ["autoplan", "autorun"])
@pytest.mark.parametrize("backend", ["terminal", "tmux"])
@pytest.mark.parametrize("mutation", MUTATIONS)
@pytest.mark.parametrize("question", [QUESTION, "[ ] Choose service tier"], ids=["decision", "unclassified"])
def test_fresh_worker_decision_changes_are_abnormal_and_route_stays_blocked(
    tmp_path, chain, backend, mutation, question
):
    init_repo(tmp_path)
    package = package_fixture(tmp_path, question)
    replacement = "[x] Choose service tier" if question != QUESTION and mutation == "close" else MUTATIONS[mutation]
    script = (
        "import json,sys; from pathlib import Path; "
        f"path = Path({str(package / 'spec.md')!r}); "
        f"path.write_text(path.read_text().replace({question!r}, {replacement!r})); "
        f"sys.path.insert(0, {str(ROOT / 'scripts')!r}); "
        "from route_spec_package import route_for_package; "
        f"print(route_for_package(Path.cwd(), {SLUG!r}, output_format='json'))"
    )
    worker = [sys.executable, "-B", "-c", script]
    if backend == "tmux":
        _launch, command, _name = chain_spawn_support.build_tmux_command(tmp_path, worker, chain)
    else:
        _launch, command = chain_spawn_support.build_terminal_command(tmp_path, worker)
    completed = subprocess.run(["/bin/sh", "-c", command], cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "decision 问题发生变化" in completed.stderr
    during = json.loads(completed.stdout)
    assert during["stage"] == "status"
    assert any("受控 worker" in error for error in during["questionErrors"])
    records = list((tmp_path / ".git/spec-worker-decisions").glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record["status"] == "abnormal" and record["exit"] == -1
    assert route(tmp_path)["stage"] == "status"
    (package / "spec.md").write_text(make_spec(CLOSED))
    assert route(tmp_path)["stage"] == "status"


@pytest.mark.parametrize("replacement", ["~~Choose service tier~~ → 已解决。", "无"])
def test_legacy_v1_question_cannot_be_closed_or_deleted_by_worker(tmp_path, replacement):
    init_repo(tmp_path)
    package = package_fixture(tmp_path, "Choose service tier")
    path = package / "spec.md"
    path.write_text(path.read_text().replace("<!-- spec-format: 2 -->\n", ""))
    guard = DecisionGuard(tmp_path)
    path.write_text(path.read_text().replace("- Choose service tier", f"- {replacement}"))
    with pytest.raises(DecisionGuardError):
        guard.finish()
    assert route(tmp_path)["stage"] == "status"


@pytest.mark.parametrize("module", [autoplan_spawn, autorun_spawn], ids=["autoplan", "autorun"])
def test_fresh_spawn_rejects_prior_decision_violation(tmp_path, monkeypatch, module):
    init_repo(tmp_path)
    package = package_fixture(tmp_path, QUESTION)
    guard = DecisionGuard(tmp_path)
    (package / "spec.md").write_text(make_spec(CLOSED))
    with pytest.raises(DecisionGuardError):
        guard.finish()
    launch = Mock(side_effect=AssertionError("must refuse before launching"))
    monkeypatch.setattr(module, "run_terminal_open", launch)
    args = ["spawn", "--root", str(tmp_path), "--host", "pi"]
    if module is autoplan_spawn:
        args.extend(["--next", "framework"])
    assert module.main(args) == 1
    launch.assert_not_called()


def test_human_decision_between_worker_invocations_is_accepted(tmp_path):
    init_repo(tmp_path)
    package = package_fixture(tmp_path, QUESTION)
    command = worker_argv(tmp_path, [sys.executable, "-B", "-c", "print('verified')"])
    first = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert first.returncode == 0, first.stderr
    (package / "spec.md").write_text(make_spec(CLOSED))
    second = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert second.returncode == 0, second.stderr
    assert not decision_guard_errors(tmp_path)
    assert route(tmp_path)["stage"] == "run"
    assert not list((tmp_path / ".git/spec-worker-decisions").glob("*.json"))


def test_user_can_recover_after_stopped_worker_and_explicit_record_review(tmp_path):
    init_repo(tmp_path)
    package = package_fixture(tmp_path, QUESTION)
    guard = DecisionGuard(tmp_path)
    (package / "spec.md").write_text(make_spec(CLOSED))
    with pytest.raises(DecisionGuardError):
        guard.finish()
    assert route(tmp_path)["stage"] == "status"
    guard.path.unlink()
    assert not decision_guard_errors(tmp_path)
    assert route(tmp_path)["stage"] == "run"


@pytest.mark.parametrize("new_package", [False, True])
@pytest.mark.parametrize("closed", [False, True])
@pytest.mark.parametrize("classified", [False, True], ids=["unclassified", "decision"])
def test_worker_may_raise_but_cannot_self_approve_new_decisions(tmp_path, new_package, closed, classified):
    init_repo(tmp_path)
    package = None if new_package else package_fixture(tmp_path, "无")
    guard = DecisionGuard(tmp_path)
    if package is None:
        package = package_fixture(tmp_path, "无")
    question = CLOSED if closed else QUESTION
    if not classified:
        question = f"[{'x' if closed else ' '}] Choose service tier"
    (package / "spec.md").write_text(make_spec(question))
    if closed:
        with pytest.raises(DecisionGuardError):
            guard.finish()
        assert decision_guard_errors(tmp_path)
    else:
        assert not decision_guard_errors(tmp_path)
        guard.finish()
        assert not decision_guard_errors(tmp_path)
    assert route(tmp_path)["stage"] == "status"


@pytest.mark.parametrize("chain", ["autoplan", "autorun"])
@pytest.mark.parametrize("backend", ["terminal", "tmux"])
@pytest.mark.parametrize("kind", ["fact", "reversible"])
@pytest.mark.parametrize("sensitive", [False, True], ids=["local-inquiry", "sensitive-action"])
def test_fresh_worker_new_inquiry_routes_without_self_authorizing(tmp_path, chain, backend, kind, sensitive):
    init_repo(tmp_path)
    package = package_fixture(tmp_path, "无")
    action = "上传结果" if sensitive else "Inspect local format"
    question = f"[ ] [{kind}] {action} | task: task-inspect"
    script = f"from pathlib import Path; Path({str(package / 'spec.md')!r}).write_text({make_spec(question)!r})"
    worker = [sys.executable, "-B", "-c", script]
    if backend == "tmux":
        _launch, command, _name = chain_spawn_support.build_tmux_command(tmp_path, worker, chain)
    else:
        _launch, command = chain_spawn_support.build_terminal_command(tmp_path, worker)
    completed = subprocess.run(["/bin/sh", "-c", command], cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not decision_guard_errors(tmp_path)
    result = route(tmp_path)
    assert result["stage"] == ("status" if sensitive else "run")
    if sensitive:
        assert "敏感动作" in json.dumps(result, ensure_ascii=False)
    else:
        assert "自主调查" in json.dumps(result, ensure_ascii=False)
        assert "需要确认需求" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("backend", ["terminal", "tmux"])
def test_main_session_can_classify_legacy_question_before_dispatch(tmp_path, backend):
    init_repo(tmp_path)
    package = package_fixture(tmp_path, "[ ] Inspect local format")
    assert route(tmp_path)["stage"] == "status"
    (package / "spec.md").write_text(make_spec("[ ] [fact] Inspect local format | task: task-inspect"))
    assert route(tmp_path)["stage"] == "run"
    worker = [sys.executable, "-B", "-c", "print('inquiry worker started')"]
    if backend == "tmux":
        _launch, command, _name = chain_spawn_support.build_tmux_command(tmp_path, worker, "autoplan")
    else:
        _launch, command = chain_spawn_support.build_terminal_command(tmp_path, worker)
    completed = subprocess.run(["/bin/sh", "-c", command], cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not decision_guard_errors(tmp_path)
    assert route(tmp_path)["stage"] == "run"


@pytest.mark.parametrize("change_decision", [False, True])
def test_archiving_preserves_decisions_but_cannot_hide_mutation(tmp_path, change_decision):
    init_repo(tmp_path)
    package = package_fixture(tmp_path, CLOSED)
    guard = DecisionGuard(tmp_path)
    archive = package.parent / "archive"
    archive.mkdir()
    destination = archive / package.name
    shutil.move(str(package), destination)
    if change_decision:
        (destination / "spec.md").write_text(make_spec("无"))
        with pytest.raises(DecisionGuardError):
            guard.finish()
        assert decision_guard_errors(tmp_path)
    else:
        guard.finish()
        assert not decision_guard_errors(tmp_path)


def test_custom_specs_root_verify_guard_cannot_be_bypassed(tmp_path):
    init_repo(tmp_path)
    package = package_fixture(tmp_path, QUESTION)
    package.parent.parent.rename(tmp_path / "records")
    package = tmp_path / "records/specs" / SLUG
    guard = DecisionGuard(tmp_path, "records")
    (package / "spec.md").write_text(make_spec(CLOSED))
    assert decision_guard_errors(tmp_path)
    with pytest.raises(DecisionGuardError):
        guard.finish()


def test_git_initialization_does_not_hide_a_nongit_worker_guard(tmp_path):
    package = package_fixture(tmp_path, QUESTION)
    guard = DecisionGuard(tmp_path)
    assert guard.path.parent == tmp_path / ".spec/.worker-decision-guards"
    init_repo(tmp_path)
    (package / "spec.md").write_text(make_spec(CLOSED))
    assert decision_guard_errors(tmp_path)
    with pytest.raises(DecisionGuardError):
        guard.finish()
    assert route(tmp_path)["stage"] == "status"


def test_unchanged_concurrent_guards_do_not_block_each_other(tmp_path):
    init_repo(tmp_path)
    package_fixture(tmp_path, QUESTION)
    first, second = DecisionGuard(tmp_path), DecisionGuard(tmp_path)
    assert not decision_guard_errors(tmp_path)
    first.finish()
    assert not decision_guard_errors(tmp_path)
    second.finish()
    assert not decision_guard_errors(tmp_path)


@pytest.mark.parametrize("change_decision", [False, True], ids=["unchanged", "mutated"])
def test_sigkill_wrapper_leaves_guard_blocking_route_check_and_spawn(tmp_path, monkeypatch, change_decision):
    init_repo(tmp_path)
    package = package_fixture(tmp_path, QUESTION if change_decision else "无")
    mutation = (
        f"path=Path({str(package / 'spec.md')!r}); path.write_text(path.read_text().replace({QUESTION!r},{CLOSED!r})); "
        if change_decision
        else ""
    )
    command = [
        sys.executable,
        "-B",
        "-c",
        "import signal; from pathlib import Path; " + mutation + "print('ready', flush=True); signal.pause()",
    ]
    process = subprocess.Popen(
        worker_argv(tmp_path, command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10)
            assert process.stdout.readline().strip() == "ready"
        paths = list((tmp_path / ".git/spec-worker-decisions").glob("*.json"))
        assert len(paths) == 1
        assert json.loads(paths[0].read_text())["status"] == "running"
        if not change_decision:
            assert not decision_guard_errors(tmp_path)
        process.kill()
        assert process.wait(timeout=5) == -signal.SIGKILL
        assert json.loads(paths[0].read_text())["status"] == "running"
        assert decision_guard_errors(tmp_path)
        result = route(tmp_path)
        assert result["stage"] == "status"
        assert any("受控 worker" in error for error in result["questionErrors"])
        from check_spec_package import compute_gate_results

        gates = compute_gate_results(
            (package / "spec.md").read_text(),
            (package / "tasks.md").read_text(),
            (package / "checklist.md").read_text(),
            root=tmp_path,
            package_dir=package,
        )
        assert not gates.assumptions_ok
        for module in (autoplan_spawn, autorun_spawn):
            launch = Mock(side_effect=AssertionError("must refuse before launching"))
            monkeypatch.setattr(module, "run_terminal_open", launch)
            args = ["spawn", "--root", str(tmp_path), "--host", "pi"]
            if module is autoplan_spawn:
                args.extend(["--next", "framework"])
            assert module.main(args) == 1
            launch.assert_not_called()
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate(timeout=5)


def test_unreadable_guard_fails_closed_without_starting_worker(tmp_path):
    init_repo(tmp_path)
    directory = tmp_path / ".git/spec-worker-decisions"
    directory.mkdir()
    (directory / "invalid.json").write_text("{")
    result = subprocess.run(
        worker_argv(tmp_path, [sys.executable, "-c", "raise AssertionError('must not execute')"]),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1
    assert "不可验证" in result.stderr
    assert "AssertionError" not in result.stderr


@pytest.mark.parametrize("damage", ["missing-lock", "symlink-lock", "legacy-record"])
def test_unverifiable_running_guard_fails_closed(tmp_path, damage):
    guard = DecisionGuard(tmp_path)
    try:
        if damage == "legacy-record":
            guard.path.write_text(json.dumps({**guard.record, "version": 1}))
        else:
            guard.lock_path.unlink()
            if damage == "symlink-lock":
                target = tmp_path / "untrusted-lock"
                target.touch()
                guard.lock_path.symlink_to(target)
        assert decision_guard_errors(tmp_path)
        with pytest.raises(DecisionGuardError):
            DecisionGuard(tmp_path)
    finally:
        guard.finish()


@pytest.mark.parametrize("backend", ["terminal", "tmux"])
@pytest.mark.parametrize("host", ["pi", "claude", "codex"])
@pytest.mark.parametrize("module", [autoplan_spawn, autorun_spawn], ids=["autoplan", "autorun"])
def test_every_fresh_builder_output_crosses_decision_guard(
    tmp_path, monkeypatch, codex_reasoning_catalog, backend, host, module
):
    import reasoning_policy

    identity = {"id": "test-model"} if host == "codex" else None
    command = module.build_worker_command(host, tmp_path, "inspect local fixture", model_identity=identity)
    received = []

    def capture_guard(root, argv):
        received.append((root, argv))
        return ["guarded-launch", *argv]

    monkeypatch.setattr(chain_spawn_support, "worker_argv", capture_guard)
    if backend == "tmux":
        _launch, shell_command, _name = chain_spawn_support.build_tmux_command(tmp_path, command, "autoplan")
    else:
        _launch, shell_command = chain_spawn_support.build_terminal_command(tmp_path, command)
    assert received == [(tmp_path, reasoning_policy.worker_launch_argv(command))]
    assert "guarded-launch" in shlex.split(shell_command)


def test_worker_signal_propagation_keeps_command_identity(tmp_path):
    command = [sys.executable, "-B", "-c", "import os,signal; os.kill(os.getpid(), signal.SIGTERM)"]
    result = subprocess.run(worker_argv(tmp_path, command), capture_output=True, text=True, timeout=15)
    assert result.returncode == 143, result.stderr
    assert not decision_guard_errors(tmp_path)
    assert shlex.split(shlex.join(worker_argv(tmp_path, command)))[-1] == command[-1]


@pytest.mark.parametrize("ignores_termination", [False, True])
def test_termination_near_poll_deadline_gets_its_own_bounded_grace(tmp_path, monkeypatch, ignores_termination):
    clock = [20.0]
    handlers = {}
    child = Mock()
    child.returncode = None
    child.poll.side_effect = lambda: child.returncode
    waits = []

    def set_handler(signum, handler):
        previous = handlers.get(signum, signal.SIG_DFL)
        handlers[signum] = handler
        return previous

    def wait(timeout):
        waits.append(timeout)
        if len(waits) == 1:
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            raise subprocess.TimeoutExpired("worker", timeout)
        if ignores_termination and len(waits) == 2:
            clock[0] += worker_decision_guard.TERMINATION_GRACE_SECONDS
            raise subprocess.TimeoutExpired("worker", timeout)
        child.returncode = -9 if ignores_termination else 42
        return child.returncode

    child.wait.side_effect = wait
    monkeypatch.setattr(worker_decision_guard.subprocess, "Popen", lambda *_args, **_kwargs: child)
    monkeypatch.setattr(worker_decision_guard.signal, "signal", set_handler)
    monkeypatch.setattr(worker_decision_guard.time, "monotonic", lambda: clock[0])
    assert worker_decision_guard.run_worker(tmp_path, ["worker"]) == (137 if ignores_termination else 42)
    child.send_signal.assert_called_once_with(signal.SIGTERM)
    assert child.kill.call_count == int(ignores_termination)
    assert not decision_guard_errors(tmp_path)


def test_wrapper_forwards_termination_to_running_worker(tmp_path):
    command = [
        sys.executable,
        "-B",
        "-c",
        "import signal,sys; "
        "signal.signal(signal.SIGTERM, lambda *_: sys.exit(42)); "
        "print('ready', flush=True); signal.pause()",
    ]
    process = subprocess.Popen(
        worker_argv(tmp_path, command), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10)
            assert process.stdout.readline().strip() == "ready"
        process.terminate()
        _stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 42, stderr
        assert not decision_guard_errors(tmp_path)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
