"""Tests for scripts/autorun_spawn.py (the /spec:autorun chain facts and spawn)."""

import fcntl
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import autorun_spawn  # noqa: E402  # type: ignore
import chain_spawn_support as chain_support  # noqa: E402  # type: ignore
from autorun_spawn import (  # noqa: E402  # type: ignore
    AutorunError,
    NoPlanError,
    build_close_argv,
    build_prompt,
    build_terminal_command,
    build_worker_command,
    count_features,
    detect_session_host,
    discover_plan_docs,
    main,
    parse_spawn_result,
    parse_window_lookup,
    plan_args_for_prompt,
    plan_payload,
    probe_lock,
    recycle_previous_window,
    resolve_worker_host,
    verify_worker_start,
    worker_running_on_tty,
)


def write_plan(path: Path, unchecked=1, checked=0):
    lines = ["# plan", ""]
    for index in range(checked):
        lines.append(f"- [x] done feature {index + 1}")
    for index in range(unchecked):
        lines.append(f"- [ ] open feature {index + 1}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_active_package(root: Path, slug="2026-01-01_demo-feature"):
    package = root / ".spec" / "specs" / slug
    package.mkdir(parents=True, exist_ok=True)
    (package / "spec.md").write_text("# demo\n", encoding="utf-8")
    return package


EVENTS_PATH = Path("/tmp/spec-test-events.jsonl")


def chain_context(**overrides):
    """Chain context kwargs for recycle / close-helper calls."""
    context = {"chain": "autorun", "round_index": 1, "events_path": EVENTS_PATH}
    context.update(overrides)
    return context


class TestCountFeatures:
    def test_counts_checked_and_unchecked(self):
        text = "# t\n- [ ] a\n  - [x] b\n* [X] c\nplain line\n- [] not a checkbox\n"
        counts = count_features(text)
        assert counts["checked"] == 2
        assert counts["unchecked"] == 1
        assert counts["total"] == 3
        assert counts["unchecked_items"] == ["a"]

    def test_no_checkboxes(self):
        assert count_features("# empty\n\nplain\n")["total"] == 0


class TestDiscoverPlanDocs:
    def test_canonical_plans_root_qualifies(self, tmp_path):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=2)
        write_plan(tmp_path / ".spec" / "plans" / "01-core.md", checked=1)
        (tmp_path / ".spec" / "plans" / "notes.md").write_text("# no boxes\n", encoding="utf-8")
        docs, scanned = discover_plan_docs(tmp_path, None)
        assert {str(doc.relative_to(tmp_path)) for doc in docs} == {".spec/plans/README.md", ".spec/plans/01-core.md"}
        assert any("notes.md" in item for item in scanned)
        assert all(doc.name != "notes.md" for doc in docs)

    def test_legacy_locations_are_not_scanned_by_default(self, tmp_path):
        write_plan(tmp_path / ".spec" / "plan.md", unchecked=2)
        write_plan(tmp_path / "ROADMAP.md", checked=1)
        write_plan(tmp_path / "docs" / "plans" / "b.md", unchecked=1)
        write_plan(tmp_path / "docs" / "design" / "schema.md", unchecked=1)
        # The pre-migration repo-root ``plans/`` tree is a legacy location too:
        # after the planning root moved under ``.spec/plans/`` an unupgraded
        # tree is not silently discovered — ``--plan`` names it explicitly.
        write_plan(tmp_path / "plans" / "README.md", unchecked=3)
        docs, scanned = discover_plan_docs(tmp_path, None)
        assert docs == []
        assert scanned == []

    def test_cluster_master_qualifies_and_archive_is_skipped(self, tmp_path):
        cluster = tmp_path / ".spec" / "plans" / "2026-01-01_add-demo"
        write_plan(cluster / "master.md", unchecked=2, checked=1)
        (cluster / "01-detail.md").write_text("# phase detail, no checkboxes\n", encoding="utf-8")
        archived = tmp_path / ".spec" / "plans" / "archive" / "2026-01-02_fix-demo"
        write_plan(archived / "master.md", unchecked=5)
        docs, scanned = discover_plan_docs(tmp_path, None)
        assert [doc.relative_to(tmp_path).as_posix() for doc in docs] == [".spec/plans/2026-01-01_add-demo/master.md"]
        # phase details are not candidate surfaces at all; the archive is never scanned
        assert not any("01-detail.md" in item for item in scanned)
        assert all("archive" not in item for item in scanned)

    def test_explicit_paths_resolve_under_root(self, tmp_path):
        """``--plan`` is the escape hatch for a document kept outside the canonical root."""
        write_plan(tmp_path / "PLAN.md", unchecked=1)
        docs, _ = discover_plan_docs(tmp_path, "PLAN.md")
        assert [doc.name for doc in docs] == ["PLAN.md"]

    def test_explicit_path_must_exist(self, tmp_path):
        with pytest.raises(AutorunError):
            discover_plan_docs(tmp_path, "missing.md")

    def test_explicit_path_must_stay_under_root(self, tmp_path):
        outside = tmp_path.parent / "outside-plan.md"
        write_plan(outside, unchecked=1)
        with pytest.raises(AutorunError):
            discover_plan_docs(tmp_path, str(outside))


class TestPlanCommand:
    def test_exit_three_without_planning_docs(self, tmp_path, capsys):
        make_active_package(tmp_path)
        code = main(["plan", "--root", str(tmp_path)])
        assert code == 3
        captured = capsys.readouterr()
        assert "planning" in captured.err

    def test_candidate_without_checkboxes_does_not_qualify(self, tmp_path, capsys):
        (tmp_path / ".spec" / "plans").mkdir(parents=True)
        (tmp_path / ".spec" / "plans" / "README.md").write_text("# just prose\n", encoding="utf-8")
        (tmp_path / "PLAN.md").write_text("- [x] legacy box\n", encoding="utf-8")
        assert main(["plan", "--root", str(tmp_path)]) == 3

    def test_json_payload_aggregates_docs(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=2, checked=1)
        write_plan(tmp_path / ".spec" / "plans" / "01-core.md", unchecked=0, checked=2)
        assert main(["plan", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["totals"] == {"checked": 3, "unchecked": 2, "total": 5}
        assert payload["complete"] is False
        assert [doc["path"] for doc in payload["docs"]] == [".spec/plans/01-core.md", ".spec/plans/README.md"]

    def test_complete_when_all_checked(self, tmp_path):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=0, checked=3)
        payload = plan_payload(tmp_path, None)
        assert payload["complete"] is True


class TestSpawnCommand:
    def test_refuses_without_active_package(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        code = main(["spawn", "--root", str(tmp_path), "--dry-run"])
        assert code == 1
        assert "no active task package" in capsys.readouterr().err

    def test_refuses_when_plan_complete(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=0, checked=1)
        make_active_package(tmp_path)
        code = main(["spawn", "--root", str(tmp_path), "--dry-run"])
        assert code == 1
        assert "complete" in capsys.readouterr().err

    def test_dry_run_prints_osascript_without_side_effects(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=2)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        code = main(
            [
                "spawn",
                "--root",
                str(tmp_path),
                "--host",
                "codex",
                "--dry-run",
                "--format",
                "json",
            ]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["dry_run"] is True
        assert payload["round"] == 1
        argv = payload["osascript"]
        assert argv[0] == "osascript"
        assert "Terminal" in argv[2]
        assert payload["window_recycle"] == {"status": "planned"}
        assert "codex" in payload["shell_command"]
        assert "$spec autorun" in payload["shell_command"]
        assert not (tmp_path / ".spec" / "autorun" / "chain.json").exists()

    def test_round_increments_from_chain_state(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=2)
        make_active_package(tmp_path)
        state = tmp_path / ".spec" / "autorun" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"round": 4, "host": "pi"}), encoding="utf-8")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        code = main(["spawn", "--root", str(tmp_path), "--host", "pi", "--dry-run", "--format", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["round"] == 5

    def test_refuses_at_round_cap(self, tmp_path, capsys, monkeypatch):
        # the cap is opt-in: refusal happens only when the flag was passed
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=2)
        make_active_package(tmp_path)
        state = tmp_path / ".spec" / "autorun" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"round": 20}), encoding="utf-8")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        code = main(["spawn", "--root", str(tmp_path), "--max-rounds", "20", "--dry-run"])
        assert code == 1
        assert "round cap" in capsys.readouterr().err

    def test_uncapped_state_spawns_past_any_count(self, tmp_path, capsys, monkeypatch):
        # no flag = no cap: a chain at round 20 continues past the count that
        # used to stop it — the recorded cap becomes null and the prompt
        # carries no cap flag
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=2)
        make_active_package(tmp_path)
        state = tmp_path / ".spec" / "autorun" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"round": 20, "max_rounds": 20}), encoding="utf-8")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        code = main(["spawn", "--root", str(tmp_path), "--host", "pi", "--format", "json", "--dry-run"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["round"] == 21
        assert payload["max_rounds"] is None
        assert "--max-rounds" not in payload["shell_command"]

    def test_round_equal_to_cap_is_allowed(self, tmp_path, capsys, monkeypatch):
        # matrix row "round/pass cap" boundary: next == cap passes (only
        # next > cap refuses), so the final round within the cap still spawns
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=2)
        make_active_package(tmp_path)
        state = tmp_path / ".spec" / "autorun" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"round": 19}), encoding="utf-8")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        code = main(
            ["spawn", "--root", str(tmp_path), "--max-rounds", "20", "--host", "pi", "--dry-run", "--format", "json"]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["round"] == 20

    def test_cap_one_new_chain_starts_at_one(self, tmp_path, capsys, monkeypatch):
        # F12 boundary matrix: a fresh chain (no state) starts at 1 — even
        # cap=1 allows the chain's first (and final) spawn
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=2)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        code = main(
            ["spawn", "--root", str(tmp_path), "--max-rounds", "1", "--host", "pi", "--dry-run", "--format", "json"]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["round"] == 1

    def test_state_at_cap_refusal_text_is_verbatim(self, tmp_path, capsys, monkeypatch):
        # F12 boundary matrix: next > cap refuses with the exact frozen text
        # and leaves no audit record behind (the fixture state file stays);
        # the cap is opt-in, so the refusal needs the explicit flag
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=2)
        make_active_package(tmp_path)
        state = tmp_path / ".spec" / "autorun" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"round": 20}), encoding="utf-8")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        code = main(["spawn", "--root", str(tmp_path), "--max-rounds", "20", "--dry-run"])
        assert code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip() == "error: round cap reached: next round 21 exceeds --max-rounds 20"
        assert not (tmp_path / ".spec" / "autorun" / "spawns.jsonl").exists()

    def test_bool_and_float_round_refuse_like_non_integer(self, tmp_path, capsys, monkeypatch):
        # F12 strict int gate: bool/float/string values are refused like any
        # non-int — the old lenient int() cast silently accepted True / 1.0
        # and truncated 4.5, so a hand-edited state file could lie
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        state = tmp_path / ".spec" / "autorun" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        for bad in (True, 1.0, 4.5, "4"):
            state.write_text(json.dumps({"round": bad}), encoding="utf-8")
            code = main(["spawn", "--root", str(tmp_path), "--dry-run"])
            assert code == 1, bad
            captured = capsys.readouterr()
            assert captured.out == "", bad
            assert "non-integer round" in captured.err, bad
            assert repr(bad) in captured.err, bad
            assert not (tmp_path / ".spec" / "autorun" / "spawns.jsonl").exists(), bad

    def test_max_rounds_zero_refuses_without_chain_writes(self, tmp_path, capsys, monkeypatch):
        # F12 boundary matrix: --max-rounds 0 fails fast in main (validate_cap)
        # before any lock/state touch — no chain.json, no spawns.jsonl
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=2)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        code = main(["spawn", "--root", str(tmp_path), "--max-rounds", "0", "--dry-run"])
        assert code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip() == "error: --max-rounds must be >= 1"
        assert not (tmp_path / ".spec" / "autorun" / "chain.json").exists()
        assert not (tmp_path / ".spec" / "autorun" / "spawns.jsonl").exists()

    def test_refuses_when_lock_held(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        lock = tmp_path / ".spec" / "autorun" / "chain.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
                code = main(["spawn", "--root", str(tmp_path), "--dry-run"])
                assert code == 1
                assert "chain" in capsys.readouterr().err
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def test_records_state_after_successful_spawn(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="4421 /dev/ttys042", stderr=""),
        )
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: True)
        code = main(["spawn", "--root", str(tmp_path), "--host", "claude", "--format", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["round"] == 1
        assert payload["host"] == "claude"
        state = json.loads((tmp_path / ".spec" / "autorun" / "chain.json").read_text(encoding="utf-8"))
        assert state["round"] == 1
        assert state["host"] == "claude"
        assert state["host_source"] == "flag"
        spawns = (tmp_path / ".spec" / "autorun" / "spawns.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(spawns) == 1
        assert json.loads(spawns[0])["round"] == 1

    def test_osascript_failure_refuses_without_recording(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout="", stderr="not authorized"),
        )
        code = main(["spawn", "--root", str(tmp_path)])
        assert code == 1
        captured = capsys.readouterr()
        # matrix row "osascript 开窗失败（非零）": exit + stderr reason + refusal
        # audited in events.jsonl + no chain state written
        assert "osascript" in captured.err
        assert "not authorized" in captured.err
        assert captured.out == ""
        assert not (tmp_path / ".spec" / "autorun" / "chain.json").exists()
        events = (tmp_path / ".spec" / "autorun" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(events) == 1
        event = json.loads(events[0])
        assert event["kind"] == "spawn_refusal"
        assert event["chain"] == "autorun"
        assert event["type"] == "AutorunError"
        assert "not authorized" in event["message"]

    def test_non_integer_round_refuses_and_records(self, tmp_path, capsys, monkeypatch):
        # matrix row "chain 状态不可读 / 非对象 / round|pass 非整数": F3 owns the
        # non-integer round refusal (spawns rebuild / recovered_state is F4's)
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        state = tmp_path / ".spec" / "autorun" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"round": "soon"}), encoding="utf-8")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        code = main(["spawn", "--root", str(tmp_path), "--dry-run"])
        assert code == 1
        captured = capsys.readouterr()
        assert "non-integer round" in captured.err
        assert "'soon'" in captured.err
        assert captured.out == ""
        assert not (tmp_path / ".spec" / "autorun" / "spawns.jsonl").exists()
        events = (tmp_path / ".spec" / "autorun" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(events) == 1
        event = json.loads(events[0])
        assert event["kind"] == "spawn_refusal"
        assert event["type"] == "AutorunError"
        assert "non-integer round" in event["message"]

    def test_unknown_env_host_refuses(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setenv("SPEC_AUTORUN_HOST", "zcode")
        code = main(["spawn", "--root", str(tmp_path), "--dry-run"])
        assert code == 1
        assert "unknown worker host" in capsys.readouterr().err

    def test_env_host_selected(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setenv("SPEC_AUTORUN_HOST", "pi")
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        code = main(["spawn", "--root", str(tmp_path), "--dry-run", "--format", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["host"] == "pi"
        assert payload["host_source"] == "env"

    def test_session_detection_selects_running_host(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.delenv("SPEC_AUTORUN_HOST", raising=False)
        monkeypatch.setenv("PI_CODING_AGENT", "true")
        monkeypatch.setattr(
            "shutil.which",
            lambda name: {"codex": "/codex", "pi": "/pi"}.get(name),
        )
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        code = main(["spawn", "--root", str(tmp_path), "--dry-run", "--format", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["host"] == "pi"
        assert payload["host_source"] == "session"

    def test_command_override_skips_host_resolution(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: None)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        code = main(
            [
                "spawn",
                "--root",
                str(tmp_path),
                "--command",
                "echo hello > proof.txt",
                "--dry-run",
                "--format",
                "json",
            ]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["host"] == "custom"
        assert payload["shell_command"].endswith("echo hello > proof.txt")


class TestStatusCommand:
    def test_not_started(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        code = main(["status", "--root", str(tmp_path), "--format", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["started"] is False

    def test_with_chain_state(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        state = tmp_path / ".spec" / "autorun" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"round": 2, "host": "codex", "updated_at": "t"}), encoding="utf-8")
        code = main(["status", "--root", str(tmp_path), "--format", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["started"] is True
        assert payload["round"] == 2


class TestLockObservability:
    """chain.lock content mode and the status lock probe (F2 task-lock-content)."""

    def hold_lock(self, lock_path: Path, content=None):
        """Open + flock the lock file, optionally seed content, return handle."""
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if content is not None:
            handle.write(content)
            handle.flush()
        return handle

    def test_spawn_writes_holder_content(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        assert main(["spawn", "--root", str(tmp_path), "--host", "pi", "--dry-run"]) == 0
        lock_file = tmp_path / ".spec" / "autorun" / "chain.lock"
        lines = [line for line in lock_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert lines, "a holder line must be written once flock succeeds"
        holder = json.loads(lines[-1])
        assert holder["pid"] == os.getpid()
        assert holder["chain"] == "autorun"
        # the command line identifies the holding process (pytest's argv under test)
        assert isinstance(holder["command"], str) and holder["command"]
        assert holder["at"]

    def test_probe_free_without_lock_file(self, tmp_path):
        assert probe_lock(tmp_path / ".spec" / "autorun") == {"status": "free"}

    def test_probe_free_when_unheld_and_leaves_lock_acquirable(self, tmp_path):
        chain_dir = tmp_path / ".spec" / "autorun"
        chain_dir.mkdir(parents=True)
        lock_file = chain_dir / "chain.lock"
        lock_file.write_text("", encoding="utf-8")
        assert probe_lock(chain_dir) == {"status": "free"}
        # the probe released its own descriptor: the mutex stays acquirable
        with open(lock_file, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def test_probe_reports_live_holder_and_releases(self, tmp_path):
        chain_dir = tmp_path / ".spec" / "autorun"
        lock_file = chain_dir / "chain.lock"
        holder_line = (
            json.dumps(
                {
                    "pid": os.getpid(),
                    "command": "python3 scripts/autorun_spawn.py spawn --root .",
                    "at": "2026-10-07T00:00:00Z",
                    "chain": "autorun",
                }
            )
            + "\n"
        )
        handle = self.hold_lock(lock_file, holder_line)
        try:
            assert probe_lock(chain_dir) == {
                "status": "held",
                "holder": "pid",
                "pid": os.getpid(),
                "command": "python3 scripts/autorun_spawn.py spawn --root .",
                "since": "2026-10-07T00:00:00Z",
            }
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        # after release the probe reports free again: probing never keeps the mutex
        assert probe_lock(chain_dir) == {"status": "free"}

    def test_probe_reports_stale_content_for_dead_pid(self, tmp_path, monkeypatch):
        chain_dir = tmp_path / ".spec" / "autorun"
        lock_file = chain_dir / "chain.lock"
        content = json.dumps({"pid": 999999, "command": "gone", "at": "t", "chain": "autorun"}) + "\n"
        handle = self.hold_lock(lock_file, content)
        monkeypatch.setattr("autorun_spawn._pid_alive", lambda pid: False)
        try:
            assert probe_lock(chain_dir) == {"status": "held", "holder": "stale", "pid": 999999}
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def test_probe_degrades_on_empty_content(self, tmp_path):
        chain_dir = tmp_path / ".spec" / "autorun"
        handle = self.hold_lock(chain_dir / "chain.lock")
        try:
            assert probe_lock(chain_dir) == {"status": "held", "holder": "unknown"}
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def test_probe_degrades_on_partial_content(self, tmp_path):
        chain_dir = tmp_path / ".spec" / "autorun"
        handle = self.hold_lock(chain_dir / "chain.lock", '{"pid": 123')
        try:
            assert probe_lock(chain_dir) == {"status": "held", "holder": "unknown"}
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def test_probe_degrades_when_liveness_unverifiable(self, tmp_path, monkeypatch):
        chain_dir = tmp_path / ".spec" / "autorun"
        content = json.dumps({"pid": os.getpid(), "command": "x", "at": "t", "chain": "autorun"}) + "\n"
        handle = self.hold_lock(chain_dir / "chain.lock", content)
        monkeypatch.setattr("autorun_spawn._pid_alive", lambda pid: None)
        try:
            assert probe_lock(chain_dir) == {"status": "held", "holder": "unknown"}
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def test_probe_degrades_when_lock_unreadable(self, tmp_path, monkeypatch):
        chain_dir = tmp_path / ".spec" / "autorun"
        chain_dir.mkdir(parents=True)
        (chain_dir / "chain.lock").write_text("", encoding="utf-8")

        def raise_eacces(*a, **k):
            raise OSError("EACCES")

        monkeypatch.setattr("autorun_spawn.os.open", raise_eacces)
        assert probe_lock(chain_dir) == {"status": "unknown"}

    def test_pid_alive_for_live_and_exited_processes(self):
        assert autorun_spawn._pid_alive(os.getpid()) is True
        exited = subprocess.Popen(["/usr/bin/true"], stdout=subprocess.DEVNULL)
        exited.wait()
        assert autorun_spawn._pid_alive(exited.pid) is False

    def test_pid_alive_unverifiable_on_ps_failure(self, monkeypatch):
        def fake_run(*a, **k):
            raise subprocess.TimeoutExpired(a[0], 5)

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert autorun_spawn._pid_alive(4242) is None

    def test_status_renders_lock_states(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        assert main(["status", "--root", str(tmp_path)]) == 0
        assert "lock: free" in capsys.readouterr().out
        chain_dir = tmp_path / ".spec" / "autorun"
        content = json.dumps({"pid": os.getpid(), "command": "spawn", "at": "t0", "chain": "autorun"}) + "\n"
        handle = self.hold_lock(chain_dir / "chain.lock", content)
        try:
            assert main(["status", "--root", str(tmp_path)]) == 0
            assert f"lock: held by pid {os.getpid()} (spawn) since t0" in capsys.readouterr().out
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def test_status_json_lock_key(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["lock"] == {"status": "free"}
        assert payload["resume"]["action"] == "fresh_chain"
        assert payload["last_refusal"] is None


class TestRefusalAudit:
    """spawn_refusal events plus the status last_refusal render (F2 task-refusal-audit)."""

    def test_lock_refusal_records_event(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        lock_file = tmp_path / ".spec" / "autorun" / "chain.lock"
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_file, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
                assert main(["spawn", "--root", str(tmp_path), "--dry-run"]) == 1
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        err = capsys.readouterr().err
        assert err.startswith("error:")
        assert "parallel chains are refused" in err
        events = (tmp_path / ".spec" / "autorun" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(events) == 1
        event = json.loads(events[0])
        assert event["kind"] == "spawn_refusal"
        assert event["chain"] == "autorun"
        assert event["type"] == "AutorunError"
        assert "parallel chains are refused" in event["message"]
        assert event["at"]

    def test_no_plan_refusal_records_event_with_exit_three(self, tmp_path, capsys, monkeypatch):
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        assert main(["spawn", "--root", str(tmp_path), "--dry-run"]) == 3
        events = (tmp_path / ".spec" / "autorun" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert json.loads(events[0])["type"] == "NoPlanError"

    def test_plan_command_error_is_not_a_refusal_event(self, tmp_path, capsys):
        make_active_package(tmp_path)
        assert main(["plan", "--root", str(tmp_path)]) == 3
        assert not (tmp_path / ".spec" / "autorun" / "events.jsonl").exists()

    def test_refusal_records_do_not_write_chain_state(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        lock_file = tmp_path / ".spec" / "autorun" / "chain.lock"
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_file, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
                assert main(["spawn", "--root", str(tmp_path), "--dry-run"]) == 1
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        assert not (tmp_path / ".spec" / "autorun" / "chain.json").exists()
        assert not (tmp_path / ".spec" / "autorun" / "spawns.jsonl").exists()

    def test_status_last_refusal_ignores_recycle_close(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        events = tmp_path / ".spec" / "autorun" / "events.jsonl"
        events.parent.mkdir(parents=True, exist_ok=True)
        events.write_text(
            json.dumps(
                {"kind": "spawn_refusal", "at": "t1", "chain": "autorun", "type": "AutorunError", "message": "first"}
            )
            + "\n"
            + json.dumps({"kind": "recycle_close", "at": "t2", "chain": "autorun", "round": 1, "result": "closed"})
            + "\n",
            encoding="utf-8",
        )
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["last_refusal"] == {
            "kind": "spawn_refusal",
            "at": "t1",
            "type": "AutorunError",
            "message": "first",
        }
        assert payload["refusals_recorded"] == 1
        # the text render carries the same facts
        assert main(["status", "--root", str(tmp_path)]) == 0
        assert "last_refusal: first (type=AutorunError, at=t1)" in capsys.readouterr().out

    def test_status_tolerates_partial_tail_line(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        events = tmp_path / ".spec" / "autorun" / "events.jsonl"
        events.parent.mkdir(parents=True, exist_ok=True)
        good = json.dumps(
            {"kind": "spawn_refusal", "at": "t1", "chain": "autorun", "type": "AutorunError", "message": "locked"}
        )
        events.write_text(good + "\n" + '{"kind": "spawn_ref', encoding="utf-8")
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["last_refusal"]["message"] == "locked"
        assert "skipping unparseable events line" in captured.err

    def test_status_reports_refusals_without_chain_state(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        events = tmp_path / ".spec" / "autorun" / "events.jsonl"
        events.parent.mkdir(parents=True, exist_ok=True)
        events.write_text(
            json.dumps(
                {
                    "kind": "spawn_refusal",
                    "at": "t1",
                    "chain": "autorun",
                    "type": "AutorunError",
                    "message": "first failure",
                }
            )
            + "\n"
            + json.dumps(
                {
                    "kind": "spawn_refusal",
                    "at": "t2",
                    "chain": "autorun",
                    "type": "AutorunError",
                    "message": "second failure",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        assert main(["status", "--root", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "chain: not started (2 refusals recorded: second failure)" in out
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["started"] is False
        assert payload["refusals_recorded"] == 2
        assert payload["last_refusal"]["message"] == "second failure"

    def test_last_refusal_window_ignores_older_refusals(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        events = tmp_path / ".spec" / "autorun" / "events.jsonl"
        events.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps(
                {"kind": "spawn_refusal", "at": "t1", "chain": "autorun", "type": "AutorunError", "message": "old"}
            )
        ]
        for index in range(25):
            lines.append(
                json.dumps({"kind": "recycle_close", "at": f"t{index}", "chain": "autorun", "result": "closed"})
            )
        events.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        # the refusal sits outside the 20-line tail window
        assert payload["last_refusal"] is None
        assert payload["refusals_recorded"] == 1

    def test_refusal_append_separates_from_unterminated_tail(self, tmp_path):
        # live defect (2026-10-07): an unterminated tail glued the next append
        # onto the same line; the append now separates before writing
        events_dir = tmp_path / ".spec" / "autorun"
        events_dir.mkdir(parents=True)
        events = events_dir / "events.jsonl"
        events.write_text('{"kind": "recycle_close", "at": "t0"}', encoding="utf-8")
        autorun_spawn.append_refusal_event(events_dir, "autorun", AutorunError("locked"))
        lines = events.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["kind"] == "recycle_close"
        assert json.loads(lines[1])["kind"] == "spawn_refusal"
        total, last = autorun_spawn.read_last_refusal(events)
        assert total == 1
        assert last["message"] == "locked"

    def test_refusal_append_failure_does_not_mask_error(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=0, checked=1)
        make_active_package(tmp_path)
        # events.jsonl as a directory makes the append fail
        (tmp_path / ".spec" / "autorun" / "events.jsonl").mkdir(parents=True)
        assert main(["spawn", "--root", str(tmp_path), "--dry-run"]) == 1
        err = capsys.readouterr().err
        assert err.startswith("error:")
        assert "every feature checked" in err
        assert "could not record spawn refusal event" in err


class TestStatusResumeDecision:
    """The F4 resume decision wired into status: one positive render per
    decision-table class reachable on the autorun side (R5 closure)."""

    @staticmethod
    def write_chain_state(root: Path, state):
        target = root / ".spec" / "autorun" / "chain.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(state) + "\n", encoding="utf-8")
        return target

    def status_json(self, tmp_path, capsys):
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        return json.loads(capsys.readouterr().out)

    def status_text(self, tmp_path, capsys):
        assert main(["status", "--root", str(tmp_path)]) == 0
        return capsys.readouterr().out

    def test_fresh_chain_render(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        payload = self.status_json(tmp_path, capsys)
        assert payload["resume"]["action"] == "fresh_chain"
        assert payload["state_status"] == "missing"
        out = self.status_text(tmp_path, capsys)
        assert "resume: fresh_chain (" in out
        assert "resume: none" not in out

    def test_continue_package_render(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        package = make_active_package(tmp_path)
        (package / "tasks.md").write_text("- [x] a\n- [ ] b\n- [ ] c\n", encoding="utf-8")
        self.write_chain_state(tmp_path, {"round": 2, "max_rounds": 20, "host": "pi"})
        payload = self.status_json(tmp_path, capsys)
        assert payload["resume"]["action"] == "continue_package"
        assert payload["resume"]["detail"]["slug"] == "2026-01-01_demo-feature"
        assert payload["resume"]["detail"]["checked"] == 1 and payload["resume"]["detail"]["total"] == 3
        out = self.status_text(tmp_path, capsys)
        assert "resume: continue_package (" in out
        assert "2026-01-01_demo-feature (1/3 tasks)" in out

    def test_await_new_package_render(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=2)
        self.write_chain_state(tmp_path, {"round": 3, "max_rounds": 20, "host": "pi"})
        payload = self.status_json(tmp_path, capsys)
        assert payload["resume"]["action"] == "await_new_package"
        assert "resume: await_new_package (" in self.status_text(tmp_path, capsys)

    def test_disambiguate_multiple_packages_render(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path, "2026-01-01_alpha")
        make_active_package(tmp_path, "2026-01-02_beta")
        self.write_chain_state(tmp_path, {"round": 2, "max_rounds": 20, "host": "pi"})
        payload = self.status_json(tmp_path, capsys)
        assert payload["resume"]["action"] == "disambiguate_multiple_packages"
        assert payload["resume"]["detail"]["packages"] == ["2026-01-01_alpha", "2026-01-02_beta"]
        assert "resume: disambiguate_multiple_packages (" in self.status_text(tmp_path, capsys)

    def test_cap_reached_render(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)  # the cap row outranks the package resume
        self.write_chain_state(tmp_path, {"round": 20, "max_rounds": 20, "host": "pi"})
        payload = self.status_json(tmp_path, capsys)
        assert payload["resume"]["action"] == "cap_reached"
        assert payload["resume"]["detail"] == {"next": 21, "cap": 20}
        assert "resume: cap_reached (" in self.status_text(tmp_path, capsys)

    def test_chain_complete_render(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=0, checked=3)
        self.write_chain_state(tmp_path, {"round": 2, "max_rounds": 20, "host": "pi"})
        payload = self.status_json(tmp_path, capsys)
        assert payload["resume"]["action"] == "chain_complete"
        assert "resume: chain_complete (" in self.status_text(tmp_path, capsys)

    def test_no_plan_docs_render_chain_complete(self, tmp_path, capsys):
        # tolerant status: (0, 0) counts mean nothing for the chain to consume
        payload = self.status_json(tmp_path, capsys)
        assert payload["resume"]["action"] == "chain_complete"
        assert "no qualifying planning document" in payload["resume"]["reason"]

    def test_state_corrupt_render(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        state = tmp_path / ".spec" / "autorun" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("{half-written", encoding="utf-8")
        payload = self.status_json(tmp_path, capsys)
        assert payload["state_status"] == "corrupt"
        assert payload["resume"]["action"] == "state_corrupt"
        assert "never silently restart" in payload["resume"]["reason"]
        out = self.status_text(tmp_path, capsys)
        assert "resume: state_corrupt (" in out
        assert "chain: state unreadable (corrupt; see resume)" in out

    def test_recovered_state_render(self, tmp_path, capsys):
        # half-written chain.json rebuilt from the audit tail: correct action,
        # recovered_from surfaced, recovery event audited
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        package = make_active_package(tmp_path)
        (package / "tasks.md").write_text("- [ ] a\n", encoding="utf-8")
        chain_dir = tmp_path / ".spec" / "autorun"
        chain_dir.mkdir(parents=True, exist_ok=True)
        (chain_dir / "chain.json").write_text("{half-written", encoding="utf-8")
        (chain_dir / "spawns.jsonl").write_text(
            json.dumps({"round": 4, "max_rounds": 20, "host": "pi"}) + "\n", encoding="utf-8"
        )
        payload = self.status_json(tmp_path, capsys)
        assert payload["state_status"] == "recovered"
        assert payload["resume"]["action"] == "continue_package"
        assert payload["resume"]["detail"]["recovered_state"] is True
        assert payload["recovered_from"] == "spawns.jsonl line 1"
        out = self.status_text(tmp_path, capsys)
        assert "resume: continue_package (" in out
        assert "state: recovered from spawns.jsonl line 1" in out
        events = [json.loads(line) for line in (chain_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        assert events[-1]["kind"] == "recovery" and events[-1]["action"] == "recovered_state"

    def test_audit_mismatch_adopts_audit_render(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        chain_dir = tmp_path / ".spec" / "autorun"
        chain_dir.mkdir(parents=True, exist_ok=True)
        (chain_dir / "chain.json").write_text(
            json.dumps({"round": 3, "max_rounds": 7, "host": "pi"}) + "\n", encoding="utf-8"
        )
        (chain_dir / "spawns.jsonl").write_text(
            json.dumps({"round": 7, "max_rounds": 7, "host": "pi"}) + "\n", encoding="utf-8"
        )
        payload = self.status_json(tmp_path, capsys)
        assert payload["resume"]["action"] == "cap_reached"  # audit round 7 -> next 8 > cap 7
        assert payload["resume"]["detail"]["recovered_state"] is True

    def test_resume_json_shape_is_action_reason_detail(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        payload = self.status_json(tmp_path, capsys)
        assert set(payload["resume"]) == {"action", "reason", "detail"}


class TestStatusDerivedKeys:
    """Active-package facts in the status payload (resume lives in TestStatusResumeDecision)."""

    def test_active_package_progress_single(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        package = make_active_package(tmp_path, "2026-01-01_demo")
        (package / "tasks.md").write_text("# tasks\n- [x] a\n- [ ] b\n- [ ] c\n", encoding="utf-8")
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["active_package"] == {"slug": "2026-01-01_demo", "checked": 1, "total": 3}
        assert "active_packages" not in payload
        assert main(["status", "--root", str(tmp_path)]) == 0
        assert "active package: 2026-01-01_demo (1/3 tasks)" in capsys.readouterr().out

    def test_active_package_none_without_packages(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["active_package"] is None
        assert main(["status", "--root", str(tmp_path)]) == 0
        assert "active package: none" in capsys.readouterr().out

    def test_active_packages_multiple_are_listed(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        first = make_active_package(tmp_path, "2026-01-01_alpha")
        second = make_active_package(tmp_path, "2026-01-02_beta")
        (first / "tasks.md").write_text("- [ ] a\n", encoding="utf-8")
        (second / "tasks.md").write_text("- [x] a\n- [x] b\n", encoding="utf-8")
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["active_package"] is None
        assert payload["active_packages"] == [
            {"slug": "2026-01-01_alpha", "checked": 0, "total": 1},
            {"slug": "2026-01-02_beta", "checked": 2, "total": 2},
        ]
        assert main(["status", "--root", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "active packages: 2026-01-01_alpha (0/1 tasks), 2026-01-02_beta (2/2 tasks)" in out

    def test_archive_packages_are_excluded(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        archived = tmp_path / ".spec" / "specs" / "archive" / "2025-01-01_old"
        archived.mkdir(parents=True)
        (archived / "spec.md").write_text("# old\n", encoding="utf-8")
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["active_package"] is None
        assert "active_packages" not in payload

    def test_status_text_lines_are_key_value_parseable(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        package = make_active_package(tmp_path)
        (package / "tasks.md").write_text("- [ ] a\n", encoding="utf-8")
        assert main(["status", "--root", str(tmp_path)]) == 0
        lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
        assert lines
        for line in lines:
            assert ": " in line
        keys = {line.split(": ", 1)[0] for line in lines}
        assert {"project", "lock", "chain", "last_refusal", "resume", "active package", "plan"} <= keys


class TestCommandBuilders:
    def test_prompt_shapes_per_host(self):
        assert build_prompt("claude", ["--plan a.md"], 5) == "/spec:autorun --plan a.md --max-rounds 5"
        assert build_prompt("codex", [], 5) == "$spec autorun --max-rounds 5"
        assert build_prompt("pi", ["--plan a.md,b.md"], 5) == "$spec autorun --plan a.md,b.md --max-rounds 5"

    def test_prompt_without_cap_carries_no_flag(self):
        # unbounded chains forward no cap flag — nothing to hit mid-chain
        assert build_prompt("codex", [], None) == "$spec autorun"
        assert build_prompt("claude", ["--plan a.md"], None) == "/spec:autorun --plan a.md"

    def test_plan_args_quote_spacey_paths(self):
        # the prompt is re-parsed as arguments by the next session, so a path
        # with a space must survive the round trip intact
        plan_args = plan_args_for_prompt("docs/plan v2.md")
        assert plan_args == ["--plan 'docs/plan v2.md'"]
        assert build_prompt("codex", plan_args, 5) == "$spec autorun --plan 'docs/plan v2.md' --max-rounds 5"

    def test_plan_args_quiet_without_plan(self):
        assert plan_args_for_prompt(None) == []
        assert plan_args_for_prompt("PLAN.md") == ["--plan PLAN.md"]

    def test_worker_commands(self, tmp_path):
        codex = build_worker_command("codex", tmp_path, "PROMPT")
        assert codex[0] == "codex" and "exec" not in codex
        assert "--cd" in codex and str(tmp_path) in codex
        assert codex[-1] == "PROMPT"
        pi = build_worker_command("pi", tmp_path, "PROMPT")
        assert pi[:3] == ["pi", "--mode", "text"]
        assert "-p" not in pi and "--no-session" not in pi
        claude = build_worker_command("claude", tmp_path, "PROMPT")
        assert claude[0] == "claude" and claude[-1] == "PROMPT"
        assert "-p" not in claude

    def test_codex_worker_carves_git_writable_root(self, tmp_path):
        # codex >=0.160 seatbelt denies .git writes under workspace-write; the
        # worker argv must carve the repo's .git back into the writable roots.
        codex = build_worker_command("codex", tmp_path, "PROMPT")
        carve = "sandbox_workspace_write.writable_roots=[{}]".format(json.dumps(str(tmp_path / ".git")))
        assert carve in codex
        assert "-c" in codex
        # json string escaping keeps the value a valid TOML basic string
        assert carve.count('"') == 2


class TestSessionHostDetection:
    MARKERS = ("CODEX_SANDBOX", "PI_CODING_AGENT", "CLAUDECODE")

    def clear_markers(self, monkeypatch):
        for marker in self.MARKERS:
            monkeypatch.delenv(marker, raising=False)

    def test_single_marker_wins(self, monkeypatch):
        self.clear_markers(monkeypatch)
        monkeypatch.setenv("PI_CODING_AGENT", "true")
        assert detect_session_host() == "pi"

    def test_no_markers_returns_none_even_with_known_ancestors(self, monkeypatch):
        # the ancestor walk only disambiguates inherited markers; it never
        # invents a host without env evidence
        self.clear_markers(monkeypatch)
        monkeypatch.setattr(autorun_spawn, "_nearest_ancestor_host", lambda candidates: "codex")
        assert detect_session_host() is None

    def test_multiple_markers_resolve_to_nearest_marked_ancestor(self, monkeypatch):
        self.clear_markers(monkeypatch)
        monkeypatch.setenv("CODEX_SANDBOX", "seatbelt")
        monkeypatch.setenv("PI_CODING_AGENT", "true")
        seen: list = []
        monkeypatch.setattr(autorun_spawn, "_nearest_ancestor_host", lambda candidates: seen.append(candidates) or "pi")
        assert detect_session_host() == "pi"
        # only marked hosts are candidates, so an unmarked inner CLI can never
        # win the disambiguation
        assert seen == [("codex", "pi")]

    def test_multiple_markers_walk_miss_returns_none(self, monkeypatch):
        self.clear_markers(monkeypatch)
        monkeypatch.setenv("CODEX_SANDBOX", "seatbelt")
        monkeypatch.setenv("PI_CODING_AGENT", "true")
        monkeypatch.setattr(autorun_spawn, "_nearest_ancestor_host", lambda candidates: None)
        assert detect_session_host() is None


class TestNearestAncestorHost:
    """The parent-chain walk: nearest marked ancestor wins, first hit stops."""

    def stub_chain(self, monkeypatch, chain):
        """Serve ``ps -o ppid=,command= -p <pid>`` from a pid -> (ppid, command)."""
        calls: list = []

        def fake_run(argv, *a, **k):
            pid = int(argv[-1])
            calls.append(pid)
            ppid, command = chain[pid]
            if command is None:
                return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
            return subprocess.CompletedProcess(argv, 0, stdout=f"{ppid} {command}\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        return calls

    def test_stops_at_first_marked_ancestor(self, monkeypatch):
        me = os.getpid()
        calls = self.stub_chain(
            monkeypatch,
            {
                me: (2000, "/bin/zsh -c python3 scripts/autorun_spawn.py spawn"),
                2000: (1900, "node /opt/homebrew/bin/pi --mode text"),
                1900: (1, "launchd"),
            },
        )
        assert autorun_spawn._nearest_ancestor_host(("pi",)) == "pi"
        # nearest hit ends the walk: no further ps fork for the outer chain
        assert calls == [me, 2000]

    def test_skips_unmarked_ancestors(self, monkeypatch):
        me = os.getpid()
        calls = self.stub_chain(
            monkeypatch,
            {
                me: (2000, "/bin/zsh -c python3 scripts/autorun_spawn.py spawn"),
                2000: (1900, "node /opt/homebrew/bin/pi --mode text"),
                1900: (1, "node /opt/homebrew/bin/codex --cd /tmp"),
            },
        )
        assert autorun_spawn._nearest_ancestor_host(("codex",)) == "codex"
        assert calls == [me, 2000, 1900]

    def test_no_match_returns_none(self, monkeypatch):
        me = os.getpid()
        self.stub_chain(
            monkeypatch,
            {
                me: (2000, "/bin/zsh -c python3 scripts/autorun_spawn.py spawn"),
                2000: (1, "launchd"),
            },
        )
        assert autorun_spawn._nearest_ancestor_host(("codex", "pi")) is None

    def test_unreadable_ps_output_returns_none(self, monkeypatch):
        me = os.getpid()
        self.stub_chain(monkeypatch, {me: (0, None)})
        assert autorun_spawn._nearest_ancestor_host(("codex",)) is None


class TestResolveWorkerHostOrder:
    def clear_resolution_env(self, monkeypatch):
        monkeypatch.delenv("SPEC_AUTORUN_HOST", raising=False)
        for marker in ("CODEX_SANDBOX", "PI_CODING_AGENT", "CLAUDECODE"):
            monkeypatch.delenv(marker, raising=False)

    def test_flag_beats_env_and_detection(self, monkeypatch):
        self.clear_resolution_env(monkeypatch)
        monkeypatch.setenv("SPEC_AUTORUN_HOST", "pi")
        monkeypatch.setenv("CODEX_SANDBOX", "seatbelt")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/claude" if name == "claude" else None)
        assert resolve_worker_host("claude") == ("claude", "flag")

    def test_env_var_beats_detection(self, monkeypatch):
        self.clear_resolution_env(monkeypatch)
        monkeypatch.setenv("SPEC_AUTORUN_HOST", "claude")
        monkeypatch.setenv("PI_CODING_AGENT", "true")
        monkeypatch.setattr("shutil.which", lambda name: {"pi": "/pi", "claude": "/claude"}.get(name))
        assert resolve_worker_host(None) == ("claude", "env")

    def test_detection_beats_path_order(self, monkeypatch):
        self.clear_resolution_env(monkeypatch)
        monkeypatch.setenv("PI_CODING_AGENT", "true")
        # codex is first on PATH, but the running session is pi — same-host
        # continuation must win
        monkeypatch.setattr("shutil.which", lambda name: {"codex": "/codex", "pi": "/pi"}.get(name))
        assert resolve_worker_host(None) == ("pi", "session")

    def test_detected_host_missing_on_path_falls_back(self, monkeypatch):
        self.clear_resolution_env(monkeypatch)
        monkeypatch.setenv("PI_CODING_AGENT", "true")
        monkeypatch.setattr("shutil.which", lambda name: "/codex" if name == "codex" else None)
        assert resolve_worker_host(None) == ("codex", "path")

    def test_no_detection_falls_back_to_path_order(self, monkeypatch):
        self.clear_resolution_env(monkeypatch)
        monkeypatch.setattr("shutil.which", lambda name: "/codex" if name == "codex" else None)
        assert resolve_worker_host(None) == ("codex", "path")

    def test_plain_path_fallback_is_quiet(self, monkeypatch, capsys):
        self.clear_resolution_env(monkeypatch)
        monkeypatch.setattr("shutil.which", lambda name: "/codex" if name == "codex" else None)
        assert resolve_worker_host(None) == ("codex", "path")
        assert capsys.readouterr().err == ""

    def test_unresolved_markers_warn_on_path_fallback(self, monkeypatch, capsys):
        # markers say "the session is codex/pi" but no marked CLI resolves and
        # none runs in the process chain: continuing on a PATH host is a
        # different CLI than the session's, so it is recorded and never silent
        self.clear_resolution_env(monkeypatch)
        monkeypatch.setenv("CODEX_SANDBOX", "seatbelt")
        monkeypatch.setenv("PI_CODING_AGENT", "true")
        monkeypatch.setattr(autorun_spawn, "_nearest_ancestor_host", lambda candidates: None)
        monkeypatch.setattr("shutil.which", lambda name: "/codex" if name == "codex" else None)
        assert resolve_worker_host(None) == ("codex", "path")
        err = capsys.readouterr().err
        assert "falling back to PATH order" in err
        assert "codex/pi" in err

    def test_nothing_available_raises(self, monkeypatch):
        self.clear_resolution_env(monkeypatch)
        monkeypatch.setattr("shutil.which", lambda name: None)
        with pytest.raises(AutorunError, match="no worker host available"):
            resolve_worker_host(None)

    def test_unknown_explicit_host_raises(self, monkeypatch):
        with pytest.raises(AutorunError, match="unknown worker host"):
            resolve_worker_host("zcode")

    def test_terminal_command_quotes_paths_and_prompts(self, tmp_path):
        spacey = tmp_path / "pro ject"
        spacey.mkdir(exist_ok=True)
        argv, shell = build_terminal_command(spacey, ["echo", "a b", 'q"uote'])
        assert argv[0] == "osascript"
        assert argv[2].startswith('tell application "Terminal"')
        assert argv[2].endswith("end tell")
        assert 'do script "' in argv[2]
        assert shell.startswith("cd ")
        assert "pro ject" in shell
        # Every double quote the shell command carries must arrive escaped in
        # the AppleScript literal, so osascript never sees a broken string.
        script_line = next(part for part in argv[2].splitlines() if 'do script "' in part)
        assert script_line.endswith('"')
        body = script_line.split('do script "', 1)[1][:-1]
        assert body.replace('\\"', '"') == shell


class TestSessionTty:
    def test_controlling_tty_wins(self, monkeypatch):
        monkeypatch.setattr("autorun_spawn.controlling_tty", lambda: "/dev/ttys009")
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: pytest.fail("ancestor walk must not run when /dev/tty works")
        )
        assert autorun_spawn.session_tty() == "/dev/ttys009"

    def test_unresolved_dev_tty_literal_falls_back_to_ps(self, monkeypatch):
        # macOS answers ttyname() for a /dev/tty descriptor with the literal
        # "/dev/tty" (verified 2026-10-07 in a Terminal tab); that never
        # matches a window's tty, so session_tty must resolve via ps instead
        # — own pid first, which names the real device
        monkeypatch.setattr("autorun_spawn.controlling_tty", lambda: "/dev/tty")
        responses = iter(["999 ttys013 python3 scripts/autorun_spawn.py spawn --root ."])

        def fake_run(argv, *a, **k):
            return subprocess.CompletedProcess(argv, 0, stdout=next(responses, "") + "\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert autorun_spawn.session_tty() == "/dev/ttys013"

    def test_falls_back_to_nearest_ancestor_tty(self, monkeypatch):
        # the agent tool subprocess itself has no tty (??), two levels up the
        # host shell inside the Terminal tab still names the window's tty
        monkeypatch.setattr("autorun_spawn.controlling_tty", lambda: None)
        responses = iter(
            [
                "12345 ?? /bin/bash --noprofile",
                "999 ttys004 pi --mode text -- '$spec autorun'",
            ]
        )

        def fake_run(argv, *a, **k):
            return subprocess.CompletedProcess(argv, 0, stdout=next(responses, "") + "\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert autorun_spawn.session_tty() == "/dev/ttys004"

    def test_all_ancestors_without_tty_return_none(self, monkeypatch):
        monkeypatch.setattr("autorun_spawn.controlling_tty", lambda: None)

        def fake_run(argv, *a, **k):
            return subprocess.CompletedProcess(argv, 0, stdout="1 ?? /bin/bash\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert autorun_spawn.session_tty() is None

    def test_ps_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr("autorun_spawn.controlling_tty", lambda: None)

        def fake_run(*a, **k):
            raise subprocess.TimeoutExpired(a[0], 5)

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert autorun_spawn.session_tty() is None

    def test_unreadable_ps_output_returns_none(self, monkeypatch):
        monkeypatch.setattr("autorun_spawn.controlling_tty", lambda: None)

        def fake_run(argv, *a, **k):
            return subprocess.CompletedProcess(argv, 0, stdout="garbage\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert autorun_spawn.session_tty() is None

    def test_empty_ps_output_returns_none(self, monkeypatch):
        monkeypatch.setattr("autorun_spawn.controlling_tty", lambda: None)

        def fake_run(argv, *a, **k):
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert autorun_spawn.session_tty() is None


class TestWindowRecycle:
    def test_parse_spawn_result(self):
        assert parse_spawn_result("42 /dev/ttys009\n") == (42, "/dev/ttys009")
        assert parse_spawn_result("tab 1 of window 1") == (None, None)
        assert parse_spawn_result("") == (None, None)
        assert parse_spawn_result("42 ttys009") == (None, None)
        assert parse_spawn_result("not-an-int /dev/ttys009") == (None, None)

    def test_parse_window_lookup(self):
        assert parse_window_lookup("7 1 false\n") == (7, 1, False)
        assert parse_window_lookup("7 3 true") == (7, 3, True)
        assert parse_window_lookup("") == (None, 0, False)
        assert parse_window_lookup("7 1") == (None, 0, False)
        assert parse_window_lookup("7 0 false") == (None, 0, False)
        assert parse_window_lookup("abc 1 false") == (None, 0, False)

    def test_lookup_applescript_reports_tab_count_and_busy(self):
        script = autorun_spawn.build_window_lookup_applescript("/dev/ttys009")
        assert "set matchTabs to (count of tabs of w)" in script
        assert "set matchBusy to (busy of t)" in script
        assert 'return match & " " & (matchTabs as string) & " " & (matchBusy as string)' in script

    def test_worker_running_on_tty(self, monkeypatch):
        # real process shapes: codex is a node shebang script (comm=node), the
        # pi launcher a /bin/sh shim, claude a native Mach-O binary — the
        # command line is the only column carrying the host name for all three
        ps_output = (
            "ttys009  node /opt/homebrew/bin/codex --cd /tmp -- PROMPT\n"
            "ttys009  /bin/sh /usr/local/bin/pi --mode text -- PROMPT2\n"
            "ttys010  /opt/homebrew/bin/claude\n"
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout=ps_output, stderr=""),
        )
        assert worker_running_on_tty("codex", "/dev/ttys009") is True
        assert worker_running_on_tty("pi", "/dev/ttys009") is True
        assert worker_running_on_tty("claude", "/dev/ttys010") is True
        assert worker_running_on_tty("claude", "/dev/ttys009") is False
        assert worker_running_on_tty("codex", "/dev/ttys011") is False

    def test_worker_matching_ignores_bare_words_beyond_leading_tokens(self, monkeypatch):
        # tightened matching: a worker name past the leading tokens is command
        # data, not a running worker (`vim pi` must not read as pi on the tty)
        noise = "ttys009  sh -c echo pi\nttys009  tail -f logs/pi\nttys009  vim pi\n"
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout=noise, stderr=""),
        )
        assert worker_running_on_tty("pi", "/dev/ttys009") is False
        assert worker_running_on_tty("claude", "/dev/ttys009") is False

    def test_worker_running_on_tty_survives_ps_failure(self, monkeypatch):
        def fake_run(*a, **k):
            raise subprocess.TimeoutExpired(a[0], 5)

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert worker_running_on_tty("codex", "/dev/ttys009") is False

    def test_close_argv_shape_and_validation(self):
        argv = build_close_argv(42, 3, 120, prev_tty="/dev/ttys012", **chain_context())
        assert argv[:2] == ["/bin/sh", "-c"]
        script = argv[2]
        assert "sleep 3" in script
        # F17 three-phase escalation: natural-exit grace, then SIGTERM, then
        # SIGKILL — each phase polls on a delay, closes only when free, and
        # maps anything unexpected to osascript-error; without session pids
        # (the bypass) no kill segment is generated
        assert "repeat" in script
        assert "delay 2" in script
        assert 'if waited >= 10 then return "busy-timeout"' in script
        assert 'if waited >= 15 then return "busy-timeout"' in script
        assert 'if waited >= 95 then return "busy-timeout"' in script
        assert "kill -TERM" not in script
        assert "kill -KILL" not in script
        assert 'closed|multi-tab|window-gone) result="$out" ;;' in script
        assert 'closed|multi-tab|window-gone|busy-timeout) result="$out" ;;' in script
        assert "osascript-error" in script
        # the close helper re-checks both guards before touching the window:
        # tab count (Terminal cannot close one tab) and a still-running session
        assert "count of tabs of window id 42" in script
        assert "busy of tab 1 of window id 42" in script
        assert "close window id 42" in script
        assert f">> {EVENTS_PATH}" in script
        # with session pids + worker name: both escalation kills target their
        # process groups behind the F19 identity interlock (a reused pid whose
        # command line does not name the worker never matches)
        argv = build_close_argv(
            42, 3, 120, prev_tty="/dev/ttys012", session_pids=[4321, 4321, 99], worker_name="claude", **chain_context()
        )
        script = argv[2]
        assert "for pid in 99 4321; do" in script
        assert 'case "$cmd" in *"claude "*|*/claude)' in script
        assert 'kill -TERM -"$pgid"' in script
        assert 'kill -KILL -"$pgid"' in script
        # without a worker name the interlock withholds the escalation entirely
        argv = build_close_argv(42, 3, 120, prev_tty="/dev/ttys012", session_pids=[4321], **chain_context())
        assert "kill -TERM" not in argv[2]
        with pytest.raises(AutorunError):
            build_close_argv(-1, 3, 120, prev_tty="/dev/ttys012", **chain_context())
        with pytest.raises(AutorunError):
            build_close_argv("42", 3, 120, prev_tty="/dev/ttys012", **chain_context())
        with pytest.raises(AutorunError):
            build_close_argv(42, -1, 120, prev_tty="/dev/ttys012", **chain_context())
        with pytest.raises(AutorunError):
            build_close_argv(42, 3, -1, prev_tty="/dev/ttys012", **chain_context())
        with pytest.raises(AutorunError):
            build_close_argv(42, 3, 120, prev_tty="/dev/ttys012", **chain_context(chain="bogus"))
        with pytest.raises(AutorunError):
            build_close_argv(42, 3, 120, prev_tty="/dev/ttys012", **chain_context(round_index=None))
        with pytest.raises(AutorunError):
            build_close_argv(42, 3, 120, prev_tty="/dev/ttys012", **chain_context(pass_index=2))
        with pytest.raises(AutorunError):
            build_close_argv(42, 3, 120, prev_tty="", **chain_context())
        with pytest.raises(AutorunError):
            build_close_argv(42, 3, 120, prev_tty="/dev/ttys012", **chain_context(events_path=Path("rel.jsonl")))
        with pytest.raises(AutorunError):
            build_close_argv(42, 3, 120, prev_tty="/dev/ttys012", session_pids=[0], **chain_context())
        with pytest.raises(AutorunError):
            build_close_argv(42, 3, 120, prev_tty="/dev/ttys012", session_pids=["99"], **chain_context())

    def test_close_argv_event_line_is_parseable_json(self):
        argv = build_close_argv(42, 3, 120, prev_tty="/dev/ttys012", **chain_context())
        match = re.search(r"printf '(.+)' \"\$now\" \"\$result\" \"\$waited_ms\"", argv[2])
        assert match is not None
        template = match.group(1)
        # the record carries its own trailing newline: two appends must never
        # glue into one unparseable line (live defect found 2026-10-07)
        assert template.endswith("\\n")
        rendered = (template[:-2] % ("2026-10-07T12:00:00Z", "closed", "3000")) + "\n"
        assert json.loads(rendered) == {
            "kind": "recycle_close",
            "at": "2026-10-07T12:00:00Z",
            "chain": "autorun",
            "round": 1,
            "window_id": 42,
            "prev_tty": "/dev/ttys012",
            "result": "closed",
            "waited_ms": 3000,
        }

    def test_close_argv_pass_context_uses_pass_key(self):
        argv = build_close_argv(
            42,
            3,
            120,
            prev_tty="/dev/ttys012",
            **chain_context(chain="autoplan", round_index=None, pass_index=7),
        )
        match = re.search(r"printf '(.+)' \"\$now\"", argv[2])
        assert match is not None
        payload = json.loads((match.group(1)[:-2] % ("2026-10-07T12:00:00Z", "closed", "1000")) + "\n")
        assert payload["chain"] == "autoplan"
        assert payload["pass"] == 7
        assert "round" not in payload

    def test_recycle_schedules_close(self, monkeypatch):
        monkeypatch.setattr("autorun_spawn.worker_pids_on_tty", lambda worker, tty: [4321])
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="7 1 false\n", stderr=""),
        )
        scheduled: list = []
        monkeypatch.setattr(
            "autorun_spawn.schedule_window_close",
            lambda window_id, delay, wait, **kwargs: scheduled.append((window_id, delay, wait, kwargs)),
        )
        result = recycle_previous_window("/dev/ttys012", 42, "/dev/ttys009", "claude", **chain_context())
        assert result == {
            "status": "scheduled",
            "window_id": 7,
            "delay_seconds": 3,
            "close_wait_seconds": 120,
        }
        # F17: the lingering session's pids ride along to the close helper
        assert scheduled == [
            (
                7,
                3,
                120,
                {
                    "chain": "autorun",
                    "round_index": 1,
                    "pass_index": None,
                    "prev_tty": "/dev/ttys012",
                    "events_path": EVENTS_PATH,
                    "session_pids": [4321],
                    "worker_name": "claude",
                },
            )
        ]

    def test_recycle_schedules_close_when_session_still_running(self, monkeypatch):
        # The spawning session is almost always still finishing its round
        # summary when recycle runs; the helper now waits (bounded) for it to
        # exit instead of giving up on the first busy observation.
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: True)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="7 1 true\n", stderr=""),
        )
        scheduled: list = []
        monkeypatch.setattr(
            "autorun_spawn.schedule_window_close",
            lambda window_id, delay, wait, **kwargs: scheduled.append((window_id, delay, wait)),
        )
        result = recycle_previous_window("/dev/ttys012", 42, "/dev/ttys009", "claude", **chain_context())
        assert result["status"] == "scheduled"
        assert scheduled == [(7, 3, 120)]

    def test_recycle_skips_multi_tab_window(self, monkeypatch):
        # Terminal cannot close a single tab, so a window the user merged other
        # tabs into stays open — and why it stayed open is recorded
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: True)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="7 3 false\n", stderr=""),
        )
        scheduled: list = []
        monkeypatch.setattr(
            "autorun_spawn.schedule_window_close",
            lambda window_id, delay, wait, **kwargs: scheduled.append(window_id),
        )
        result = recycle_previous_window("/dev/ttys012", 42, "/dev/ttys009", "claude", **chain_context())
        assert result["status"] == "skipped"
        assert "3 tabs" in result["reason"]
        assert scheduled == []

    def test_recycle_skips_when_lookup_times_out(self, monkeypatch):
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: True)

        def fake_run(argv, *a, **k):
            if argv[0] == "osascript":
                raise subprocess.TimeoutExpired(argv, 30)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        scheduled: list = []
        monkeypatch.setattr(
            "autorun_spawn.schedule_window_close",
            lambda window_id, delay, wait, **kwargs: scheduled.append(window_id),
        )
        result = recycle_previous_window("/dev/ttys012", 42, "/dev/ttys009", "claude", **chain_context())
        assert result["status"] == "skipped"
        assert "did not answer" in result["reason"]
        # matrix row "lookup 超时": the reason names the osascript bound
        assert f"within {chain_support.OSASCRIPT_TIMEOUT_SECONDS}s" in result["reason"]
        assert scheduled == []

    def test_verify_worker_start_split_from_recycle(self, monkeypatch):
        # F17: the worker-observation poll moved out of recycle into the
        # verify gate — recycle no longer skips on an unobserved worker
        monkeypatch.setattr("autorun_spawn.WORKER_START_TIMEOUT_SECONDS", 0)
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: False)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="42 1 false\n", stderr=""),
        )
        verdict = verify_worker_start("claude", 42, "/dev/ttys009")
        assert verdict["status"] == "dead-tab"  # tab idle — the worker died
        assert "never started on /dev/ttys009 within 0s (tab idle)" in verdict["reason"]
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="42 1 true\n", stderr=""),
        )
        verdict = verify_worker_start("claude", 42, "/dev/ttys009")
        assert verdict["status"] == "late-start"  # busy tab — something runs
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: True)
        monkeypatch.setattr("autorun_spawn.WORKER_START_TIMEOUT_SECONDS", 1)
        verdict = verify_worker_start("claude", 42, "/dev/ttys009")
        assert verdict["status"] == "running"
        verdict = verify_worker_start("", 42, "/dev/ttys009")  # bypass trusts
        assert verdict["status"] == "running"
        # and recycle itself proceeds regardless of the worker observation
        result = recycle_previous_window("/dev/ttys012", 42, "/dev/ttys009", "claude", **chain_context())
        assert result["status"] != "skipped" or "not observed" not in result.get("reason", "")

    def test_recycle_skips_without_prev_tty(self):
        result = recycle_previous_window(None, 42, "/dev/ttys009", "claude", **chain_context())
        assert result["status"] == "skipped"
        assert "no controlling Terminal" in result["reason"]

    def test_recycle_skips_without_spawn_ids(self):
        result = recycle_previous_window("/dev/ttys012", None, None, "claude", **chain_context())
        assert result["status"] == "skipped"
        assert "unavailable" in result["reason"]

    def test_recycle_skips_when_prev_window_missing(self, monkeypatch):
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: True)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="\n", stderr=""),
        )
        result = recycle_previous_window("/dev/ttys012", 42, "/dev/ttys009", "claude", **chain_context())
        assert result["status"] == "skipped"
        assert "no Terminal window hosts" in result["reason"]

    def test_recycle_skips_same_window(self, monkeypatch):
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: True)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="42 1 false\n", stderr=""),
        )
        result = recycle_previous_window("/dev/ttys012", 42, "/dev/ttys009", "claude", **chain_context())
        assert result["status"] == "skipped"
        assert "spawned window" in result["reason"]

    def test_recycle_custom_command_skips_verification(self, monkeypatch):
        calls: list = []

        def fake_run(*a, **k):
            calls.append(a[0])
            if a[0][0] == "ps":
                return subprocess.CompletedProcess(a[0], 0, stdout="", stderr="")
            return subprocess.CompletedProcess(a[0], 0, stdout="7 1 false\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        scheduled: list = []
        monkeypatch.setattr(
            "autorun_spawn.schedule_window_close",
            lambda window_id, delay, wait, **kwargs: scheduled.append(window_id),
        )
        result = recycle_previous_window("/dev/ttys012", 42, "/dev/ttys009", "", **chain_context())
        assert result["status"] == "scheduled"
        assert scheduled == [7]
        assert all(argv[0] != "ps" for argv in calls)

    def test_spawn_records_recycle_state(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.controlling_tty", lambda: "/dev/ttys012")

        def fake_run(*a, **k):
            argv = a[0]
            if argv[0] == "ps":
                return subprocess.CompletedProcess(argv, 0, stdout="ttys009  /usr/local/bin/claude\n", stderr="")
            if argv[0] == "osascript" and "do script" in argv[2]:
                return subprocess.CompletedProcess(argv, 0, stdout="42 /dev/ttys009\n", stderr="")
            return subprocess.CompletedProcess(argv, 0, stdout="7 1 false\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        scheduled: list = []
        monkeypatch.setattr(
            "autorun_spawn.schedule_window_close",
            lambda window_id, delay, wait, **kwargs: scheduled.append((window_id, delay, wait, kwargs)),
        )
        code = main(["spawn", "--root", str(tmp_path), "--host", "claude", "--format", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["window_recycle"] == {
            "status": "scheduled",
            "window_id": 7,
            "delay_seconds": 3,
            "close_wait_seconds": 120,
        }
        # the fake ps output carries no pid column, so the escalation target
        # set is empty (the helper still runs its natural-exit grace phase)
        assert scheduled[0][3]["session_pids"] == []
        assert scheduled == [
            (
                7,
                3,
                120,
                {
                    "chain": "autorun",
                    "round_index": 1,
                    "pass_index": None,
                    "prev_tty": "/dev/ttys012",
                    "events_path": tmp_path / ".spec" / "autorun" / "events.jsonl",
                    "session_pids": [],
                    "worker_name": "claude",
                },
            )
        ]
        state = json.loads((tmp_path / ".spec" / "autorun" / "chain.json").read_text(encoding="utf-8"))
        assert state["window_recycle"]["window_id"] == 7
        assert state["window_recycle"]["close_wait_seconds"] == 120
        assert state["prev_tty"] == "/dev/ttys012"

    def test_spawn_refuses_when_worker_never_starts(self, tmp_path, capsys, monkeypatch):
        # F17 "dead tab": the window opened but the worker died instantly
        # (codex's probabilistic startup failure) — the dead window is closed
        # quietly and the round is refused with zero state written, so a
        # re-run resumes exactly here instead of stranding the chain.
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.controlling_tty", lambda: "/dev/ttys012")
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: False)
        monkeypatch.setattr("autorun_spawn.WORKER_START_TIMEOUT_SECONDS", 0)

        def fake_run(*a, **k):
            argv = a[0]
            if argv[0] == "osascript" and "do script" in argv[2]:
                return subprocess.CompletedProcess(argv, 0, stdout="42 /dev/ttys009\n", stderr="")
            # every other osascript (window lookups, quiet close) answers empty
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        code = main(["spawn", "--root", str(tmp_path), "--host", "claude", "--format", "json"])
        captured = capsys.readouterr()
        assert code == 1
        assert "never started on /dev/ttys009 within 0s (tab idle)" in captured.err
        assert "refusing to record the round" in captured.err
        assert not (tmp_path / ".spec" / "autorun" / "chain.json").exists()
        assert not (tmp_path / ".spec" / "autorun" / "spawns.jsonl").exists()
        events = (tmp_path / ".spec" / "autorun" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        event = json.loads(events[-1])
        assert event["kind"] == "spawn_refusal"
        assert "never started" in event["message"]

    def test_spawn_continues_when_worker_is_late_start(self, tmp_path, capsys, monkeypatch):
        # F17 "late start": the worker was not observed within the bound but
        # the new tab is busy — something is running, so the spawn proceeds
        # fail-open exactly as before.
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.controlling_tty", lambda: "/dev/ttys012")
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: False)
        monkeypatch.setattr("autorun_spawn.WORKER_START_TIMEOUT_SECONDS", 0)

        def fake_run(*a, **k):
            argv = a[0]
            if argv[0] == "osascript" and "do script" in argv[2]:
                return subprocess.CompletedProcess(argv, 0, stdout="42 /dev/ttys009\n", stderr="")
            if argv[0] == "osascript":
                # the verify lookup (new tty ttys009) reports a busy tab — the
                # late-start signal; the prev-window lookup (ttys012) finds none
                if "ttys009" in argv[2]:
                    return subprocess.CompletedProcess(argv, 0, stdout="42 1 true\n", stderr="")
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        code = main(["spawn", "--root", str(tmp_path), "--host", "claude", "--format", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        # the recycle still ran: the prev-window lookup answered empty, so the
        # skip records "no Terminal window hosts" (not the worker reason)
        assert payload["window_recycle"]["status"] == "skipped"
        assert "no Terminal window hosts" in payload["window_recycle"]["reason"]
        state = json.loads((tmp_path / ".spec" / "autorun" / "chain.json").read_text(encoding="utf-8"))
        assert state["round"] == 1

    def test_spawn_osascript_timeout_refuses_without_recording(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)

        def fake_run(argv, *a, **k):
            if argv[0] == "osascript":
                raise subprocess.TimeoutExpired(argv, 30)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        code = main(["spawn", "--root", str(tmp_path), "--host", "claude"])
        assert code == 1
        assert "did not answer" in capsys.readouterr().err
        assert not (tmp_path / ".spec" / "autorun" / "chain.json").exists()


class TestRefusalClosesSpawnedWindow:
    """2026-10-09 交接未确认 = 不留进程: a refusal after the window opened
    must close that window synchronously — an alive worker the chain never
    recorded is a duplicate executor on re-run. A2 (unparseable open reply)
    recovers the front window behind the identity interlock; A5 (any failure
    between the confirmed open and the recorded state) closes the confirmed
    window; A6 (append_audit, after the state write) stays outside the guard
    — the handoff is confirmed there and the window is legitimate."""

    @staticmethod
    def _probe(monkeypatch, open_reply, front_reply, worker_runs, pids):
        """Fake subprocess.run: the open osascript answers ``open_reply``, the
        front-window probe answers ``front_reply``; every close-helper argv is
        recorded and every other call answers empty."""
        close_argv = []

        def fake_run(argv, *a, **k):
            if argv[0] == "/bin/sh":
                close_argv.append(argv)
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
            if argv[0] == "osascript" and "do script" in argv[2]:
                return subprocess.CompletedProcess(argv, 0, stdout=open_reply, stderr="")
            if argv[0] == "osascript" and "front window" in argv[2]:
                return subprocess.CompletedProcess(argv, 0, stdout=front_reply, stderr="")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: worker_runs)
        monkeypatch.setattr("autorun_spawn.worker_pids_on_tty", lambda worker, tty: list(pids))
        return close_argv

    def test_unparseable_reply_closes_the_recovered_window(self, tmp_path, capsys, monkeypatch):
        # A2 主缺陷: the open reply carries no id/tty — the just-opened window
        # is recovered via the front window (accepted only because it
        # verifiably runs OUR worker) and closed before the refusal raises.
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.controlling_tty", lambda: "/dev/ttys012")
        close_argv = self._probe(monkeypatch, "front window opened\n", "77 /dev/ttys042\n", True, [4242])
        code = main(["spawn", "--root", str(tmp_path), "--host", "claude", "--format", "json"])
        captured = capsys.readouterr()
        assert code == 1
        assert "cannot confirm the handoff" in captured.err  # refusal text frozen verbatim
        assert captured.out == ""
        assert not (tmp_path / ".spec" / "autorun" / "chain.json").exists()
        assert not (tmp_path / ".spec" / "autorun" / "spawns.jsonl").exists()
        assert len(close_argv) == 1
        script = close_argv[0][2]
        assert "sleep 0" in script  # synchronous: no scheduling delay
        assert '"kind": "refusal_close"' in script
        assert '"round": 1' in script
        assert '"window_id": 77' in script
        assert '"prev_tty": "/dev/ttys042"' in script
        assert 'case "$cmd" in *"claude "*|*/claude)' in script  # identity interlock carries
        events = (tmp_path / ".spec" / "autorun" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        event = json.loads(events[-1])
        assert event["kind"] == "spawn_refusal"
        assert "cannot confirm the handoff" in event["message"]

    def test_unparseable_reply_without_recovery_skips_the_close(self, tmp_path, capsys, monkeypatch):
        # A2 fail-open: the front window is not OUR worker (or no window at
        # all) — no close target, the refusal proceeds with the text frozen
        # verbatim and an honest skip (never a forced close of a user window)
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.controlling_tty", lambda: "/dev/ttys012")
        close_argv = self._probe(monkeypatch, "front window opened\n", "", False, [4242])
        code = main(["spawn", "--root", str(tmp_path), "--host", "claude"])
        captured = capsys.readouterr()
        assert code == 1
        assert "cannot confirm the handoff" in captured.err
        assert close_argv == []  # nothing identity-confirmed → nothing closed
        assert not (tmp_path / ".spec" / "autorun" / "chain.json").exists()

    def test_failure_between_open_and_state_closes_the_window(self, tmp_path, capsys, monkeypatch):
        # A5: the open reply parsed fine but the guarded stretch fails before
        # the state is written — the confirmed window is closed before the
        # failure propagates (re-run would otherwise spawn a second worker
        # for the same round)
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.controlling_tty", lambda: "/dev/ttys012")
        close_argv = self._probe(monkeypatch, "42 /dev/ttys009\n", "", True, [991])
        monkeypatch.setattr("autorun_spawn.read_window_geometry", lambda tty: (10, 20, 30, 40))
        monkeypatch.setattr(
            "autorun_spawn.apply_window_geometry",
            lambda window_id, geometry: (_ for _ in ()).throw(AutorunError("geometry boom")),
        )
        code = main(["spawn", "--root", str(tmp_path), "--host", "claude", "--format", "json"])
        captured = capsys.readouterr()
        assert code == 1
        assert "geometry boom" in captured.err
        assert len(close_argv) == 1
        script = close_argv[0][2]
        assert '"kind": "refusal_close"' in script
        assert '"window_id": 42' in script
        assert '"prev_tty": "/dev/ttys009"' in script
        assert not (tmp_path / ".spec" / "autorun" / "chain.json").exists()
        assert not (tmp_path / ".spec" / "autorun" / "spawns.jsonl").exists()

    def test_state_written_then_audit_failure_keeps_the_window(self, tmp_path, capsys, monkeypatch):
        # A6 (裁定不修): once the state is written the handoff is confirmed —
        # the window is the chain's legitimate worker and stays open even if
        # the audit append fails afterwards
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.controlling_tty", lambda: "/dev/ttys012")
        close_argv = self._probe(monkeypatch, "42 /dev/ttys009\n", "", True, [991])

        def broken_append(path, record):
            raise RuntimeError("audit disk full")

        monkeypatch.setattr("autorun_spawn.append_audit", broken_append)
        with pytest.raises(RuntimeError, match="audit disk full"):
            main(["spawn", "--root", str(tmp_path), "--host", "claude"])
        assert close_argv == []  # confirmed handoff → never closed
        assert (tmp_path / ".spec" / "autorun" / "chain.json").exists()
        assert not (tmp_path / ".spec" / "autorun" / "spawns.jsonl").exists()

    def test_refusal_close_template_renders_contract_lines(self):
        contract = AUDIT_FIELD_CONTRACT
        argv = build_close_argv(
            42,
            0,
            chain_support.REFUSAL_CLOSE_WAIT_SECONDS,
            prev_tty="/dev/ttys042",
            session_pids=[4242],
            worker_name="claude",
            chain="autorun",
            round_index=3,
            events_path=EVENTS_PATH,
            event_kind="refusal_close",
        )
        match = re.search(r"printf '(.+)' \"\$now\" \"\$result\" \"\$waited_ms\"", argv[2])
        assert match is not None
        template = match.group(1)
        assert template.endswith("\\n")
        for result in sorted(contract["recycle_close_result_domain"]):
            event = json.loads((template[:-2] % ("2026-10-09T12:00:00Z", result, "3000")) + "\n")
            assert set(event) == contract["event_keys"]["refusal_close"]
            assert event["kind"] == "refusal_close"
            assert event["chain"] == "autorun"
            assert event["round"] == 3
            assert event["window_id"] == 42
            assert event["prev_tty"] == "/dev/ttys042"
            assert event["result"] == result


class TestChannelSplit:
    """Matrix row "stdout 可解析": refusals keep stdout empty (or legal JSON)
    with the reason on stderr; success payloads stay machine-parseable."""

    def test_errors_on_stderr_stdout_parseable(self, tmp_path, capsys):
        code = main(["plan", "--root", str(tmp_path)])
        captured = capsys.readouterr()
        assert code == 3
        assert captured.err.startswith("error:")
        assert captured.out == ""

    def test_matrix_refusals_keep_stdout_empty(self, tmp_path, capsys, monkeypatch):
        # the F3 negative samples in one channel sweep: every refusal path
        # writes its reason to stderr and leaves stdout untouched
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        refusals = [
            (["spawn", "--root", str(tmp_path), "--max-rounds", "0", "--dry-run"], "--max-rounds must be >= 1"),
            (["spawn", "--root", str(tmp_path / "nope")], "not a directory"),
        ]
        state = tmp_path / ".spec" / "autorun" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"round": "soon"}), encoding="utf-8")
        refusals.append((["spawn", "--root", str(tmp_path), "--dry-run"], "non-integer round"))
        for argv, reason in refusals:
            assert main(argv) == 1, argv
            captured = capsys.readouterr()
            assert captured.out == "", argv
            assert captured.err.startswith("error:"), argv
            assert reason in captured.err, argv

    def test_success_dry_run_stdout_is_parseable_json(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        assert main(["spawn", "--root", str(tmp_path), "--dry-run", "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["dry_run"] is True

    def test_missing_root_refuses(self, tmp_path, capsys):
        code = main(["plan", "--root", str(tmp_path / "nope")])
        assert code == 1
        assert "not a directory" in capsys.readouterr().err


class TestNoPlanErrorContract:
    def test_no_plan_error_is_exit_three(self):
        assert isinstance(NoPlanError("x"), AutorunError)


# --- F13: the chain-state audit field contract, frozen in tests -----------------
#
# plans/04 §F13: chain reads stay tolerant by design (missing keys get read
# defaults, unknown keys pass through, old files are never rewritten), so the
# contract has no runtime validator — it lives here as frozen sets. Add-only:
# a new key joins alongside the old (F14 will add model / model_injection);
# a renamed or dropped key is a contract break. These tests pin key sets and
# value domains only; window sizes and exact message texts are behavior, not
# contract, and stay out.

ISO_Z_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

AUDIT_FIELD_CONTRACT = {
    # chain.json after a real spawn (updated_at form; window_recycle always
    # recorded, whatever the recycle outcome)
    "chain_state_required": frozenset(
        {
            "round",
            "host",
            "host_source",
            "plan_docs",
            "max_rounds",
            "updated_at",
            "shell_command",
            "prev_tty",
            "window_recycle",
            "model",
        }
    ),
    # written only by the corrupt-rebuild path, with a recovery event
    "chain_state_conditional": frozenset({"recovered_from"}),
    # spawns.jsonl row after a real spawn (spawned_at form)
    "spawns_row_required": frozenset(
        {
            "round",
            "host",
            "host_source",
            "plan_docs",
            "max_rounds",
            "spawned_at",
            "shell_command",
            "prev_tty",
            "window_recycle",
            "model",
            "model_injection",
        }
    ),
    # F14: the per-spawn injection fact written on every spawns row; the
    # chain state carries only the lock, never the level
    "model_injection_domain": frozenset({"full", "partial", "none", "custom"}),
    # spawn payload keys beyond the audit row: every payload carries dry_run;
    # the face key reports what would run (osascript argv) vs what did (stdout)
    "payload_mode_extra": {
        "dry-run": frozenset({"dry_run", "osascript"}),
        "real": frozenset({"dry_run", "terminal"}),
    },
    # in-memory read defaults for missing writer keys; the file never changes
    "read_defaults": {"window_recycle": {}, "host": "unknown"},
    "window_recycle_status_domain": frozenset({"scheduled", "skipped", "planned"}),
    "window_recycle_keys": {
        "scheduled": frozenset({"status", "window_id", "delay_seconds", "close_wait_seconds"}),
        "skipped": frozenset({"status", "reason"}),
        "planned": frozenset({"status"}),
    },
    "event_kind_domain": frozenset({"spawn_refusal", "recycle_close", "refusal_close", "recovery"}),
    "event_keys": {
        "spawn_refusal": frozenset({"kind", "at", "chain", "type", "message"}),
        "recycle_close": frozenset({"kind", "at", "chain", "round", "window_id", "prev_tty", "result", "waited_ms"}),
        "refusal_close": frozenset({"kind", "at", "chain", "round", "window_id", "prev_tty", "result", "waited_ms"}),
        "recovery": frozenset({"kind", "at", "chain", "action", "detail"}),
    },
    "recycle_close_result_domain": frozenset({"closed", "multi-tab", "window-gone", "busy-timeout", "osascript-error"}),
    "recovery_action_domain": frozenset({"recovered_state", "state_diverged"}),
    # state_diverged is an autoplan-only audit; this chain never writes it
    "recovery_detail_keys": {
        "recovered_state": frozenset({"source", "state_file"}),
    },
    # status derivation: spawn_refusal rows only, chain dropped
    "last_refusal_keys": frozenset({"kind", "at", "type", "message"}),
    # the write_lock_holder line (sort_keys json + newline, never cleared)
    "lock_holder_keys": frozenset({"pid", "command", "at", "chain"}),
    "host_source_domain": frozenset({"flag", "env", "session", "path", "custom"}),
    # status payload keys computed beyond the state; state keys pass through
    "status_derived_required": frozenset(
        {
            "project",
            "chain_state_file",
            "state_status",
            "started",
            "lock",
            "refusals_recorded",
            "last_refusal",
            "plan",
            "active_package",
            "resume",
            "model",
        }
    ),
    # present only when more than one active package is discovered
    "status_derived_conditional": frozenset({"active_packages"}),
    # the autoplan faces never arrive on this chain's status
    "status_foreign_keys": frozenset({"detail_docs", "kind", "target", "pass", "max_passes"}),
}


# --- F13 legacy-format samples --------------------------------------------------
#
# Real historical shapes only: committed bytes come from git 4031293 (the F1
# window-recycle commit, which first carried chain runtime state; verified
# byte-exact), constructed shapes come from that era's committed writer code.
# Every sample must read back without a crash and without a rewrite — the
# corrupt-rebuild is the only write a read performs, and it leaves a recovery
# event behind.
#
# Redaction: the private repository absolute path inside committed samples is
# mechanically rewritten to ~/dev/spec (the public-export gate forbids home
# paths in the exported tree; tests/ ships publicly). Equivalence proof:
# fixture bytes == committed bytes after that single-point substitution —
# no other byte differs, and the substitution site count is exactly one.

# git 4031293:.spec/autorun/chain.json — round 2 of the 2026-10-07 chain run:
# pretty JSON, sorted keys, trailing newline; the F1 scheduled recycle
# already carries close_wait_seconds.
LEGACY_STATE_F1_BYTES = b"""{
  "host": "custom",
  "host_source": "custom",
  "max_rounds": 20,
  "plan_docs": [
    "plans/00-master-plan.md"
  ],
  "prev_tty": "/dev/ttys013",
  "round": 2,
  "shell_command": "cd ~/dev/spec && sleep 8",
  "updated_at": "2026-10-07T13:52:10Z",
  "window_recycle": {
    "close_wait_seconds": 120,
    "delay_seconds": 3,
    "status": "scheduled",
    "window_id": 44468
  }
}
"""

# git 4031293:.spec/autorun/spawns.jsonl — both committed rows: sort_keys
# single-line JSONL, one row per spawn. Round 1 skipped the recycle (no
# Terminal window hosts /dev/tty); round 2 scheduled the bounded close.
LEGACY_SPAWNS_ROUND1_ROW_BYTES = (
    b'{"host": "custom", "host_source": "custom", "max_rounds": 20, '
    b'"plan_docs": ["plans/00-master-plan.md"], "prev_tty": "/dev/tty", '
    b'"round": 1, "shell_command": "cd ~/dev/spec && sleep 8", '
    b'"spawned_at": "2026-10-07T13:46:47Z", '
    b'"window_recycle": {"reason": "no Terminal window hosts /dev/tty", "status": "skipped"}}\n'
)
LEGACY_SPAWNS_ROUND2_ROW_BYTES = (
    b'{"host": "custom", "host_source": "custom", "max_rounds": 20, '
    b'"plan_docs": ["plans/00-master-plan.md"], "prev_tty": "/dev/ttys013", '
    b'"round": 2, "shell_command": "cd ~/dev/spec && sleep 8", '
    b'"spawned_at": "2026-10-07T13:52:10Z", '
    b'"window_recycle": {"close_wait_seconds": 120, "delay_seconds": 3, '
    b'"status": "scheduled", "window_id": 44468}}\n'
)

# git 4031293:.spec/autorun/events.jsonl — the round-2 close record: a single
# line without a trailing newline (the historical tail predates the F1
# anti-glue guard; reads tolerate it as-is).
LEGACY_RECYCLE_CLOSE_BYTES = (
    b'{"kind": "recycle_close", "at": "2026-10-07T13:52:13Z", "chain": "autorun", '
    b'"round": 2, "window_id": 44468, "prev_tty": "/dev/ttys013", '
    b'"result": "closed", "waited_ms": 3000}'
)

# The pre-F1 writer shape (git 4031293^:scripts/autorun_spawn.py): the same
# key set, but the era's scheduled recycle had no bounded close wait. No
# state file from before F1 survives (F1 was the first commit carrying chain
# runtime state), so the sample is constructed from that era's writer code.
LEGACY_PRE_F1_STATE = {
    "round": 2,
    "host": "custom",
    "host_source": "custom",
    "plan_docs": ["plans/00-master-plan.md"],
    "max_rounds": 20,
    "updated_at": "2026-10-07T13:52:10Z",
    "shell_command": "cd ~/dev/spec && sleep 8",
    "prev_tty": "/dev/ttys013",
    "window_recycle": {"status": "scheduled", "window_id": 44468, "delay_seconds": 3},
}

# The bare-minimum hand-written state: an index and a timestamp.
LEGACY_MINIMAL_STATE = {"round": 3, "updated_at": "2026-10-06T09:15:00Z"}

# Forward-compat: keys no current writer produces (F14 adds `model` to the
# row; future_field stands in for anything later) pass through reads and
# renders untouched — reads never drop what they do not know.
LEGACY_FORWARD_COMPAT_STATE = dict(
    json.loads(LEGACY_STATE_F1_BYTES.decode("utf-8")),
    model={"id": "gpt-5.2", "reasoning": "high"},
    future_field=1,
)

# git 4031293 committed .spec/autorun/chain.lock as a 0-byte file: the lock
# is a flock mutex; the content is advice for status.
LEGACY_LOCK_EMPTY_BYTES = b""

# A crashed holder's line (write_lock_holder sort_keys form): the pid is
# dead, so the probe degrades to stale-content advice.
LEGACY_STALE_HOLDER_LINE = (
    b'{"at": "2026-10-07T13:00:00Z", "chain": "autorun", '
    b'"command": "python3 scripts/autorun_spawn.py spawn --root .", "pid": 999999}\n'
)

# The defect residue the F1 anti-glue guard fixed: two records glued onto one
# unterminated tail line form one unparseable line, skipped whole — no
# phantom refusal may be counted out of half a record.
LEGACY_GLUED_EVENTS_BYTES = (
    b'{"kind": "recycle_close", "at": "2026-10-07T13:52:13Z", "chain": "autorun", '
    b'"round": 2, "window_id": 44468, "prev_tty": "/dev/ttys013", "result": "closed", "waited_ms": 3000}'
    b'{"kind": "spawn_refusal", "at": "2026-10-07T14:00:00Z", "chain": "autorun", '
    b'"type": "AutorunError", "message": "glued onto the unguarded tail"}'
)


class TestAuditFieldContract:
    """plans/04 §F13: the frozen audit field contract, driven through every
    writer face (payload, state, audit row, events, lock, status derivation)."""

    def test_contract_dict_is_internally_consistent(self):
        contract = AUDIT_FIELD_CONTRACT
        # the state and the audit row differ only in the timestamp key and
        # the F14 spawns-row-only injection level (the lock rides in both)
        assert contract["chain_state_required"] - {"updated_at"} == (
            contract["spawns_row_required"] - {"spawned_at", "model_injection"}
        )
        assert "updated_at" in contract["chain_state_required"]
        assert "spawned_at" in contract["spawns_row_required"]
        assert contract["chain_state_conditional"].isdisjoint(contract["chain_state_required"])
        assert "window_recycle" in contract["chain_state_required"]
        # read defaults fill writer keys that old files may lack
        assert set(contract["read_defaults"]) == {"window_recycle", "host"}
        for key in contract["read_defaults"]:
            assert key in contract["chain_state_required"]
        # every window_recycle status has exactly one pinned key shape
        assert set(contract["window_recycle_keys"]) == contract["window_recycle_status_domain"]
        for keys in contract["window_recycle_keys"].values():
            assert "status" in keys
        # every event kind has a pinned key set; all events carry kind/at/chain
        assert set(contract["event_keys"]) == contract["event_kind_domain"]
        for keys in contract["event_keys"].values():
            assert {"kind", "at", "chain"} <= keys
        # last_refusal is a spawn_refusal derivation: chain is dropped
        assert contract["last_refusal_keys"] < contract["event_keys"]["spawn_refusal"]
        # payloads extend the audit row; the modes share only dry_run
        dry, real = contract["payload_mode_extra"]["dry-run"], contract["payload_mode_extra"]["real"]
        assert dry & real == {"dry_run"}
        assert dry | real == {"dry_run", "osascript", "terminal"}
        # the lock holder line is its own contract
        assert contract["lock_holder_keys"] == frozenset({"pid", "command", "at", "chain"})
        # derived status keys never collide with state keys, and the other
        # chain's faces never arrive here
        assert contract["status_derived_conditional"].isdisjoint(contract["status_derived_required"])
        assert contract["status_derived_conditional"].isdisjoint(contract["chain_state_required"])
        assert contract["status_foreign_keys"].isdisjoint(contract["status_derived_required"])
        assert contract["status_foreign_keys"].isdisjoint(contract["chain_state_required"])
        # round|pass is per-chain: this chain's recycle event carries round
        assert "round" in contract["event_keys"]["recycle_close"]
        assert "pass" not in contract["event_keys"]["recycle_close"]
        # this chain never writes the autoplan-only divergence audit
        assert set(contract["recovery_detail_keys"]) <= contract["recovery_action_domain"]
        assert contract["recovery_detail_keys"]["recovered_state"] == frozenset({"source", "state_file"})

    def test_dry_run_payload_matches_contract(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        assert main(["spawn", "--root", str(tmp_path), "--host", "claude", "--dry-run", "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        contract = AUDIT_FIELD_CONTRACT
        assert set(payload) == contract["spawns_row_required"] | contract["payload_mode_extra"]["dry-run"]
        assert payload["dry_run"] is True
        assert payload["round"] == 1
        assert isinstance(payload["round"], int) and not isinstance(payload["round"], bool)
        # the cap is opt-in: the no-flag spawn records null and forwards none
        assert payload["max_rounds"] is None
        assert ISO_Z_PATTERN.fullmatch(payload["spawned_at"])
        assert payload["host"] == "claude"
        assert payload["host_source"] in contract["host_source_domain"]
        assert payload["plan_docs"] == [".spec/plans/README.md"]
        assert isinstance(payload["shell_command"], str)
        assert payload["prev_tty"] == "/dev/ttys012"
        assert payload["osascript"][0] == "osascript"
        assert payload["window_recycle"] == {"status": "planned"}
        assert set(payload["window_recycle"]) == contract["window_recycle_keys"]["planned"]
        # the dry run still takes the lock: one holder line in the writer's form
        holder_line = (tmp_path / ".spec" / "autorun" / "chain.lock").read_text(encoding="utf-8")
        assert set(json.loads(holder_line)) == contract["lock_holder_keys"]

    def test_real_spawn_state_row_and_payload_match_contract(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="4421 /dev/ttys042", stderr=""),
        )
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: True)
        assert main(["spawn", "--root", str(tmp_path), "--host", "claude", "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        contract = AUDIT_FIELD_CONTRACT
        assert set(payload) == contract["spawns_row_required"] | contract["payload_mode_extra"]["real"]
        assert payload["dry_run"] is False
        assert payload["terminal"] == "4421 /dev/ttys042"
        assert ISO_Z_PATTERN.fullmatch(payload["spawned_at"])
        assert payload["host_source"] in contract["host_source_domain"]

        state = json.loads((tmp_path / ".spec" / "autorun" / "chain.json").read_text(encoding="utf-8"))
        assert set(state) == contract["chain_state_required"]
        assert ISO_Z_PATTERN.fullmatch(state["updated_at"])
        assert state["round"] == 1
        recycle = state["window_recycle"]
        assert recycle["status"] in contract["window_recycle_status_domain"]
        assert set(recycle) == contract["window_recycle_keys"][recycle["status"]]

        rows = (tmp_path / ".spec" / "autorun" / "spawns.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(rows) == 1
        row = json.loads(rows[0])
        assert set(row) == contract["spawns_row_required"]
        assert ISO_Z_PATTERN.fullmatch(row["spawned_at"])
        assert row["window_recycle"] == recycle

    def test_spawn_refusal_event_matches_contract(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout="", stderr="not authorized"),
        )
        assert main(["spawn", "--root", str(tmp_path), "--host", "claude"]) == 1
        capsys.readouterr()
        contract = AUDIT_FIELD_CONTRACT
        events = (tmp_path / ".spec" / "autorun" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(events) == 1
        event = json.loads(events[0])
        assert set(event) == contract["event_keys"]["spawn_refusal"]
        assert event["kind"] == "spawn_refusal"
        assert event["chain"] == "autorun"
        assert event["type"] == "AutorunError"
        assert ISO_Z_PATTERN.fullmatch(event["at"])
        assert isinstance(event["message"], str)
        # a refused spawn never reaches the state or the audit row
        assert not (tmp_path / ".spec" / "autorun" / "chain.json").exists()
        assert not (tmp_path / ".spec" / "autorun" / "spawns.jsonl").exists()

    def test_corrupt_state_rebuild_writes_contract_shaped_state_and_recovery_event(self, tmp_path, capsys):
        chain_dir = tmp_path / ".spec" / "autorun"
        chain_dir.mkdir(parents=True, exist_ok=True)
        (chain_dir / "chain.json").write_text("{half-written", encoding="utf-8")
        (chain_dir / "spawns.jsonl").write_bytes(LEGACY_SPAWNS_ROUND2_ROW_BYTES)
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        contract = AUDIT_FIELD_CONTRACT
        assert payload["state_status"] == "recovered"
        assert payload["recovered_from"] == "spawns.jsonl line 1"
        state = json.loads((chain_dir / "chain.json").read_text(encoding="utf-8"))
        # the rebuilt state is the audit row (spawned_at intact) plus provenance;
        # the legacy row predates F14's model keys, so the expected set comes
        # from the row itself (the rebuild adds provenance, invents nothing)
        row_keys = set(json.loads(LEGACY_SPAWNS_ROUND2_ROW_BYTES.decode("utf-8")))
        assert set(state) == row_keys | contract["chain_state_conditional"]
        assert state["recovered_from"] == "spawns.jsonl line 1"
        assert state["round"] == 2
        events = (chain_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        event = json.loads(events[-1])
        assert set(event) == contract["event_keys"]["recovery"]
        assert event["kind"] == "recovery"
        assert event["chain"] == "autorun"
        assert event["action"] == "recovered_state"
        assert event["action"] in contract["recovery_action_domain"]
        assert ISO_Z_PATTERN.fullmatch(event["at"])
        assert set(event["detail"]) == contract["recovery_detail_keys"]["recovered_state"]
        assert event["detail"] == {"source": "spawns.jsonl line 1", "state_file": "chain.json"}

    def test_close_argv_produces_contract_recycle_close_lines(self):
        contract = AUDIT_FIELD_CONTRACT
        argv = build_close_argv(42, 3, 120, prev_tty="/dev/ttys012", **chain_context())
        script = argv[2]
        match = re.search(r"printf '(.+)' \"\$now\" \"\$result\" \"\$waited_ms\"", script)
        assert match is not None
        template = match.group(1)
        assert template.endswith("\\n")
        # every frozen result value renders a contract-shaped line — the
        # writer cannot produce a result outside the domain
        for result in sorted(contract["recycle_close_result_domain"]):
            event = json.loads((template[:-2] % ("2026-10-07T12:00:00Z", result, "3000")) + "\n")
            assert set(event) == contract["event_keys"]["recycle_close"]
            assert event["result"] == result
            assert ISO_Z_PATTERN.fullmatch(event["at"])
            assert event["chain"] == "autorun"
            assert event["round"] == 1
            assert event["window_id"] == 42
            assert event["prev_tty"] == "/dev/ttys012"
            assert event["waited_ms"] == 3000

    def test_window_recycle_shapes_cover_the_status_domain(self, tmp_path, capsys, monkeypatch):
        contract = AUDIT_FIELD_CONTRACT
        observed = set()
        # scheduled: one-tab previous window, worker already on the new tty
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: True)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="7 1 false\n", stderr=""),
        )
        monkeypatch.setattr(
            "autorun_spawn.schedule_window_close",
            lambda window_id, delay, wait, **kwargs: None,
        )
        result = recycle_previous_window("/dev/ttys012", 42, "/dev/ttys009", "claude", **chain_context())
        assert set(result) == contract["window_recycle_keys"]["scheduled"]
        assert result["status"] == "scheduled"
        assert isinstance(result["window_id"], int) and not isinstance(result["window_id"], bool)
        assert result["window_id"] > 0
        assert isinstance(result["delay_seconds"], int)
        assert isinstance(result["close_wait_seconds"], int)
        observed.add(result["status"])
        # skipped: no controlling Terminal in the spawning session
        result = recycle_previous_window(None, 42, "/dev/ttys009", "claude", **chain_context())
        assert set(result) == contract["window_recycle_keys"]["skipped"]
        assert result["status"] == "skipped"
        assert isinstance(result["reason"], str)
        observed.add(result["status"])
        # planned: the dry-run face reports what a real spawn would record
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        assert main(["spawn", "--root", str(tmp_path), "--dry-run", "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert set(payload["window_recycle"]) == contract["window_recycle_keys"]["planned"]
        assert payload["window_recycle"]["status"] == "planned"
        observed.add(payload["window_recycle"]["status"])
        # the three shapes exhaust the status domain
        assert observed == contract["window_recycle_status_domain"]

    def test_status_derived_key_set_matches_contract(self, tmp_path, capsys):
        chain_dir = tmp_path / ".spec" / "autorun"
        chain_dir.mkdir(parents=True, exist_ok=True)
        (chain_dir / "chain.json").write_bytes(LEGACY_STATE_F1_BYTES)
        contract = AUDIT_FIELD_CONTRACT
        state_keys = set(json.loads(LEGACY_STATE_F1_BYTES.decode("utf-8")))
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert set(payload) == contract["status_derived_required"] | state_keys
        assert "active_packages" not in payload
        assert set(payload).isdisjoint(contract["status_foreign_keys"])
        assert payload["lock"] == {"status": "free"}
        assert payload["started"] is True
        assert payload["state_status"] == "ok"
        # more than one active package is the one conditional derived key
        make_active_package(tmp_path, slug="2026-01-01_first-package")
        make_active_package(tmp_path, slug="2026-01-02_second-package")
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert set(payload) == (
            contract["status_derived_required"] | contract["status_derived_conditional"] | state_keys
        )

    def test_last_refusal_derives_from_spawn_refusals_only(self, tmp_path, capsys):
        chain_dir = tmp_path / ".spec" / "autorun"
        chain_dir.mkdir(parents=True, exist_ok=True)
        (chain_dir / "chain.json").write_text(
            json.dumps({"round": 3, "updated_at": "2026-10-07T12:00:00Z"}) + "\n", encoding="utf-8"
        )
        stream = [
            {
                "kind": "spawn_refusal",
                "at": "2026-10-07T12:00:01Z",
                "chain": "autorun",
                "type": "AutorunError",
                "message": "first refusal",
            },
            json.loads(LEGACY_RECYCLE_CLOSE_BYTES.decode("utf-8")),
            {
                "kind": "recovery",
                "at": "2026-10-07T12:00:03Z",
                "chain": "autorun",
                "action": "recovered_state",
                "detail": {"source": "spawns.jsonl line 1", "state_file": "chain.json"},
            },
            {
                "kind": "spawn_refusal",
                "at": "2026-10-07T12:00:04Z",
                "chain": "autorun",
                "type": "AutorunError",
                "message": "second refusal",
            },
        ]
        (chain_dir / "events.jsonl").write_text(
            "\n".join(json.dumps(record) for record in stream) + "\n", encoding="utf-8"
        )
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        contract = AUDIT_FIELD_CONTRACT
        # the count spans the whole file; recycle_close and recovery rows never
        # cover the derivation
        assert payload["refusals_recorded"] == 2
        assert set(payload["last_refusal"]) == contract["last_refusal_keys"]
        assert payload["last_refusal"] == {
            "kind": "spawn_refusal",
            "at": "2026-10-07T12:00:04Z",
            "type": "AutorunError",
            "message": "second refusal",
        }


class TestModelIdentity:
    """plans/04 §F14: the chain model identity — lock, propagation, the four
    conflict refusals, the bypass, the injection levels, and status visibility.

    Flag literals are never hardcoded: every argv/shell_command shape assert
    renders through chain_support.HOST_MODEL_FLAGS (the single mapping), so a
    host CLI flag rename stays a one-line fix in the mapping table.
    """

    IDENTITY = {"id": "anthropic/opus-5.5", "reasoning": "high"}

    # sentinel: the pre-F14 chain.json carries no model key at all
    NO_MODEL_KEY = object()

    @staticmethod
    def write_state(root: Path, model, host="pi") -> Path:
        """chain.json with a locked round-3 chain; ``model`` dict | None (null
        lock) | NO_MODEL_KEY (pre-F14 chain), ``host`` as recorded."""
        state_file = root / ".spec" / "autorun" / "chain.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "round": 3,
            "host": host,
            "host_source": "flag",
            "plan_docs": [".spec/plans/00-master-plan.md"],
            "max_rounds": 20,
            "updated_at": "2026-10-08T00:00:00Z",
            "shell_command": "cd /p && pi --mode text -- '$spec autorun'",
            "prev_tty": None,
            "window_recycle": {},
        }
        if model is not TestModelIdentity.NO_MODEL_KEY:
            state["model"] = model
        state_file.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return state_file

    @staticmethod
    def model_flag_segment(host: str, component: str, value: str) -> str:
        """The rendered flag segment for one component, straight from the mapping."""
        prefix, template = chain_support.HOST_MODEL_FLAGS[host][component]
        return " ".join([*prefix, template.format(value)])

    @staticmethod
    def refused(tmp_path, capsys, argv, *, match):
        """One refusal: exit 1, stderr carrying both values, one refusal event."""
        state_file = tmp_path / ".spec" / "autorun" / "chain.json"
        before = state_file.read_bytes()
        events_file = tmp_path / ".spec" / "autorun" / "events.jsonl"
        events_before = events_file.read_bytes() if events_file.is_file() else b""
        code = main(argv)
        captured = capsys.readouterr()
        assert code == 1
        assert match in captured.err
        events = events_file.read_text(encoding="utf-8").splitlines()
        assert len(events) == len(events_before.splitlines()) + 1
        event = json.loads(events[-1])
        assert set(event) == AUDIT_FIELD_CONTRACT["event_keys"]["spawn_refusal"]
        assert event["kind"] == "spawn_refusal"
        assert event["chain"] == "autorun"
        assert event["type"] == "AutorunError"
        assert match in event["message"]
        # refusals write no state
        assert state_file.read_bytes() == before

    # --- lock ------------------------------------------------------------------

    def test_first_spawn_with_flags_locks_chain_state_and_audit_row(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="4421 /dev/ttys042", stderr=""),
        )
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: True)
        argv = ["spawn", "--root", str(tmp_path), "--host", "pi", "--format", "json"] + [
            "--model",
            "anthropic/opus-5.5",
            "--reasoning",
            "high",
        ]
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["model"] == self.IDENTITY
        assert payload["model_injection"] == "full"
        state = json.loads((tmp_path / ".spec" / "autorun" / "chain.json").read_text(encoding="utf-8"))
        assert state["model"] == self.IDENTITY
        assert "model_injection" not in state
        row = json.loads((tmp_path / ".spec" / "autorun" / "spawns.jsonl").read_text(encoding="utf-8").splitlines()[0])
        assert row["model"] == self.IDENTITY
        assert row["model_injection"] in AUDIT_FIELD_CONTRACT["model_injection_domain"]

    def test_first_spawn_without_flags_locks_null(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="4421 /dev/ttys042", stderr=""),
        )
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: True)
        assert main(["spawn", "--root", str(tmp_path), "--host", "pi", "--format", "json"]) == 0
        json.loads(capsys.readouterr().out)
        state = json.loads((tmp_path / ".spec" / "autorun" / "chain.json").read_text(encoding="utf-8"))
        assert state["model"] is None
        row = json.loads((tmp_path / ".spec" / "autorun" / "spawns.jsonl").read_text(encoding="utf-8").splitlines()[0])
        assert row["model"] is None
        assert row["model_injection"] == "none"

    # --- propagation ------------------------------------------------------------

    @pytest.mark.parametrize("host", ["pi", "codex", "claude"])
    def test_no_flags_continuation_injects_the_locked_value(self, tmp_path, capsys, monkeypatch, host):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        self.write_state(tmp_path, self.IDENTITY, host=host)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        argv = ["spawn", "--root", str(tmp_path), "--host", host, "--format", "json", "--dry-run"]
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["model"] == self.IDENTITY
        assert payload["model_injection"] == "full"
        shell = payload["shell_command"]
        # the id flag segment renders straight from the shared mapping table
        assert self.model_flag_segment(host, "id", "anthropic/opus-5.5") in shell
        # every host maps the reasoning level through its own flag form
        assert self.model_flag_segment(host, "reasoning", "high") in shell
        # both segments sit between the worker binary and the prompt
        worker_at = shell.index(" ")
        prompt_at = shell.index("'$spec autorun") if host != "claude" else shell.index("/spec:autorun")
        assert shell.index(self.model_flag_segment(host, "id", "anthropic/opus-5.5")) > worker_at
        assert shell.index(self.model_flag_segment(host, "id", "anthropic/opus-5.5")) < prompt_at

    def test_propagation_survives_a_real_second_round(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        self.write_state(tmp_path, self.IDENTITY)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="4421 /dev/ttys042", stderr=""),
        )
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: True)
        assert main(["spawn", "--root", str(tmp_path), "--host", "pi", "--format", "json"]) == 0
        json.loads(capsys.readouterr().out)
        state = json.loads((tmp_path / ".spec" / "autorun" / "chain.json").read_text(encoding="utf-8"))
        assert state["round"] == 4
        assert state["model"] == self.IDENTITY  # the lock carried unchanged
        rows = (tmp_path / ".spec" / "autorun" / "spawns.jsonl").read_text(encoding="utf-8").splitlines()
        row = json.loads(rows[-1])
        assert row["model"] == self.IDENTITY
        assert row["model_injection"] == "full"
        assert self.model_flag_segment("pi", "id", "anthropic/opus-5.5") in row["shell_command"]

    # --- the four conflict refusals ---------------------------------------------

    def test_conflict_id_refuses_with_both_values(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        self.write_state(tmp_path, self.IDENTITY)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        argv = ["spawn", "--root", str(tmp_path), "--host", "pi", "--dry-run", "--model", "openai/gpt-5.2"]
        self.refused(
            tmp_path,
            capsys,
            argv,
            match="locked id 'anthropic/opus-5.5' vs incoming id 'openai/gpt-5.2'",
        )

    def test_conflict_reasoning_refuses_with_both_values(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        self.write_state(tmp_path, self.IDENTITY)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--host",
            "pi",
            "--dry-run",
            "--model",
            "anthropic/opus-5.5",
            "--reasoning",
            "max",
        ]
        self.refused(tmp_path, capsys, argv, match="locked reasoning 'high' vs incoming reasoning 'max'")

    def test_conflict_absent_vs_present_reasoning_refuses(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        self.write_state(tmp_path, {"id": "anthropic/opus-5.5"})
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--host",
            "pi",
            "--dry-run",
            "--model",
            "anthropic/opus-5.5",
            "--reasoning",
            "high",
        ]
        self.refused(tmp_path, capsys, argv, match="locked reasoning absent vs incoming reasoning 'high'")

    def test_conflict_null_lock_then_value_refuses(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        self.write_state(tmp_path, None)  # model key present, locked null
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--host",
            "pi",
            "--dry-run",
            "--model",
            "anthropic/opus-5.5",
        ]
        self.refused(
            tmp_path,
            capsys,
            argv,
            match='locked model: null but this spawn carries {"id": "anthropic/opus-5.5"}',
        )

    def test_conflict_resolved_host_change_with_identity_refuses(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        self.write_state(tmp_path, self.IDENTITY)  # chain host recorded as pi
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--host",
            "codex",
            "--dry-run",
            "--model",
            "anthropic/opus-5.5",
            "--reasoning",
            "high",
        ]
        self.refused(
            tmp_path,
            capsys,
            argv,
            match="resolved host 'codex' differs from the chain host 'pi'",
        )

    def test_reasoning_without_model_refuses(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        argv = ["spawn", "--root", str(tmp_path), "--host", "pi", "--dry-run", "--reasoning", "high"]
        code = main(argv)
        captured = capsys.readouterr()
        assert code == 1
        assert "--reasoning requires --model" in captured.err

    def test_empty_model_refuses_through_refusal_path(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        self.write_state(tmp_path, self.IDENTITY)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        argv = ["spawn", "--root", str(tmp_path), "--host", "pi", "--dry-run", "--model", ""]
        self.refused(tmp_path, capsys, argv, match="--model must be a non-empty model id")

    def test_whitespace_reasoning_refuses_through_refusal_path(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        self.write_state(tmp_path, self.IDENTITY)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--host",
            "pi",
            "--dry-run",
            "--model",
            "anthropic/opus-5.5",
            "--reasoning",
            "   ",
        ]
        self.refused(
            tmp_path,
            capsys,
            argv,
            match="--reasoning must be a non-empty level when given",
        )

    def test_padded_meaningful_values_pass_through_verbatim(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="4421 /dev/ttys042", stderr=""),
        )
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: True)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--host",
            "pi",
            "--format",
            "json",
            "--model",
            " anthropic/opus-5.5 ",
            "--reasoning",
            " high ",
        ]
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        # original passthrough: no trim, no normalization — the locked value
        # keeps the caller's padding verbatim
        assert payload["model"] == {"id": " anthropic/opus-5.5 ", "reasoning": " high "}

    def test_malformed_lock_refuses(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        self.write_state(tmp_path, "not-an-object")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        argv = ["spawn", "--root", str(tmp_path), "--host", "pi", "--dry-run", "--model", "anthropic/opus-5.5"]
        self.refused(tmp_path, capsys, argv, match="malformed model identity lock")

    # --- the no-lock no-flags default stays byte-identical ----------------------

    def test_no_lock_no_flags_default_argv_is_unchanged(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        assert main(["spawn", "--root", str(tmp_path), "--host", "pi", "--format", "json", "--dry-run"]) == 0
        payload = json.loads(capsys.readouterr().out)
        # the pre-F14 literal form: nothing between the worker binary and the prompt
        assert payload["model"] is None
        assert payload["model_injection"] == "none"
        assert payload["shell_command"] == f"cd {tmp_path} && pi --mode text -- '$spec autorun'"

    # --- the --command bypass ---------------------------------------------------

    def test_bypass_records_identity_without_injecting(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--command",
            "sleep 8",
            "--model",
            "anthropic/opus-5.5",
            "--reasoning",
            "high",
            "--format",
            "json",
            "--dry-run",
        ]
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["host"] == "custom"
        assert payload["model"] == self.IDENTITY
        assert payload["model_injection"] == "custom"
        assert payload["shell_command"] == f"cd {tmp_path} && sleep 8"

    def test_bypass_first_lock_then_host_spawn_refuses(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="4421 /dev/ttys042", stderr=""),
        )
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: True)
        bypass_argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--command",
            "sleep 8",
            "--model",
            "anthropic/opus-5.5",
            "--format",
            "json",
        ]
        assert main(bypass_argv) == 0
        json.loads(capsys.readouterr().out)
        state = json.loads((tmp_path / ".spec" / "autorun" / "chain.json").read_text(encoding="utf-8"))
        assert state["host"] == "custom"
        assert state["model"] == {"id": "anthropic/opus-5.5"}
        # a later host spawn carrying the identity correctly refuses against
        # the bypass's custom host (the identity is namespace-bound)
        host_argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--host",
            "pi",
            "--dry-run",
            "--model",
            "anthropic/opus-5.5",
        ]
        self.refused(tmp_path, capsys, host_argv, match="resolved host 'pi' differs from the chain host 'custom'")

    # --- partial injection ------------------------------------------------------

    def test_partial_when_host_lacks_a_reasoning_flag(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        # construct the host-lacking-reasoning-flag form straight in the table
        monkeypatch.setitem(chain_support.HOST_MODEL_FLAGS["claude"], "reasoning", None)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--host",
            "claude",
            "--model",
            "claude-opus-4-6",
            "--reasoning",
            "max",
            "--format",
            "json",
            "--dry-run",
        ]
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["model"] == {"id": "claude-opus-4-6", "reasoning": "max"}
        assert payload["model_injection"] == "partial"
        shell = payload["shell_command"]
        assert self.model_flag_segment("claude", "id", "claude-opus-4-6") in shell
        # the reasoning level never reaches the worker argv
        assert " max" not in shell

    def test_none_injection_for_null_identity(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        assert main(["spawn", "--root", str(tmp_path), "--host", "claude", "--format", "json", "--dry-run"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["model"] is None
        assert payload["model_injection"] == "none"

    # --- status visibility -------------------------------------------------------

    def test_status_renders_the_locked_model_line(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        self.write_state(tmp_path, self.IDENTITY)
        spawns_file = tmp_path / ".spec" / "autorun" / "spawns.jsonl"
        spawns_file.write_text(
            json.dumps(
                {
                    "round": 3,
                    "host": "pi",
                    "model": self.IDENTITY,
                    "model_injection": "full",
                    "spawned_at": "2026-10-08T00:00:00Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["model"] == {"id": "anthropic/opus-5.5", "reasoning": "high", "injection": "full"}
        assert main(["status", "--root", str(tmp_path)]) == 0
        assert "model: anthropic/opus-5.5 [high] (injection: full)" in capsys.readouterr().out

    def test_status_renders_worker_default_when_null(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        self.write_state(tmp_path, None)
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["model"] is None
        assert main(["status", "--root", str(tmp_path)]) == 0
        assert "model: none (worker default)" in capsys.readouterr().out

    # --- old-chain compatibility --------------------------------------------------

    def test_legacy_state_without_model_key_first_locks_on_flags(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        # the F1-era shape: no model key at all (pre-F14 chain)
        state_file = tmp_path / ".spec" / "autorun" / "chain.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_bytes(LEGACY_STATE_F1_BYTES)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="4421 /dev/ttys042", stderr=""),
        )
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: True)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--command",
            "sleep 8",
            "--model",
            "anthropic/opus-5.5",
            "--format",
            "json",
        ]
        assert main(argv) == 0
        json.loads(capsys.readouterr().out)
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["model"] == {"id": "anthropic/opus-5.5"}

    def test_legacy_state_without_model_key_reads_as_unlocked_status(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        state_file = tmp_path / ".spec" / "autorun" / "chain.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_bytes(LEGACY_STATE_F1_BYTES)
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["model"] is None  # absent key reads as the worker default


class TestLegacyStateSamples:
    """F13 acceptance: every old-format sample (committed bytes or a shape
    constructed from that era's writer code) reads back through status without
    a crash and without a rewrite — the corrupt-rebuild (covered above) is the
    only write a read performs."""

    @staticmethod
    def write_state(root: Path, data) -> Path:
        state_file = root / ".spec" / "autorun" / "chain.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            state_file.write_bytes(data)
        else:
            state_file.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return state_file

    @staticmethod
    def status_payload(root: Path, capsys):
        assert main(["status", "--root", str(root), "--format", "json"]) == 0
        return json.loads(capsys.readouterr().out)

    def assert_status_preserves_state_bytes(self, root: Path, capsys, renders=()):
        """json + text renders succeed and leave chain.json byte-identical."""
        state_file = root / ".spec" / "autorun" / "chain.json"
        before = state_file.read_bytes()
        payload = self.status_payload(root, capsys)
        assert payload["started"] is True
        assert payload["state_status"] == "ok"
        assert main(["status", "--root", str(root)]) == 0
        out = capsys.readouterr().out
        for fragment in renders:
            assert fragment in out, fragment
        assert state_file.read_bytes() == before, "reads must never rewrite chain.json"
        return payload

    def test_committed_f1_state_reads_clean(self, tmp_path, capsys):
        self.write_state(tmp_path, LEGACY_STATE_F1_BYTES)
        payload = self.assert_status_preserves_state_bytes(
            tmp_path,
            capsys,
            renders=("round: 2", "host: custom", "updated_at: 2026-10-07T13:52:10Z"),
        )
        assert payload["round"] == 2
        assert payload["host"] == "custom"
        assert payload["host_source"] == "custom"
        assert payload["max_rounds"] == 20
        assert payload["window_recycle"] == {
            "status": "scheduled",
            "window_id": 44468,
            "delay_seconds": 3,
            "close_wait_seconds": 120,
        }
        # direct read: the parsed state is the file, nothing invented
        state, read_status = autorun_spawn.read_state(tmp_path / ".spec" / "autorun", "autorun")
        assert read_status == "ok"
        assert state == json.loads(LEGACY_STATE_F1_BYTES.decode("utf-8"))

    def test_pre_f1_scheduled_form_has_no_close_wait(self, tmp_path, capsys):
        self.write_state(tmp_path, LEGACY_PRE_F1_STATE)
        payload = self.assert_status_preserves_state_bytes(
            tmp_path, capsys, renders=("round: 2", "host: " + LEGACY_PRE_F1_STATE["host"])
        )
        # the pre-F1 scheduled shape reads as-is: no key invented, none dropped
        assert payload["window_recycle"] == {
            "status": "scheduled",
            "window_id": 44468,
            "delay_seconds": 3,
        }

    def test_minimal_state_gets_read_defaults_in_memory(self, tmp_path, capsys):
        self.write_state(tmp_path, LEGACY_MINIMAL_STATE)
        payload = self.assert_status_preserves_state_bytes(
            tmp_path, capsys, renders=("round: 3", "host: unknown", "updated_at: 2026-10-06T09:15:00Z")
        )
        # defaults exist only in memory — the file keeps its two keys
        assert payload["host"] == "unknown"
        assert payload["window_recycle"] == {}
        state, read_status = chain_support.read_state(tmp_path / ".spec" / "autorun", "autorun")
        assert read_status == "ok"
        assert state == {
            "round": 3,
            "updated_at": "2026-10-06T09:15:00Z",
            "host": "unknown",
            "window_recycle": {},
        }

    def test_forward_compat_unknown_keys_pass_through(self, tmp_path, capsys):
        self.write_state(tmp_path, LEGACY_FORWARD_COMPAT_STATE)
        payload = self.assert_status_preserves_state_bytes(tmp_path, capsys)
        # unknown keys ride through reads and renders untouched
        assert payload["model"] == {"id": "gpt-5.2", "reasoning": "high"}
        assert payload["future_field"] == 1

    def test_committed_zero_byte_lock_reads_free(self, tmp_path, capsys):
        self.write_state(tmp_path, LEGACY_MINIMAL_STATE)
        lock_file = tmp_path / ".spec" / "autorun" / "chain.lock"
        lock_file.write_bytes(LEGACY_LOCK_EMPTY_BYTES)
        payload = self.status_payload(tmp_path, capsys)
        assert payload["lock"] == {"status": "free"}
        assert main(["status", "--root", str(tmp_path)]) == 0
        assert "lock: free" in capsys.readouterr().out

    def test_stale_holder_line_reports_stale(self, tmp_path, capsys, monkeypatch):
        self.write_state(tmp_path, LEGACY_MINIMAL_STATE)
        lock_file = tmp_path / ".spec" / "autorun" / "chain.lock"
        lock_file.write_bytes(LEGACY_STALE_HOLDER_LINE)
        monkeypatch.setattr(autorun_spawn, "_pid_alive", lambda pid: False)
        with open(lock_file, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                payload = self.status_payload(tmp_path, capsys)
                assert payload["lock"] == {"status": "held", "holder": "stale", "pid": 999999}
                assert main(["status", "--root", str(tmp_path)]) == 0
                assert "lock: held (stale content)" in capsys.readouterr().out
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def test_committed_events_tail_tolerated_and_spawns_index_reads(self, tmp_path, capsys):
        chain_dir = tmp_path / ".spec" / "autorun"
        self.write_state(tmp_path, LEGACY_MINIMAL_STATE)
        events_file = chain_dir / "events.jsonl"
        events_file.write_bytes(LEGACY_RECYCLE_CLOSE_BYTES)
        spawns_file = chain_dir / "spawns.jsonl"
        spawns_file.write_bytes(LEGACY_SPAWNS_ROUND1_ROW_BYTES + LEGACY_SPAWNS_ROUND2_ROW_BYTES)
        payload = self.status_payload(tmp_path, capsys)
        # a recycle_close tail is not a refusal
        assert payload["refusals_recorded"] == 0
        assert payload["last_refusal"] is None
        assert main(["status", "--root", str(tmp_path)]) == 0
        capsys.readouterr()
        # reads never rewrite the audit files either
        assert events_file.read_bytes() == LEGACY_RECYCLE_CLOSE_BYTES
        assert spawns_file.read_bytes() == (LEGACY_SPAWNS_ROUND1_ROW_BYTES + LEGACY_SPAWNS_ROUND2_ROW_BYTES)
        # the committed rows parse as contract rows; the last one is the index.
        # Add-only: the F1-era row predates F14's model keys, so it carries a
        # subset of the frozen required set (the F14 writer's exact set is
        # pinned by the real-spawn contract test)
        record, line_number = chain_support.last_audit_record(spawns_file)
        assert line_number == 2
        assert set(record) <= AUDIT_FIELD_CONTRACT["spawns_row_required"]
        assert record["round"] == 2

    def test_glued_events_pair_counts_no_refusals(self, tmp_path, capsys):
        self.write_state(tmp_path, LEGACY_MINIMAL_STATE)
        events_file = tmp_path / ".spec" / "autorun" / "events.jsonl"
        events_file.write_bytes(LEGACY_GLUED_EVENTS_BYTES)
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["refusals_recorded"] == 0
        assert payload["last_refusal"] is None
        assert "skipping unparseable events line" in captured.err
        assert events_file.read_bytes() == LEGACY_GLUED_EVENTS_BYTES

    def test_corrupt_state_without_audit_row_is_never_rewritten(self, tmp_path, capsys):
        chain_dir = tmp_path / ".spec" / "autorun"
        chain_dir.mkdir(parents=True, exist_ok=True)
        corrupt_bytes = b"{half-written"
        (chain_dir / "chain.json").write_bytes(corrupt_bytes)
        payload = self.status_payload(tmp_path, capsys)
        assert payload["state_status"] == "corrupt"
        assert payload["started"] is False
        assert payload["resume"]["action"] == "state_corrupt"
        assert main(["status", "--root", str(tmp_path)]) == 0
        assert "chain: state unreadable (corrupt; see resume)" in capsys.readouterr().out
        # no audit row to rebuild from: the bytes stay exactly as found
        assert (chain_dir / "chain.json").read_bytes() == corrupt_bytes
        assert not (chain_dir / "events.jsonl").exists()
        assert not (chain_dir / "spawns.jsonl").exists()


class TestOptInRoundCap:
    """The round cap is opt-in (the F17 adaptive default was removed): without
    an explicit flag the chain is unbounded — max_rounds records as null and
    the next round's prompt carries no cap flag."""

    def test_no_flag_means_unbounded(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=30)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        assert main(["spawn", "--root", str(tmp_path), "--host", "pi", "--format", "json", "--dry-run"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["max_rounds"] is None
        assert "--max-rounds" not in payload["shell_command"]
        assert "--max-rounds" not in payload["osascript"][2]

    def test_explicit_flag_wins_exactly(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=30)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        argv = ["spawn", "--root", str(tmp_path), "--host", "pi", "--format", "json", "--dry-run", "--max-rounds", "5"]
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["max_rounds"] == 5
        assert "--max-rounds 5" in payload["shell_command"]

    def test_recorded_cap_is_not_sticky(self, tmp_path, capsys, monkeypatch):
        # a cap recorded by an earlier spawn no longer binds later spawns:
        # the flag alone decides, so a no-flag continuation goes unbounded
        write_plan(tmp_path / ".spec" / "plans" / "README.md", unchecked=1)
        make_active_package(tmp_path)
        state_file = tmp_path / ".spec" / "autorun" / "chain.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"round": 3, "max_rounds": 50}) + "\n", encoding="utf-8")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")
        argv = ["spawn", "--root", str(tmp_path), "--host", "pi", "--format", "json", "--dry-run"]
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["max_rounds"] is None
        assert "--max-rounds" not in payload["shell_command"]
