"""Portable session dispatch: Linux/WSL use tmux, macOS keeps Terminal."""

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import autoplan_spawn as autoplan
import autorun_spawn as autorun
import chain_spawn_support as support


@pytest.fixture(params=[autoplan, autorun], ids=["autoplan", "autorun"])
def entry(request, tmp_path, monkeypatch):
    module = request.param
    plan = tmp_path / ".spec/plans/README.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# plan\n- [ ] feature\n", encoding="utf-8")
    package = tmp_path / ".spec/specs/2026-01-01_demo/spec.md"
    package.parent.mkdir(parents=True)
    package.write_text("# demo\n", encoding="utf-8")
    monkeypatch.setattr(module, "resolve_worker_host", lambda _: ("pi", "explicit"))
    monkeypatch.setattr(module, "run_terminal_open", Mock(side_effect=AssertionError("AppleScript on Linux")))
    args = ["spawn", "--root", str(tmp_path), "--host", "pi", "--format", "json"]
    if module is autoplan:
        args += ["--next", "framework"]
    return module, args, tmp_path / ".spec" / ("autoplan" if module is autoplan else "autorun")


@pytest.mark.parametrize("system,expected", [("Darwin", "terminal"), ("Linux", "tmux")])
def test_backend_auto(system, expected, monkeypatch):
    monkeypatch.setattr(support.platform, "system", lambda: system)
    assert support.resolve_terminal_backend("auto") == expected


def test_windows_guidance_without_import_crash(entry, monkeypatch, capsys):
    module, args, chain = entry
    monkeypatch.setattr(support.platform, "system", lambda: "Windows")
    assert module.main(args) == 1
    assert "WSL2" in capsys.readouterr().err
    assert not (chain / "chain.json").exists()


def test_linux_dry_run(entry, monkeypatch, capsys):
    module, args, chain = entry
    monkeypatch.setattr(support.platform, "system", lambda: "Linux")
    assert module.main(args + ["--dry-run"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["terminal_backend"] == "tmux"
    assert result["launch_argv"][0] == "tmux"
    assert "attach-session" in result["attach_command"]
    assert "osascript" not in result
    assert not (chain / "chain.json").exists()


def test_missing_tmux_actionable(entry, monkeypatch, capsys):
    module, args, chain = entry
    monkeypatch.setattr(support.platform, "system", lambda: "Linux")
    monkeypatch.setattr(support.shutil, "which", lambda _: None)
    assert module.main(args) == 1
    assert "install tmux" in capsys.readouterr().err
    assert not (chain / "chain.json").exists()


@pytest.mark.parametrize("failure", [False, True])
def test_linux_commit_or_rollback(entry, monkeypatch, capsys, failure):
    module, args, chain = entry
    monkeypatch.setattr(support.platform, "system", lambda: "Linux")
    open_session = Mock(return_value=None)
    close_session = Mock(return_value={"status": "closed"})
    monkeypatch.setattr(support, "open_tmux_session", open_session)
    monkeypatch.setattr(support, "close_tmux_session", close_session)
    if failure:
        monkeypatch.setattr(support, "atomic_write", Mock(side_effect=OSError("disk full")))
    assert module.main(args) == int(failure)
    if failure:
        assert "disk full" in capsys.readouterr().err
        assert not (chain / "chain.json").exists()
        close_session.assert_called_once()
    else:
        result = json.loads(capsys.readouterr().out)
        state = json.loads((chain / "chain.json").read_text())
        assert state["terminal_backend"] == "tmux"
        assert state["tmux_session"] == result["tmux_session"]
        assert state["window_recycle"]["status"] == "skipped"
        assert "model_injection" not in state
        close_session.assert_not_called()
    open_session.assert_called_once()


def test_launch_quoting(tmp_path):
    argv = ["pi", "--", "a 'quote' $HOME; echo unsafe\n中文"]
    launch, command, name = support.build_tmux_command(tmp_path / "a b'c", argv, "autoplan")
    assert shlex.split(launch[-1]) == ["exec", *argv]
    assert launch[launch.index("-c") + 1] == str(tmp_path / "a b'c")
    assert name.startswith("spec-autoplan-")
    assert command == launch[-1]


def test_explicit_terminal_refused_on_linux(monkeypatch):
    monkeypatch.setattr(support.platform, "system", lambda: "Linux")
    with pytest.raises(support.ChainSpawnError, match="macOS"):
        support.resolve_terminal_backend("terminal")


@pytest.mark.skipif(not shutil.which("tmux"), reason="tmux integration dependency unavailable")
def test_real_tmux_start_and_stop(tmp_path):
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]
    launch, _, name = support.build_tmux_command(tmp_path, argv, "autoplan")
    try:
        support.open_tmux_session(launch, name)
        probe = subprocess.run(["tmux", "-L", name, "has-session", "-t", "=" + name], capture_output=True)
        assert probe.returncode == 0
    finally:
        support.close_tmux_session(name)
    assert subprocess.run(["tmux", "-L", name, "has-session", "-t", "=" + name], capture_output=True).returncode != 0


@pytest.mark.skipif(not shutil.which("tmux"), reason="tmux integration dependency unavailable")
def test_real_tmux_dead_worker_refused(tmp_path):
    launch, _, name = support.build_tmux_command(tmp_path, [sys.executable, "-c", "raise SystemExit(1)"], "autoplan")
    try:
        with pytest.raises(support.ChainSpawnError, match="exited"):
            support.open_tmux_session(launch, name)
    finally:
        support.close_tmux_session(name)


@pytest.mark.parametrize("failure", ["launch", "timeout", "probe", "oserror"])
def test_open_errors_are_bounded(failure, tmp_path, monkeypatch):
    monkeypatch.setattr(support.shutil, "which", lambda _: "/usr/bin/tmux")
    monkeypatch.setattr(support.time, "sleep", lambda _: None)
    launch, _, name = support.build_tmux_command(tmp_path, ["pi"], "autorun")
    ok = subprocess.CompletedProcess([], 0, "", "")
    fail = subprocess.CompletedProcess([], 1, "", "failed")
    effects = {
        "launch": [fail],
        "timeout": [subprocess.TimeoutExpired("tmux", 10)],
        "probe": [ok, subprocess.CompletedProcess([], 0, "bad reply", "")],
        "oserror": [OSError("missing executable")],
    }
    run = Mock(side_effect=effects[failure])
    monkeypatch.setattr(support.subprocess, "run", run)
    with pytest.raises(support.ChainSpawnError):
        support.open_tmux_session(launch, name)
    assert all(call.kwargs["timeout"] == support.TMUX_TIMEOUT_SECONDS for call in run.call_args_list)


def test_failed_launch_keeps_previous_state(entry, monkeypatch, capsys):
    module, args, directory = entry
    monkeypatch.setattr(support.platform, "system", lambda: "Linux")
    directory.mkdir(parents=True)
    previous = {"pass" if module is autoplan else "round": 3, "host": "pi"}
    state_file = directory / "chain.json"
    state_file.write_text(json.dumps(previous), encoding="utf-8")
    monkeypatch.setattr(support, "open_tmux_session", Mock(side_effect=support.ChainSpawnError("launch refused")))
    close = Mock(return_value={"status": "closed"})
    monkeypatch.setattr(support, "close_tmux_session", close)
    assert module.main(args) == 1
    assert "launch refused" in capsys.readouterr().err
    assert json.loads(state_file.read_text()) == previous
    assert not (directory / "spawns.jsonl").exists()
    close.assert_called_once()
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    assert {event["kind"] for event in events} == {"refusal_close", "spawn_refusal"}


def test_audit_failure_does_not_rollback_accepted_worker(entry, monkeypatch, capsys):
    module, args, directory = entry
    monkeypatch.setattr(support.platform, "system", lambda: "Linux")
    monkeypatch.setattr(support, "open_tmux_session", Mock())
    close = Mock()
    monkeypatch.setattr(support, "close_tmux_session", close)
    monkeypatch.setattr(support, "append_audit", Mock(side_effect=OSError("audit unavailable")))
    assert module.main(args) == 1
    assert "handoff committed" in capsys.readouterr().err
    assert (directory / "chain.json").exists()
    close.assert_not_called()


def test_tmux_backend_persists_and_status_shows_attach(entry, monkeypatch, capsys):
    module, args, directory = entry
    monkeypatch.setattr(support.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(support, "open_tmux_session", Mock())
    assert module.main(args + ["--terminal", "tmux"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert module.main(args + ["--dry-run"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["terminal_backend"] == "tmux"
    assert second["tmux_session"] != first["tmux_session"]
    assert module.main(["status", "--root", str(directory.parent.parent)]) == 0
    assert first["attach_command"] in capsys.readouterr().out


def test_no_fcntl_import_and_lock_guidance(tmp_path):
    # Separate interpreter faithfully models an unavailable POSIX-only module.
    scripts = str(Path(support.__file__).parent)
    code = (
        "import sys; sys.modules['fcntl'] = None; sys.path.insert(0, sys.argv[1]); "
        "import autoplan_spawn, autorun_spawn, chain_spawn_support as s; "
        "assert s.fcntl is None; "
        "assert s.lock_holder(__import__('pathlib').Path(sys.argv[2]))['status'] == 'unknown'; "
        "assert autoplan_spawn.main(['status', '--root', sys.argv[2]]) == 0; "
        "assert autorun_spawn.main(['status', '--root', sys.argv[2]]) == 0; "
        "s.platform.system = lambda: 'Windows'; "
        "assert autoplan_spawn.main(['spawn', '--root', sys.argv[2], '--next', 'framework']) == 1; "
        "autoplan_spawn.main(['--help'])"
    )
    result = subprocess.run([sys.executable, "-c", code, scripts, str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_unavailable_fcntl_lock_fails_clearly(tmp_path, monkeypatch):
    monkeypatch.setattr(support, "fcntl", None)
    with pytest.raises(support.ChainSpawnError, match="WSL2"):
        with support.single_chain_lock(tmp_path, "autoplan"):
            pytest.fail("must not acquire an unprotected lock")


@pytest.mark.parametrize("system", ["Windows", "MINGW64_NT", "CYGWIN_NT"])
def test_native_windows_backend_override_still_refused(system, monkeypatch):
    monkeypatch.setattr(support.platform, "system", lambda: system)
    with pytest.raises(support.ChainSpawnError, match="WSL2"):
        support.resolve_terminal_backend("tmux")


def test_custom_command_is_only_explicit_raw_shell_path(tmp_path):
    _, command, _ = support.build_tmux_command(tmp_path, [], "autorun", "printf '%s' hi && sleep 1")
    assert command == "printf '%s' hi && sleep 1"


def test_cleanup_never_targets_user_sessions(monkeypatch):
    run = Mock(side_effect=AssertionError("must not touch user session"))
    monkeypatch.setattr(support, "_tmux_run", run)
    assert support.close_tmux_session("user-session")["status"] == "skipped"
    run.assert_not_called()


@pytest.mark.parametrize("outcome", ["gone", "failed", "exception"])
def test_cleanup_reports_outcome(outcome, monkeypatch):
    replies = [subprocess.CompletedProcess([], 1, "", "no server running" if outcome == "gone" else "denied")]
    if outcome == "exception":
        replies = [OSError("tmux unavailable")]
    monkeypatch.setattr(support, "_tmux_run", Mock(side_effect=replies))
    status = support.close_tmux_session("spec-autoplan-" + "a" * 32)["status"]
    assert status == ("failed" if outcome == "exception" else outcome)


@pytest.mark.skipif(not shutil.which("tmux"), reason="tmux integration dependency unavailable")
@pytest.mark.parametrize("write_failure", [False, True])
def test_real_entry_tui_and_state_transaction(entry, monkeypatch, capsys, write_failure):
    module, args, directory = entry
    root = directory.parent.parent
    evidence = root / "worker evidence 中文.json"
    prompt = "a 'quote' $HOME; echo unsafe\n中文"
    code = (
        "import json,os,signal,sys,time; "
        "signal.signal(signal.SIGHUP, signal.SIG_IGN); "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "open(sys.argv[1], 'w').write(json.dumps([os.getcwd(), os.isatty(0), sys.argv[2], os.getpid()])); "
        "time.sleep(30)"
    )
    monkeypatch.setattr(support.platform, "system", lambda: "Linux")
    monkeypatch.setattr(module, "build_worker_command", lambda *a: [sys.executable, "-c", code, str(evidence), prompt])
    if write_failure:
        monkeypatch.setattr(support, "atomic_write", Mock(side_effect=OSError("disk full")))
    name = None
    worker_pid = None
    try:
        assert module.main(args) == int(write_failure)
        output = capsys.readouterr()
        observed = json.loads(evidence.read_text())
        worker_pid = observed[3]
        assert observed[:3] == [str(root), True, prompt]
        if write_failure:
            assert "disk full" in output.err
            assert not (directory / "chain.json").exists()
            events = [json.loads(row) for row in (directory / "events.jsonl").read_text().splitlines()]
            rollback = next(event for event in events if event["kind"] == "refusal_close")
            name = rollback["tmux_session"]
            assert rollback["cleanup"]["status"] in ("closed", "gone")
            probe = subprocess.run(["tmux", "-L", name, "has-session", "-t", "=" + name], capture_output=True)
            assert probe.returncode != 0
            worker = subprocess.run(["ps", "-p", str(worker_pid), "-o", "stat="], capture_output=True, text=True)
            assert not worker.stdout.strip() or worker.stdout.strip().startswith("Z"), "rollback left a running worker"
        else:
            result = json.loads(output.out)
            name = result["tmux_session"]
            assert json.loads((directory / "chain.json").read_text())["tmux_session"] == name
    finally:
        if name:
            support.close_tmux_session(name)
        if worker_pid:
            try:
                os.kill(worker_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize("owner", ["", "1", "20\n21", "bad reply"])
def test_cleanup_unknown_pane_never_signals(owner, monkeypatch):
    monkeypatch.setattr(support, "_tmux_run", Mock(return_value=subprocess.CompletedProcess([], 0, owner, "")))
    kill = Mock(side_effect=AssertionError("unverified group"))
    monkeypatch.setattr(support.os, "killpg", kill)
    assert support.close_tmux_session("spec-autoplan-" + "a" * 32)["status"] == "failed"
    kill.assert_not_called()


@pytest.mark.parametrize("session,group", [(998, 999), (999, 998)])
def test_cleanup_rejects_nonisolated_pane(session, group, monkeypatch):
    monkeypatch.setattr(support.os, "getpgrp", lambda: 123)
    monkeypatch.setattr(support.os, "getpgid", lambda _: group)
    monkeypatch.setattr(support.os, "getsid", lambda _: session)
    kill = Mock()
    monkeypatch.setattr(support.os, "killpg", kill)
    with pytest.raises(support.ChainSpawnError, match="isolated"):
        support._stop_tmux_group(999)
    kill.assert_not_called()


@pytest.mark.parametrize("stdout,code", [("", 0), ("bad reply", 0), ("999 999 S", 1)])
def test_cleanup_process_observation_fails_closed(stdout, code, monkeypatch):
    monkeypatch.setattr(support.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], code, stdout, "")))
    with pytest.raises(support.ChainSpawnError, match="verify"):
        support._tmux_group_running(999)


def test_cleanup_ignores_zombies_and_other_groups(monkeypatch):
    monkeypatch.setattr(
        support.subprocess,
        "run",
        Mock(
            return_value=subprocess.CompletedProcess(
                [],
                0,
                "998 998 S\n999 999 Z+\n1000 999 X\n",
                "",
            )
        ),
    )
    monkeypatch.setattr(support.os, "getsid", Mock(side_effect=AssertionError("no live members")))
    assert support._tmux_group_running(999) is False


def test_cleanup_group_membership_changed_refuses_signal(monkeypatch):
    monkeypatch.setattr(
        support.subprocess,
        "run",
        Mock(
            return_value=subprocess.CompletedProcess(
                [],
                0,
                "999 999 S\n",
                "",
            )
        ),
    )
    monkeypatch.setattr(support.os, "getsid", lambda _: 1001)
    with pytest.raises(support.ChainSpawnError, match="identity changed"):
        support._tmux_group_running(999)


@pytest.mark.parametrize("outcome", ["exited", "denied", "stuck"])
def test_cleanup_signal_outcomes(outcome, monkeypatch):
    monkeypatch.setattr(support.os, "getpgrp", lambda: 123)
    monkeypatch.setattr(support.os, "getpgid", lambda _: 999)
    monkeypatch.setattr(support.os, "getsid", lambda _: 999)
    monkeypatch.setattr(support, "_tmux_group_running", lambda _: True)
    monkeypatch.setattr(support, "TMUX_STOP_GRACE_SECONDS", 0)
    failure = {"exited": ProcessLookupError(), "denied": PermissionError(), "stuck": None}[outcome]
    kill = Mock(side_effect=failure)
    monkeypatch.setattr(support.os, "killpg", kill)
    if outcome == "exited":
        support._stop_tmux_group(999)
    elif outcome == "denied":
        with pytest.raises(PermissionError):
            support._stop_tmux_group(999)
    else:
        with pytest.raises(support.ChainSpawnError, match="remains running"):
            support._stop_tmux_group(999)
        assert [call.args[1] for call in kill.call_args_list] == [signal.SIGTERM, signal.SIGKILL]


def test_missing_ps_refuses_before_start(tmp_path, monkeypatch):
    monkeypatch.setattr(support.shutil, "which", lambda executable: "/usr/bin/tmux" if executable == "tmux" else None)
    run = Mock(side_effect=AssertionError("must not launch without rollback dependency"))
    monkeypatch.setattr(support, "_tmux_run", run)
    argv, _, name = support.build_tmux_command(tmp_path, ["pi"], "autoplan")
    with pytest.raises(support.ChainSpawnError, match="install procps"):
        support.open_tmux_session(argv, name)
    run.assert_not_called()


def test_linux_ps_question_mark_is_not_a_tty(monkeypatch):
    monkeypatch.setattr(
        support.subprocess,
        "run",
        Mock(
            return_value=subprocess.CompletedProcess(
                [],
                0,
                "1 ? python",
                "",
            )
        ),
    )
    assert support.nearest_ancestor_tty() is None


def test_sweep_linux_does_not_automate_terminal(tmp_path, monkeypatch, capsys):
    import sweep_chain_windows

    monkeypatch.setattr(support.platform, "system", lambda: "Linux")
    monkeypatch.setattr(support, "find_dead_chain_windows", Mock(side_effect=AssertionError("macOS only")))
    assert sweep_chain_windows.main(["--root", str(tmp_path)]) == 1
    assert "macOS" in capsys.readouterr().err
