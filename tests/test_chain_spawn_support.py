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
    "DEFAULT_MAX_ROUNDS": 20,
    "DEFAULT_MAX_PASSES": 12,
}


class TestGuardTable:
    def test_guard_table_values_are_frozen(self):
        for name, expected in GUARD_TABLE.items():
            actual = getattr(support, name)
            assert actual == expected, name
            assert type(actual) is type(expected), name  # 1.0 stays float, not 1

    def test_cap_reexport_identity_across_every_import_site(self):
        import autoplan_spawn
        import autorun_spawn
        import chain_recovery

        assert autorun_spawn.DEFAULT_MAX_ROUNDS is support.DEFAULT_MAX_ROUNDS
        assert autoplan_spawn.DEFAULT_MAX_PASSES is support.DEFAULT_MAX_PASSES
        assert chain_recovery.DEFAULT_MAX_ROUNDS is support.DEFAULT_MAX_ROUNDS
        assert chain_recovery.DEFAULT_MAX_PASSES is support.DEFAULT_MAX_PASSES


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
