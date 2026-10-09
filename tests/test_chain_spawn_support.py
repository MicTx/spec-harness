"""Tests for scripts/chain_spawn_support.py (the F5 shared chain-state module).

One implementation behind two authorized chain scripts — these tests pin the
module's own contract (lock context manager, audit/event appends with the
anti-glue tail guard, tty fallback resolution, the tolerant state read, error
identity) independent of either script's command surface. The scripts'
seam wrappers (patch-point preservation) are covered by their own test files.
"""

import ast
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import chain_spawn_support as support  # noqa: E402  # type: ignore
from chain_spawn_support import (  # noqa: E402  # type: ignore
    ChainSpawnError,
    append_audit,
    append_event,
    append_refusal_event,
    chain_dir,
    lock_holder,
    read_last_refusal,
    read_state,
    read_state_file,
    single_chain_lock,
    utc_now,
)


@pytest.fixture
def chain_root(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    return root


def write_state(chain: Path, payload) -> None:
    chain.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        (chain / "chain.json").write_text(payload, encoding="utf-8")
    else:
        (chain / "chain.json").write_text(json.dumps(payload) + "\n", encoding="utf-8")


class TestModuleLeafContract:
    def test_module_imports_neither_chain_script(self):
        # F5 dependency direction: chain_spawn_support is the leaf; importing
        # either chain script from it would re-create the circularity F5
        # removed. Checked in a fresh interpreter: the full suite keeps both
        # scripts loaded in-process, so sys.modules here would lie.
        probe = (
            "import sys; sys.path.insert(0, 'scripts'); "
            "import chain_spawn_support; "
            "print('autorun_spawn' in sys.modules or 'autoplan_spawn' in sys.modules)"
        )
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(Path(__file__).resolve().parent.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == "False"

    def test_error_base_identity(self):
        assert ChainSpawnError.__module__ == "chain_spawn_support"
        assert issubclass(ChainSpawnError, Exception)

    def test_chain_dir_resolves_per_chain_and_rejects_unknown(self):
        assert chain_dir(Path("/p"), "autorun") == Path("/p/.spec/autorun")
        assert chain_dir(Path("/p"), "autoplan") == Path("/p/.spec/autoplan")
        with pytest.raises(ChainSpawnError, match="unknown chain"):
            chain_dir(Path("/p"), "bogus")

    def test_utc_now_shape(self):
        import re

        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", utc_now())


# F12: the chain guard table is frozen — every boundary constant lives in
# chain_spawn_support and every import site re-exports the same object.
GUARD_TABLE = {
    "OSASCRIPT_TIMEOUT_SECONDS": 30,
    "PS_TIMEOUT_SECONDS": 5,
    "WORKER_START_TIMEOUT_SECONDS": 60,
    "WORKER_START_POLL_SECONDS": 1.0,
    "CLOSE_DELAY_SECONDS": 3,
    "CLOSE_POLL_INTERVAL_SECONDS": 2,
    "CLOSE_WAIT_EXIT_SECONDS": 120,
    # F17 add-only: the recycle escalation phases and the open retry
    "CLOSE_NATURAL_GRACE_SECONDS": 10,
    "CLOSE_TERM_WAIT_SECONDS": 15,
    "TERMINAL_OPEN_ATTEMPTS": 2,
    "TERMINAL_OPEN_RETRY_BACKOFF_SECONDS": 2,
}


class TestGuardTable:
    def test_guard_table_values_are_frozen(self):
        for name, expected in GUARD_TABLE.items():
            actual = getattr(support, name)
            assert actual == expected, name
            assert type(actual) is type(expected), name  # 1.0 stays float, not 1

    def test_the_adaptive_cap_machinery_is_gone(self):
        # chains are unbounded without an explicit flag: the F17 adaptive
        # default caps (and their constants) must stay removed so no code
        # path ever computes a cap the user did not pass
        for gone in (
            "DEFAULT_MAX_ROUNDS",
            "DEFAULT_MAX_PASSES",
            "adaptive_max_rounds",
            "adaptive_max_passes",
            "effective_max_cap",
        ):
            assert not hasattr(support, gone), gone


# --- F12 boundedness invariant (test-file internal helpers) -----------------


def _subprocess_call_nodes(tree):
    """Every ``subprocess.run`` / ``subprocess.Popen`` attribute-style call."""
    nodes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
                and func.attr in ("run", "Popen")
            ):
                nodes.append(node)
    return nodes


def _enclosing_function_names(tree):
    """Map id(call node) -> every function name the call sits inside."""
    enclosing = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    enclosing.setdefault(id(node), []).append(fn.name)
    return enclosing


def _boundedness_violations(source):
    """F12 invariant over one module's source: every blocking ``subprocess.run``
    call must carry ``timeout=``; the only detached ``Popen`` allowed is the
    close helper inside ``schedule_window_close`` whose first argument is a
    ``build_close_argv(...)`` call."""
    tree = ast.parse(source)
    enclosing = _enclosing_function_names(tree)
    violations = []
    for call in _subprocess_call_nodes(tree):
        holders = enclosing.get(id(call), [])
        where = f" in {'/'.join(holders)}" if holders else " at module level"
        if call.func.attr == "run":
            if not any(keyword.arg == "timeout" for keyword in call.keywords):
                violations.append(f"subprocess.run without timeout={where}")
            continue
        if "schedule_window_close" not in holders:
            violations.append(f"subprocess.Popen outside schedule_window_close{where}")
            continue
        first = call.args[0] if call.args else None
        built_by_close_argv = (
            isinstance(first, ast.Call) and isinstance(first.func, ast.Name) and first.func.id == "build_close_argv"
        )
        if not built_by_close_argv:
            violations.append(f"subprocess.Popen first arg is not build_close_argv(...){where}")
    return violations


class TestBoundedSubprocessCalls:
    """F12: the three chain scripts never block unbounded — every
    ``subprocess.run`` is timeout-bounded and the sole detached ``Popen`` is
    the close helper. The negative tests plant each violation shape in a
    source string so the detector is proven able to go red."""

    GUARDED_SCRIPTS = tuple(
        ROOT / "scripts" / name for name in ("autorun_spawn.py", "autoplan_spawn.py", "chain_spawn_support.py")
    )

    def test_guarded_scripts_have_no_boundedness_violations(self):
        # the 7 subprocess.run sites carry timeout=; the only Popen is the
        # detached close helper inside schedule_window_close
        for path in self.GUARDED_SCRIPTS:
            violations = _boundedness_violations(path.read_text(encoding="utf-8"))
            assert violations == [], f"{path.name}: {violations}"

    def test_negative_run_without_timeout_is_flagged(self):
        source = "import subprocess\n\ndef probe():\n    subprocess.run(['/bin/ps'])\n"
        violations = _boundedness_violations(source)
        assert any("subprocess.run without timeout=" in v for v in violations)

    def test_negative_module_level_popen_is_flagged(self):
        source = "import subprocess\n\nsubprocess.Popen(['/bin/sh', '-c', 'work'])\n"
        violations = _boundedness_violations(source)
        assert any("subprocess.Popen outside schedule_window_close" in v for v in violations)

    def test_negative_popen_in_other_function_is_flagged(self):
        source = "import subprocess\n\ndef spawn_worker():\n    subprocess.Popen(['/bin/sh', '-c', 'work'])\n"
        violations = _boundedness_violations(source)
        assert any("subprocess.Popen outside schedule_window_close" in v for v in violations)

    def test_negative_popen_with_foreign_argv_is_flagged(self):
        source = "import subprocess\n\ndef schedule_window_close():\n    subprocess.Popen(['/bin/sh', '-c', 'close'])\n"
        violations = _boundedness_violations(source)
        assert any("first arg is not build_close_argv" in v for v in violations)

    def test_negative_popen_with_wrong_callee_is_flagged(self):
        source = "import subprocess\n\ndef schedule_window_close():\n    subprocess.Popen(other_argv(1, 2))\n"
        violations = _boundedness_violations(source)
        assert any("first arg is not build_close_argv" in v for v in violations)


class TestSingleChainLock:
    def test_lock_records_holder_and_releases(self, chain_root, capsys):
        chain = chain_dir(chain_root, "autorun")
        with single_chain_lock(chain, "autorun"):
            lines = [line for line in (chain / "chain.lock").read_text(encoding="utf-8").splitlines() if line.strip()]
            assert len(lines) == 1
            holder = json.loads(lines[0])
            assert holder["pid"] == os.getpid()
            assert holder["chain"] == "autorun"
            assert holder["at"]
            assert holder["command"]
        # released: a second acquisition in the same process succeeds and
        # appends exactly one more advisory holder line
        with single_chain_lock(chain, "autorun"):
            pass
        lines = [line for line in (chain / "chain.lock").read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == 2

    def test_parallel_chain_is_refused_not_queued(self, chain_root):
        chain = chain_dir(chain_root, "autorun")
        held = threading.Event()
        release = threading.Event()

        def hold():
            with single_chain_lock(chain, "autorun"):
                held.set()
                release.wait(timeout=10)

        thread = threading.Thread(target=hold)
        thread.start()
        assert held.wait(timeout=5)
        try:
            with pytest.raises(ChainSpawnError, match="parallel chains are refused"):
                with single_chain_lock(chain, "autorun"):
                    pass
        finally:
            release.set()
            thread.join(timeout=5)

    def test_error_factory_and_busy_message_come_from_the_caller(self, chain_root):
        class LocalError(ChainSpawnError):
            pass

        chain = chain_dir(chain_root, "autoplan")
        held = threading.Event()
        release = threading.Event()

        def hold():
            with single_chain_lock(chain, "autoplan"):
                held.set()
                release.wait(timeout=10)

        thread = threading.Thread(target=hold)
        thread.start()
        assert held.wait(timeout=5)
        try:
            with pytest.raises(LocalError, match="custom busy wording"):
                with single_chain_lock(chain, "autoplan", error=LocalError, busy_message="custom busy wording"):
                    pass
        finally:
            release.set()
            thread.join(timeout=5)


class TestLockHolderProbe:
    def test_free_when_no_lock_file(self, chain_root):
        assert lock_holder(chain_dir(chain_root, "autorun")) == {"status": "free"}

    def test_held_by_live_pid_from_holder_line(self, chain_root):
        chain = chain_dir(chain_root, "autorun")
        with single_chain_lock(chain, "autorun"):
            probe = lock_holder(chain)
        assert probe["status"] == "held"
        assert probe["holder"] == "pid"
        assert probe["pid"] == os.getpid()
        assert probe["command"]
        assert probe["since"]

    def test_holder_unknown_when_line_missing_or_unparseable(self, chain_root):
        import fcntl

        chain = chain_dir(chain_root, "autorun")
        chain.mkdir(parents=True)
        lock_file = chain / "chain.lock"
        lock_file.write_text("not json\n", encoding="utf-8")
        with open(lock_file, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert lock_holder(chain) == {"status": "held", "holder": "unknown"}
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def test_stale_when_holder_pid_exited(self, chain_root):
        import fcntl

        chain = chain_dir(chain_root, "autorun")
        exited = subprocess.Popen(["/bin/sleep", "0"])
        exited.wait()
        chain.mkdir(parents=True)
        lock_file = chain / "chain.lock"
        lock_file.write_text(
            json.dumps({"pid": exited.pid, "command": "x", "at": "t", "chain": "autorun"}) + "\n",
            encoding="utf-8",
        )
        with open(lock_file, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert lock_holder(chain) == {"status": "held", "holder": "stale", "pid": exited.pid}
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def test_injected_pid_check_decides_holder_render(self, chain_root):
        import fcntl

        chain = chain_dir(chain_root, "autorun")
        chain.mkdir(parents=True)
        lock_file = chain / "chain.lock"
        lock_file.write_text(json.dumps({"pid": 4242, "command": "x", "at": "t"}) + "\n", encoding="utf-8")
        with open(lock_file, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert lock_holder(chain, pid_alive_check=lambda pid: None) == {
                "status": "held",
                "holder": "unknown",
            }
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class TestAuditAppends:
    def test_append_audit_lines_are_json_sorted(self, chain_root):
        audit = chain_dir(chain_root, "autorun") / "spawns.jsonl"
        append_audit(audit, {"b": 2, "a": 1})
        append_audit(audit, {"a": 3})
        lines = audit.read_text(encoding="utf-8").splitlines()
        assert json.loads(lines[0]) == {"a": 1, "b": 2}
        assert json.loads(lines[1]) == {"a": 3}

    def test_append_event_never_glues_onto_unterminated_tail(self, chain_root, capsys):
        events = chain_dir(chain_root, "autorun") / "events.jsonl"
        events.parent.mkdir(parents=True, exist_ok=True)
        partial = json.dumps({"kind": "partial"})[:12]  # unterminated partial crash line
        events.write_text(partial, encoding="utf-8")
        append_event(events, {"kind": "next", "n": 1})
        lines = events.read_text(encoding="utf-8").splitlines()
        # the new record is its own complete, parseable line — not glued onto
        # the partial tail — and the partial prefix is left untouched
        assert lines[0] == partial
        assert json.loads(lines[1]) == {"kind": "next", "n": 1}
        assert events.read_text(encoding="utf-8").endswith("}\n")

    def test_append_refusal_event_records_class_name_and_message(self, chain_root, capsys):
        chain = chain_dir(chain_root, "autoplan")
        append_refusal_event(chain, "autoplan", RuntimeError("boom"))
        event = json.loads((chain / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
        assert event["kind"] == "spawn_refusal"
        assert event["chain"] == "autoplan"
        assert event["type"] == "RuntimeError"
        assert event["message"] == "boom"

    def test_append_event_failure_is_best_effort(self, chain_root, capsys):
        events = chain_dir(chain_root, "autorun") / "events.jsonl"
        events.parent.mkdir(parents=True, exist_ok=True)
        events.mkdir()  # a directory makes the append fail
        append_event(events, {"kind": "x"})
        err = capsys.readouterr().err
        assert "could not record chain event" in err

    def test_read_last_refusal_tail_window_and_count(self, chain_root):
        events = chain_dir(chain_root, "autorun") / "events.jsonl"
        lines = []
        for index in range(30):
            lines.append(json.dumps({"kind": "recycle_close", "n": index}))
        lines.append(json.dumps({"kind": "spawn_refusal", "type": "OldError", "message": "old", "at": "t0"}))
        for index in range(30):
            lines.append(json.dumps({"kind": "recycle_close", "n": 100 + index}))
        events.parent.mkdir(parents=True, exist_ok=True)
        events.write_text("\n".join(lines) + "\n", encoding="utf-8")
        total, last = read_last_refusal(events)
        # the single refusal sits outside the 20-line tail window: counted,
        # but not surfaced as the recent last_refusal
        assert total == 1
        assert last is None

    def test_read_last_refusal_prefers_refusals_over_other_events(self, chain_root):
        events = chain_dir(chain_root, "autoplan") / "events.jsonl"
        records = [
            {"kind": "spawn_refusal", "type": "A", "message": "a", "at": "t1"},
            {"kind": "recycle_close", "result": "closed", "at": "t2"},
            {"kind": "spawn_refusal", "type": "B", "message": "b", "at": "t3"},
        ]
        events.parent.mkdir(parents=True, exist_ok=True)
        events.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
        total, last = read_last_refusal(events)
        assert total == 2
        assert last == {"kind": "spawn_refusal", "at": "t3", "type": "B", "message": "b"}


class TestReadState:
    def test_missing_state_is_fresh_even_with_audit(self, chain_root):
        chain = chain_dir(chain_root, "autorun")
        chain.mkdir(parents=True)
        append_audit(chain / "spawns.jsonl", {"round": 3})
        state, status = read_state(chain, "autorun")
        assert state is None
        assert status == "missing"

    def test_ok_state_normalized_with_defaults(self, chain_root):
        chain = chain_dir(chain_root, "autorun")
        write_state(chain, {"round": 2})
        state, status = read_state(chain, "autorun")
        assert status == "ok"
        assert state["round"] == 2
        assert state["window_recycle"] == {}
        assert state["host"] == "unknown"

    def test_corrupt_state_rebuilt_from_audit_tail(self, chain_root, capsys):
        chain = chain_dir(chain_root, "autoplan")
        chain.mkdir(parents=True)
        append_audit(chain / "spawns.jsonl", {"pass": 1, "kind": "framework"})
        write_state(chain, "{half-written")
        state, status = read_state(chain, "autoplan")
        assert status == "recovered"
        assert state["pass"] == 1
        assert state["recovered_from"] == "spawns.jsonl line 1"
        # written back atomically as valid JSON
        rewritten = json.loads((chain / "chain.json").read_text(encoding="utf-8"))
        assert rewritten["pass"] == 1
        # one recovery event appended
        event = json.loads((chain / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
        assert event["kind"] == "recovery"
        assert event["action"] == "recovered_state"
        assert event["chain"] == "autoplan"

    def test_corrupt_state_without_rebuildable_audit_stays_corrupt(self, chain_root):
        chain = chain_dir(chain_root, "autorun")
        write_state(chain, "{nope")
        state, status = read_state(chain, "autorun")
        assert state is None
        assert status == "corrupt"
        # the corrupt file is left exactly as found
        assert (chain / "chain.json").read_text(encoding="utf-8") == "{nope"

    def test_partial_audit_tail_lines_do_not_hide_rebuild(self, chain_root):
        chain = chain_dir(chain_root, "autoplan")
        chain.mkdir(parents=True)
        (chain / "spawns.jsonl").write_text(
            json.dumps({"pass": 4, "kind": "detail"}) + "\n" + json.dumps({"pass": 5})[:8],
            encoding="utf-8",
        )
        write_state(chain, "not json at all")
        state, status = read_state(chain, "autoplan")
        assert status == "recovered"
        assert state["pass"] == 4
        assert state["recovered_from"] == "spawns.jsonl line 1"

    def test_read_state_file_strict_raises_with_caller_error(self, chain_root):
        chain = chain_dir(chain_root, "autorun")
        chain.mkdir(parents=True)

        class LocalError(ChainSpawnError):
            pass

        state_file = chain / "chain.json"
        state_file.write_text("{bad", encoding="utf-8")
        with pytest.raises(LocalError, match="chain state unreadable"):
            read_state_file(state_file, label="chain state", error=LocalError)
        state_file.write_text("[1, 2]", encoding="utf-8")
        with pytest.raises(LocalError, match="not a JSON object"):
            read_state_file(state_file, label="chain state", error=LocalError)
        assert read_state_file(chain / "absent.json", label="chain state", error=LocalError) is None


class TestSessionTty:
    def test_controlling_tty_used_when_it_resolves_to_a_device(self):
        assert support.session_tty(controlling=lambda: "/dev/ttys012") == "/dev/ttys012"

    def test_literal_dev_tty_falls_back_to_ancestors(self, monkeypatch):
        # macOS answers ttyname() with the literal /dev/tty for a /dev/tty
        # descriptor — that never matches a window tty, so the resolution
        # must fall through to the ps-based ancestor walk.
        monkeypatch.setattr(support, "nearest_ancestor_tty", lambda: "/dev/ttys099")
        assert support.session_tty(controlling=lambda: "/dev/tty") == "/dev/ttys099"

    def test_no_tty_anywhere_returns_none(self, monkeypatch):
        monkeypatch.setattr(support, "controlling_tty", lambda: None)
        monkeypatch.setattr(support, "nearest_ancestor_tty", lambda: None)
        assert support.session_tty() is None

    def test_nearest_ancestor_walk_on_a_real_process_chain(self, monkeypatch):
        monkeypatch.setattr(support, "controlling_tty", lambda: None)
        # Under pytest there is a real ancestor chain; the walk must either
        # find a real device path or return None — never the literal /dev/tty
        # and never a ?? placeholder.
        result = support.nearest_ancestor_tty()
        if result is not None:
            assert result.startswith("/dev/ttys")


# --- opt-in chain caps (F12 guard helpers; F17 adaptive defaults removed) ------


def _next_pass(state_value, cap):
    """One next_index_within_cap call with the pass chain's frozen wording."""
    return support.next_index_within_cap(
        state_value,
        cap,
        label="pass",
        option="max-passes",
        non_integer_template="pass-chain state has a non-integer pass index: {!r}",
    )


class TestOptInCaps:
    def test_validate_cap_refuses_non_positive(self):
        with pytest.raises(ChainSpawnError, match="--max-rounds must be >= 1"):
            support.validate_cap(0, "max-rounds")
        with pytest.raises(ChainSpawnError, match="--max-passes must be >= 1"):
            support.validate_cap(-3, "max-passes")
        assert support.validate_cap(1, "max-rounds") == 1

    def test_next_index_with_none_cap_is_unbounded(self):
        # no cap (the no-flag default): the counter advances past any
        # boundary without ever refusing
        for state_value in (None, 0, 7, 30, 10000):
            assert _next_pass(state_value, None) == (state_value or 0) + 1

    def test_next_index_with_explicit_cap_keeps_the_f12_boundary(self):
        # next == cap allows the last spawn; next > cap refuses with the
        # verbatim frozen text
        assert _next_pass(11, 12) == 12
        with pytest.raises(ChainSpawnError, match="pass cap reached: next pass 13 exceeds --max-passes 12"):
            _next_pass(12, 12)

    def test_next_index_non_integer_state_refuses(self):
        for bad in (True, 1.0, "4"):
            with pytest.raises(ChainSpawnError, match="non-integer"):
                _next_pass(bad, 12)

    def test_format_chain_counter(self):
        assert support.format_chain_counter(5, 20) == "5/20"
        assert support.format_chain_counter(5, None) == "5 (no cap)"
        assert support.format_chain_counter(31, None) == "31 (no cap)"
        # a non-int cap value (corrupt state) renders unbounded, never crashes
        assert support.format_chain_counter(5, "garbage") == "5 (no cap)"
        assert support.format_chain_counter(5, True) == "5 (no cap)"


class TestWorkerPidsOnTty:
    def test_matches_native_and_interpreter_shapes_with_pids(self, monkeypatch):
        ps_output = (
            "ttys009   4321 node /opt/homebrew/bin/codex --cd /tmp -- PROMPT\n"
            "ttys009   5002 /bin/sh /usr/local/bin/pi --mode text -- PROMPT2\n"
            "ttys010   6003 /usr/local/bin/claude\n"
            "ttys011   7004 tail -f logs/pi\n"
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout=ps_output, stderr=""),
        )
        assert support.worker_pids_on_tty("codex", "/dev/ttys009") == [4321]
        assert support.worker_pids_on_tty("pi", "/dev/ttys009") == [5002]
        assert support.worker_pids_on_tty("claude", "/dev/ttys010") == [6003]
        # a bare word further right in an unrelated command is not the worker
        assert support.worker_pids_on_tty("pi", "/dev/ttys011") == []

    def test_ps_failure_returns_empty(self, monkeypatch):
        def failed(*a, **k):
            raise subprocess.SubprocessError("ps gone")

        monkeypatch.setattr(subprocess, "run", failed)
        assert support.worker_pids_on_tty("codex", "/dev/ttys009") == []


class TestTerminalGeometryInheritance:
    def test_spawn_script_keeps_the_plain_open_form(self, tmp_path):
        argv, shell = support.build_terminal_command(tmp_path, ["pi", "--mode", "text", "--", "PROMPT"])
        script = argv[2]
        assert "parentPos" not in script
        assert "set position of" not in script
        assert "do script" in script
        assert 'return match & " " & spawnedTty' in script
        assert shell.startswith("cd ")

    def test_read_window_geometry_script_shape(self):
        # the lookup goes through the tty literal and reports position/size
        argv = None
        from unittest.mock import patch

        def fake_run(args, **kwargs):
            nonlocal argv
            argv = args
            return subprocess.CompletedProcess(args, 0, stdout="10, 20, 597, 432\n", stderr="")

        with patch.object(support.subprocess, "run", fake_run):
            assert support.read_window_geometry("/dev/ttys012") == (10, 20, 597, 432)
        assert argv[0] == "osascript"
        assert 'if tty of t is "/dev/ttys012"' in argv[2]
        assert "set {x, y} to position of matchRef" in argv[2]
        assert "set {w, h} to size of matchRef" in argv[2]

    def test_read_window_geometry_tolerant(self, monkeypatch):
        # no hosting window -> None; garbage -> None; failure -> None
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="\n", stderr=""),
        )
        assert support.read_window_geometry("/dev/ttys012") is None
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="not,numbers\n", stderr=""),
        )
        assert support.read_window_geometry("/dev/ttys012") is None

        def failed(*a, **k):
            raise subprocess.SubprocessError("gone")

        monkeypatch.setattr(subprocess, "run", failed)
        assert support.read_window_geometry("/dev/ttys012") is None

    def test_apply_window_geometry_sets_twice_and_ignores_invalid(self, monkeypatch):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args[2])
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(support.time, "sleep", lambda s: None)
        support.apply_window_geometry(42, (10, 20, 597, 432))
        assert len(calls) == 2  # the double-set beats the cascade race
        assert "set position of window id 42 to {10, 20}" in calls[0]
        assert "set size of window id 42 to {597, 432}" in calls[0]
        calls.clear()
        support.apply_window_geometry(-1, (10, 20, 597, 432))  # invalid id: no-op
        support.apply_window_geometry(True, (10, 20, 597, 432))  # bool is not an id
        assert calls == []


# --- F18: dead chain-window sweep -----------------------------------------------


class TestRecordedChainTtys:
    def test_collects_prev_tty_from_both_chains(self, tmp_path):
        autorun = tmp_path / ".spec" / "autorun"
        autoplan = tmp_path / ".spec" / "autoplan"
        autorun.mkdir(parents=True)
        autoplan.mkdir(parents=True)
        (autorun / "spawns.jsonl").write_text(
            json.dumps({"round": 1, "prev_tty": "/dev/ttys001"})
            + "\n"
            + json.dumps({"round": 2, "prev_tty": "/dev/ttys002"})
            + "\n",
            encoding="utf-8",
        )
        (autoplan / "spawns.jsonl").write_text(
            json.dumps({"pass": 1, "prev_tty": "/dev/ttys001"}) + "\n",  # duplicate dedupes
            encoding="utf-8",
        )
        assert support.recorded_chain_ttys([autorun, autoplan]) == ["/dev/ttys001", "/dev/ttys002"]

    def test_tolerant_to_missing_and_garbage(self, tmp_path):
        chain_dir = tmp_path / ".spec" / "autorun"
        chain_dir.mkdir(parents=True)
        assert support.recorded_chain_ttys([chain_dir]) == []  # no spawns file
        (chain_dir / "spawns.jsonl").write_text(
            "not json\n" + json.dumps({"prev_tty": None}) + "\n" + json.dumps({"prev_tty": "/dev/console"}) + "\n",
            encoding="utf-8",
        )
        assert support.recorded_chain_ttys([chain_dir]) == []


class TestFindDeadChainWindows:
    @staticmethod
    def _fake_enum(monkeypatch, stdout):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout=stdout, stderr=""),
        )

    def test_qualifying_window_is_returned(self, monkeypatch):
        self._fake_enum(monkeypatch, "47171|1|/dev/ttys009|false\n")
        dead = support.find_dead_chain_windows(["/dev/ttys009"])
        assert dead == [{"window_id": 47171, "tty": "/dev/ttys009"}]

    def test_each_condition_excludes(self, monkeypatch):
        recorded = ["/dev/ttys009"]
        # busy (a live session) — never touched
        self._fake_enum(monkeypatch, "1|1|/dev/ttys009|true\n")
        assert support.find_dead_chain_windows(recorded) == []
        # multi-tab — Terminal cannot close one tab
        self._fake_enum(monkeypatch, "2|2|/dev/ttys009|false\n")
        assert support.find_dead_chain_windows(recorded) == []
        # tty not recorded — the user's own window
        self._fake_enum(monkeypatch, "3|1|/dev/ttys777|false\n")
        assert support.find_dead_chain_windows(recorded) == []
        # the sweep's own session — excluded even when recorded
        self._fake_enum(monkeypatch, "4|1|/dev/ttys009|false\n")
        assert support.find_dead_chain_windows(recorded, exclude_tty="/dev/ttys009") == []
        # zero-tab ghost window (enumerated as busy by construction)
        self._fake_enum(monkeypatch, "5|0||true\n")
        assert support.find_dead_chain_windows(recorded) == []
        # empty recorded set short-circuits without touching osascript
        self._fake_enum(monkeypatch, "6|1|/dev/ttys009|false\n")
        assert support.find_dead_chain_windows([]) == []

    def test_malformed_lines_and_failures_are_tolerated(self, monkeypatch):
        self._fake_enum(monkeypatch, "garbage\n7|1|/dev/ttys009|false\nx|y|z\n")
        assert support.find_dead_chain_windows(["/dev/ttys009"]) == [{"window_id": 7, "tty": "/dev/ttys009"}]

        def failed(*a, **k):
            raise subprocess.SubprocessError("osascript gone")

        monkeypatch.setattr(subprocess, "run", failed)
        assert support.find_dead_chain_windows(["/dev/ttys009"]) == []
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout="", stderr="denied"),
        )
        assert support.find_dead_chain_windows(["/dev/ttys009"]) == []


class TestCloseTerminalWindows:
    def test_closed_and_skipped_split(self, monkeypatch):
        """F19: the close is the guarded form — only a ``closed`` outcome word
        counts as closed; busy/multi-tab/gone (the TOCTOU cases) and rc!=0
        are conservative skips, never a bare close."""
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args[2])
            if "window id 7" in args[2]:
                return subprocess.CompletedProcess(args, 0, stdout="closed\n", stderr="")
            if "window id 9" in args[2]:
                return subprocess.CompletedProcess(args, 0, stdout="busy-timeout\n", stderr="")
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="not authorized")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = support.close_terminal_windows([7, 9, 8, 7])
        assert result == {"closed": [7, 7], "skipped": [9, 8]}
        # the guarded close script re-checks existence, tab count, and busy
        assert all("busy of tab 1 of window id" in script for script in calls)

    def test_timeout_is_tolerated(self, monkeypatch):
        def failed(*a, **k):
            raise subprocess.TimeoutExpired(a[0], 30)

        monkeypatch.setattr(subprocess, "run", failed)
        assert support.close_terminal_windows([5]) == {"closed": [], "skipped": [5]}


# --- F19: kill interlock -------------------------------------------------------


class TestSessionKillInterlock:
    @staticmethod
    def _script_for(worker_name, pids=(99, 4321)):
        argv = support.build_close_argv(
            42,
            3,
            120,
            prev_tty="/dev/ttys012",
            session_pids=list(pids),
            worker_name=worker_name,
            chain="autorun",
            round_index=1,
            events_path=Path("/tmp/spec-test-events.jsonl"),
        )
        return argv[2]

    def test_guard_matches_both_boundary_forms(self):
        script = self._script_for("claude")
        assert 'case "$cmd" in *"claude "*|*/claude)' in script
        assert 'kill -TERM -"$pgid"' in script
        assert 'kill -KILL -"$pgid"' in script

    def test_no_worker_name_means_no_kill_segment(self):
        assert support._session_kill_lines([99], "TERM", "") == ""

    def test_non_token_worker_name_is_refused_conservatively(self):
        # a worker name with shell metacharacters never reaches the case pattern
        assert support._session_kill_lines([99], "TERM", "x; rm -rf") == ""
        assert support._session_kill_lines([99], "TERM", "a b") == ""

    def test_boundary_forms_exclude_substrings(self):
        guarded = support._session_kill_lines([99], "TERM", "pi")
        assert 'case "$cmd" in *"pi "*|*/pi)' in guarded
        # pip3-style substrings do not satisfy either boundary form
        assert "pip3" not in guarded

    def test_generated_helper_script_parses_under_sh(self):
        """The helper runs under macOS /bin/sh (bash 3.2): the whole script
        must parse. Found in review: an unquoted space-bearing case pattern
        (``*w *``) is a bash-3.2 parse error that string assertions missed
        because they never executed the script."""
        argv = support.build_close_argv(
            42,
            3,
            120,
            prev_tty="/dev/ttys012",
            session_pids=[4321, 99],
            worker_name="claude",
            chain="autorun",
            round_index=1,
            events_path=Path("/tmp/spec-test-events.jsonl"),
        )
        parsed = subprocess.run(["/bin/sh", "-n"], input=argv[2], text=True, capture_output=True, timeout=10)
        assert parsed.returncode == 0, parsed.stderr
        # and the no-kill (bypass) shape parses too
        argv = support.build_close_argv(
            42,
            3,
            120,
            prev_tty="/dev/ttys012",
            chain="autoplan",
            pass_index=2,
            events_path=Path("/tmp/spec-test-events.jsonl"),
        )
        parsed = subprocess.run(["/bin/sh", "-n"], input=argv[2], text=True, capture_output=True, timeout=10)
        assert parsed.returncode == 0, parsed.stderr
