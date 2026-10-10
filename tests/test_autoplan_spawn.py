"""Tests for scripts/autoplan_spawn.py (the /spec:autoplan serial pass-chain spawn)."""

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

import chain_spawn_support as chain_support  # noqa: E402  # type: ignore
from autoplan_spawn import (  # noqa: E402  # type: ignore
    AutoplanSpawnError,
    build_pass_prompt,
    main,
    master_doc_path,
    resolve_target,
)
from autorun_spawn import (  # noqa: E402  # type: ignore
    OSASCRIPT_TIMEOUT_SECONDS,
    AutorunError,
    build_close_argv,
    plan_args_for_prompt,
)


def seed_master(root: Path, path=".spec/plans/README.md"):
    doc = root / path
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("# plan\n\n## confirmed facts\n- fact A\n\n- [ ] open feature 1\n", encoding="utf-8")
    return doc


class TestMasterDocPath:
    def test_default_master_doc(self, tmp_path):
        assert master_doc_path(tmp_path, None) == (tmp_path / ".spec" / "plans" / "README.md").resolve()

    def test_first_explicit_plan_entry_wins(self, tmp_path):
        resolved = master_doc_path(tmp_path, "PLAN.md,plans/01-a.md")
        assert resolved == (tmp_path / "PLAN.md").resolve()


class TestResolveTarget:
    def test_detail_requires_target(self, tmp_path):
        with pytest.raises(AutoplanSpawnError, match="--next detail requires --target"):
            resolve_target(tmp_path, None, "detail")

    def test_target_may_be_new_file_under_root(self, tmp_path):
        assert resolve_target(tmp_path, ".spec/plans/01-auth.md", "detail") == ".spec/plans/01-auth.md"

    def test_target_must_stay_under_root(self, tmp_path):
        with pytest.raises(AutoplanSpawnError, match="escapes the project root"):
            resolve_target(tmp_path, str(tmp_path.parent / "outside.md"), "detail")

    def test_non_detail_kinds_have_no_target(self, tmp_path):
        assert resolve_target(tmp_path, "anything.md", "framework") is None
        assert resolve_target(tmp_path, None, "review") is None


class TestPassPrompt:
    def test_prompt_shapes_per_host(self):
        assert build_pass_prompt("codex", [], 12) == "$spec autoplan continue --max-passes 12"
        assert build_pass_prompt("claude", [], 5) == "/spec:autoplan continue --max-passes 5"

    def test_prompt_without_cap_carries_no_flag(self):
        # unbounded chains forward no cap flag — nothing to hit mid-cluster
        assert build_pass_prompt("codex", [], None) == "$spec autoplan continue"
        assert build_pass_prompt("claude", [], None) == "/spec:autoplan continue"
        assert build_pass_prompt("pi", ["--plan PLAN.md"], None) == "$spec autoplan continue --plan PLAN.md"

    def test_prompt_carries_plan_args(self):
        prompt = build_pass_prompt("pi", ["--plan PLAN.md"], 8)
        assert prompt == "$spec autoplan continue --plan PLAN.md --max-passes 8"

    def test_prompt_quotes_spacey_plan_paths(self):
        # the pass session re-parses the prompt as arguments, so a planning
        # path with a space must survive the round trip intact
        prompt = build_pass_prompt("pi", plan_args_for_prompt("docs/plan v2.md"), 8)
        assert prompt == "$spec autoplan continue --plan 'docs/plan v2.md' --max-passes 8"


class TestSpawnCommand:
    def test_bad_pass_kind_is_usage_error(self, tmp_path, capsys):
        seed_master(tmp_path)
        with pytest.raises(SystemExit) as excinfo:
            main(["spawn", "--root", str(tmp_path), "--next", "bogus", "--dry-run"])
        assert excinfo.value.code == 2
        assert "invalid choice" in capsys.readouterr().err

    def test_dry_run_framework_prints_osascript_without_side_effects(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        code = main(
            [
                "spawn",
                "--root",
                str(tmp_path),
                "--next",
                "framework",
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
        assert payload["pass"] == 1
        assert payload["kind"] == "framework"
        assert payload["target"] is None
        argv = payload["osascript"]
        assert argv[0] == "osascript"
        assert "Terminal" in argv[2]
        assert payload["window_recycle"] == {"status": "planned"}
        assert payload["host"] == "codex"
        assert payload["host_source"] == "flag"
        assert "codex" in payload["shell_command"]
        assert "$spec autoplan continue" in payload["shell_command"]
        assert not (tmp_path / ".spec" / "autoplan" / "chain.json").exists()

    def test_framework_refuses_without_seeded_master(self, tmp_path, capsys, monkeypatch):
        (tmp_path / ".spec").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        code = main(["spawn", "--root", str(tmp_path), "--next", "framework", "--dry-run"])
        assert code == 1
        assert "seeded master document" in capsys.readouterr().err

    def test_framework_accepts_explicit_plan_master(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path, "PLAN.md")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        code = main(
            [
                "spawn",
                "--root",
                str(tmp_path),
                "--next",
                "framework",
                "--plan",
                "PLAN.md",
                "--host",
                "codex",
                "--dry-run",
                "--format",
                "json",
            ]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["plan_docs"] == ["PLAN.md"]

    def test_detail_pass_requires_target(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        code = main(["spawn", "--root", str(tmp_path), "--next", "detail", "--dry-run"])
        assert code == 1
        assert "--target" in capsys.readouterr().err

    def test_detail_dry_run_records_target(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        code = main(
            [
                "spawn",
                "--root",
                str(tmp_path),
                "--next",
                "detail",
                "--target",
                ".spec/plans/01-auth.md",
                "--host",
                "pi",
                "--dry-run",
                "--format",
                "json",
            ]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["kind"] == "detail"
        assert payload["target"] == ".spec/plans/01-auth.md"
        assert not (tmp_path / "docs" / ".spec" / "plans" / "01-auth.md").exists()

    def test_pass_increments_from_chain_state(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        state = tmp_path / ".spec" / "autoplan" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"pass": 4, "kind": "detail", "host": "pi"}), encoding="utf-8")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        code = main(
            ["spawn", "--root", str(tmp_path), "--next", "review", "--host", "pi", "--dry-run", "--format", "json"]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["pass"] == 5

    def test_refuses_at_pass_cap(self, tmp_path, capsys, monkeypatch):
        # the cap is opt-in: refusal happens only when the flag was passed
        seed_master(tmp_path)
        state = tmp_path / ".spec" / "autoplan" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"pass": 12}), encoding="utf-8")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        code = main(["spawn", "--root", str(tmp_path), "--next", "review", "--max-passes", "12", "--dry-run"])
        assert code == 1
        assert "pass cap reached" in capsys.readouterr().err

    def test_uncapped_state_spawns_past_any_count(self, tmp_path, capsys, monkeypatch):
        # no flag = no cap: a chain at pass 12 continues past the count that
        # used to stop it — the recorded cap becomes null and the prompt
        # carries no cap flag
        seed_master(tmp_path)
        state = tmp_path / ".spec" / "autoplan" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"pass": 12, "max_passes": 12}), encoding="utf-8")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        code = main(
            ["spawn", "--root", str(tmp_path), "--next", "review", "--host", "pi", "--format", "json", "--dry-run"]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["pass"] == 13
        assert payload["max_passes"] is None
        assert "--max-passes" not in payload["shell_command"]

    def test_pass_equal_to_cap_is_allowed(self, tmp_path, capsys, monkeypatch):
        # matrix row "round/pass cap" boundary (symmetric with autorun):
        # next == cap passes, next > cap refuses
        seed_master(tmp_path)
        state = tmp_path / ".spec" / "autoplan" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"pass": 11}), encoding="utf-8")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        code = main(
            [
                "spawn",
                "--root",
                str(tmp_path),
                "--next",
                "review",
                "--max-passes",
                "12",
                "--host",
                "pi",
                "--dry-run",
                "--format",
                "json",
            ]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["pass"] == 12

    def test_cap_one_new_chain_starts_at_one(self, tmp_path, capsys, monkeypatch):
        # F12 boundary matrix (symmetric with autorun): a fresh chain
        # (no state) starts at 1 — even cap=1 allows the first spawn
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        code = main(
            [
                "spawn",
                "--root",
                str(tmp_path),
                "--next",
                "review",
                "--max-passes",
                "1",
                "--host",
                "pi",
                "--dry-run",
                "--format",
                "json",
            ]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["pass"] == 1

    def test_state_at_cap_refusal_text_is_verbatim(self, tmp_path, capsys, monkeypatch):
        # F12 boundary matrix: next > cap refuses with the exact frozen text
        # and leaves no audit record behind (the fixture state file stays);
        # the cap is opt-in, so the refusal needs the explicit flag
        seed_master(tmp_path)
        state = tmp_path / ".spec" / "autoplan" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"pass": 12}), encoding="utf-8")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        code = main(["spawn", "--root", str(tmp_path), "--next", "review", "--max-passes", "12", "--dry-run"])
        assert code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip() == "error: pass cap reached: next pass 13 exceeds --max-passes 12"
        assert not (tmp_path / ".spec" / "autoplan" / "spawns.jsonl").exists()

    def test_bool_and_float_pass_refuse_like_non_integer(self, tmp_path, capsys, monkeypatch):
        # F12 strict int gate: bool/float/string values are refused like any
        # non-int — the old lenient int() cast silently accepted True / 1.0
        # and truncated 4.5, so a hand-edited state file could lie
        seed_master(tmp_path)
        state = tmp_path / ".spec" / "autoplan" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        for bad in (True, 1.0, 4.5, "4"):
            state.write_text(json.dumps({"pass": bad}), encoding="utf-8")
            code = main(["spawn", "--root", str(tmp_path), "--next", "review", "--dry-run"])
            assert code == 1, bad
            captured = capsys.readouterr()
            assert captured.out == "", bad
            assert "non-integer pass index" in captured.err, bad
            assert repr(bad) in captured.err, bad
            assert not (tmp_path / ".spec" / "autoplan" / "spawns.jsonl").exists(), bad

    def test_max_passes_zero_refuses_without_chain_writes(self, tmp_path, capsys, monkeypatch):
        # F12 boundary matrix: --max-passes 0 fails fast in main (validate_cap)
        # before any lock/state touch — no chain.json, no spawns.jsonl
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        code = main(["spawn", "--root", str(tmp_path), "--next", "review", "--max-passes", "0", "--dry-run"])
        assert code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip() == "error: --max-passes must be >= 1"
        assert not (tmp_path / ".spec" / "autoplan" / "chain.json").exists()
        assert not (tmp_path / ".spec" / "autoplan" / "spawns.jsonl").exists()

    def test_refuses_when_lock_held(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        lock = tmp_path / ".spec" / "autoplan" / "chain.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
                code = main(["spawn", "--root", str(tmp_path), "--next", "review", "--dry-run"])
                assert code == 1
                assert "parallel chains are refused" in capsys.readouterr().err
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def test_records_state_after_successful_spawn(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="4421 /dev/ttys042", stderr=""),
        )
        monkeypatch.setattr("autoplan_spawn.worker_running_on_tty", lambda worker, tty: True)
        code = main(
            [
                "spawn",
                "--root",
                str(tmp_path),
                "--next",
                "detail",
                "--target",
                ".spec/plans/01-auth.md",
                "--host",
                "claude",
                "--format",
                "json",
            ]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["pass"] == 1
        assert payload["host"] == "claude"
        state = json.loads((tmp_path / ".spec" / "autoplan" / "chain.json").read_text(encoding="utf-8"))
        assert state["pass"] == 1
        assert state["kind"] == "detail"
        assert state["target"] == ".spec/plans/01-auth.md"
        assert state["host"] == "claude"
        assert "updated_at" in state and "spawned_at" not in state
        spawns = (tmp_path / ".spec" / "autoplan" / "spawns.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(spawns) == 1
        assert json.loads(spawns[0])["pass"] == 1
        # The pass chain never touches the autorun chain's state directory.
        assert not (tmp_path / ".spec" / "autorun").exists()

    def test_osascript_failure_refuses_without_recording(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout="", stderr="not authorized"),
        )
        code = main(["spawn", "--root", str(tmp_path), "--next", "framework"])
        assert code == 1
        captured = capsys.readouterr()
        # matrix row "osascript 开窗失败（非零）": refusal is audited, state is not
        assert "osascript" in captured.err
        assert captured.out == ""
        assert not (tmp_path / ".spec" / "autoplan" / "chain.json").exists()
        events = (tmp_path / ".spec" / "autoplan" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(events) == 1
        event = json.loads(events[0])
        assert event["kind"] == "spawn_refusal"
        assert event["chain"] == "autoplan"
        assert event["type"] == "AutoplanSpawnError"
        assert "not authorized" in event["message"]

    def test_osascript_timeout_refuses_without_recording(self, tmp_path, capsys, monkeypatch):
        # matrix row "osascript 开窗超时": the pass spawn shares the autorun
        # OSASCRIPT_TIMEOUT_SECONDS bound, so a hung osascript can never block
        # the chain unbounded (runtime gap closed by this package)
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)

        def fake_run(argv, *a, **k):
            if argv[0] == "osascript":
                raise subprocess.TimeoutExpired(argv, 30)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        code = main(["spawn", "--root", str(tmp_path), "--next", "framework", "--host", "claude"])
        assert code == 1
        captured = capsys.readouterr()
        assert "did not answer" in captured.err
        assert "30s" in captured.err
        assert captured.out == ""
        assert not (tmp_path / ".spec" / "autoplan" / "chain.json").exists()
        events = (tmp_path / ".spec" / "autoplan" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(events) == 1
        event = json.loads(events[0])
        assert event["kind"] == "spawn_refusal"
        assert event["type"] == "AutoplanSpawnError"
        assert "did not answer" in event["message"]

        # the timeout must reach subprocess.run as a keyword, not just exist in source
        seen: dict = {}

        def spy_run(argv, *a, **k):
            if argv[0] == "osascript" and "do script" in argv[2]:
                seen.update(k)
                raise subprocess.TimeoutExpired(argv, 30)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", spy_run)
        main(["spawn", "--root", str(tmp_path), "--next", "framework", "--host", "claude"])
        assert seen.get("timeout") == OSASCRIPT_TIMEOUT_SECONDS

    def test_env_host_selected(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        monkeypatch.setenv("SPEC_AUTORUN_HOST", "pi")
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        code = main(["spawn", "--root", str(tmp_path), "--next", "review", "--dry-run", "--format", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["host"] == "pi"

    def test_unknown_env_host_refuses(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        monkeypatch.setenv("SPEC_AUTORUN_HOST", "zcode")
        code = main(["spawn", "--root", str(tmp_path), "--next", "review", "--dry-run"])
        assert code == 1
        assert "unknown worker host" in capsys.readouterr().err

    def test_explicit_plan_path_escaping_root_refuses(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        outside = tmp_path.parent / "outside-plan.md"
        outside.write_text("- [ ] x\n", encoding="utf-8")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        code = main(["spawn", "--root", str(tmp_path), "--next", "review", "--plan", str(outside), "--dry-run"])
        assert code == 1
        captured = capsys.readouterr()
        # matrix row "plan 路径越根": exit + stderr reason + audited refusal
        assert "escapes the project root" in captured.err
        assert captured.out == ""
        events = (tmp_path / ".spec" / "autoplan" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(events) == 1
        assert json.loads(events[0])["kind"] == "spawn_refusal"

    def test_state_not_json_object_refuses(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        state = tmp_path / ".spec" / "autoplan" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("[1, 2]", encoding="utf-8")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        code = main(["spawn", "--root", str(tmp_path), "--next", "review", "--dry-run"])
        assert code == 1
        assert "not a JSON object" in capsys.readouterr().err

    def test_max_passes_must_be_positive(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        code = main(["spawn", "--root", str(tmp_path), "--next", "review", "--max-passes", "0", "--dry-run"])
        assert code == 1
        assert "--max-passes must be >= 1" in capsys.readouterr().err

    def test_non_integer_pass_refuses_and_records(self, tmp_path, capsys, monkeypatch):
        # matrix row "chain 状态不可读 / 非对象 / round|pass 非整数": F3 owns the
        # non-integer pass refusal (spawns rebuild / recovered_state is F4's)
        seed_master(tmp_path)
        state = tmp_path / ".spec" / "autoplan" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"pass": "next"}), encoding="utf-8")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        code = main(["spawn", "--root", str(tmp_path), "--next", "review", "--dry-run"])
        assert code == 1
        captured = capsys.readouterr()
        assert "non-integer pass index" in captured.err
        assert "'next'" in captured.err
        assert captured.out == ""
        assert not (tmp_path / ".spec" / "autoplan" / "spawns.jsonl").exists()
        events = (tmp_path / ".spec" / "autoplan" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(events) == 1
        event = json.loads(events[0])
        assert event["kind"] == "spawn_refusal"
        assert event["type"] == "AutoplanSpawnError"
        assert "non-integer pass index" in event["message"]

    def test_spawn_without_plan_docs_records_empty_list_not_exit_three(self, tmp_path, capsys, monkeypatch):
        # matrix row "缺主文档 / 缺 --target / 越根 / 不存在" — the autoplan
        # discover 对照: unlike the autorun spawn (exit 3 NoPlanError), the pass
        # chain has no NoPlanError contract — discover_plan_docs returns an
        # empty list, the spawn proceeds, and qualification stays the gate's
        # contract (autoplan_gate.py), not this script's
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        code = main(
            ["spawn", "--root", str(tmp_path), "--next", "review", "--host", "pi", "--dry-run", "--format", "json"]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["plan_docs"] == []
        assert payload["pass"] == 1
        assert not (tmp_path / ".spec" / "autoplan" / "events.jsonl").exists()

    def test_missing_target_refusal_records_event(self, tmp_path, capsys, monkeypatch):
        # matrix row "detail 缺 --target": exit + stderr reason + audited refusal
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        code = main(["spawn", "--root", str(tmp_path), "--next", "detail", "--dry-run"])
        assert code == 1
        captured = capsys.readouterr()
        assert "--next detail requires --target" in captured.err
        assert captured.out == ""
        events = (tmp_path / ".spec" / "autoplan" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(events) == 1
        event = json.loads(events[0])
        assert event["kind"] == "spawn_refusal"
        assert event["type"] == "AutoplanSpawnError"
        assert "--target" in event["message"]

    def test_target_escape_refusal_records_event(self, tmp_path, capsys, monkeypatch):
        # matrix row "target 越根": exit + stderr reason + audited refusal
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        outside = str(tmp_path.parent / "outside-detail.md")
        code = main(["spawn", "--root", str(tmp_path), "--next", "detail", "--target", outside, "--dry-run"])
        assert code == 1
        captured = capsys.readouterr()
        assert "escapes the project root" in captured.err
        assert captured.out == ""
        events = (tmp_path / ".spec" / "autoplan" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(events) == 1
        assert json.loads(events[0])["type"] == "AutoplanSpawnError"

    def test_missing_plan_path_refuses_with_reason_and_event(self, tmp_path, capsys, monkeypatch):
        # matrix row "plan 路径不存在": exit + stderr reason + audited refusal
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        code = main(
            ["spawn", "--root", str(tmp_path), "--next", "review", "--plan", ".spec/plans/absent.md", "--dry-run"]
        )
        assert code == 1
        captured = capsys.readouterr()
        assert "--plan path does not exist" in captured.err
        assert ".spec/plans/absent.md" in captured.err
        assert captured.out == ""
        events = (tmp_path / ".spec" / "autoplan" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(events) == 1
        event = json.loads(events[0])
        assert event["kind"] == "spawn_refusal"
        assert "does not exist" in event["message"]


class TestStatusCommand:
    def test_not_started(self, tmp_path, capsys):
        seed_master(tmp_path)
        code = main(["status", "--root", str(tmp_path)])
        assert code == 0
        assert "pass chain: not started" in capsys.readouterr().out

    def test_with_chain_state(self, tmp_path, capsys):
        seed_master(tmp_path)
        state = tmp_path / ".spec" / "autoplan" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(
            json.dumps(
                {"pass": 3, "kind": "detail", "target": ".spec/plans/02-api.md", "host": "pi", "updated_at": "t"}
            ),
            encoding="utf-8",
        )
        code = main(["status", "--root", str(tmp_path), "--format", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["started"] is True
        assert payload["pass"] == 3
        assert payload["kind"] == "detail"
        assert payload["target"] == ".spec/plans/02-api.md"


class TestAutorunReuseContract:
    def test_both_scripts_reexport_the_same_shared_base_class(self):
        # F5: the shared chain-state module (chain_spawn_support) owns the
        # operational error base; both chain scripts derive their own class
        # from that one base, so a single main() catch handles both chains
        # while each script's audit ``type`` stays its own class name.
        import autoplan_spawn
        import autorun_spawn
        import chain_spawn_support

        assert chain_spawn_support.ChainSpawnError.__module__ == "chain_spawn_support"
        assert autorun_spawn.ChainSpawnError is chain_spawn_support.ChainSpawnError
        assert autoplan_spawn.ChainSpawnError is chain_spawn_support.ChainSpawnError
        assert issubclass(AutorunError, chain_spawn_support.ChainSpawnError)
        assert issubclass(AutoplanSpawnError, chain_spawn_support.ChainSpawnError)
        assert not issubclass(AutoplanSpawnError, AutorunError)


class TestChannelSplit:
    """Matrix row "stdout 可解析" (autoplan side): refusals keep stdout empty
    with the reason on stderr; success payloads stay machine-parseable."""

    def test_matrix_refusals_keep_stdout_empty(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        refusals = [
            (["spawn", "--root", str(tmp_path), "--next", "detail", "--dry-run"], "--next detail requires --target"),
            (
                ["spawn", "--root", str(tmp_path), "--next", "review", "--plan", ".spec/plans/absent.md", "--dry-run"],
                "--plan path does not exist",
            ),
            (
                ["spawn", "--root", str(tmp_path), "--next", "review", "--max-passes", "0", "--dry-run"],
                "--max-passes must be >= 1",
            ),
        ]
        for argv, reason in refusals:
            assert main(argv) == 1, argv
            captured = capsys.readouterr()
            assert captured.out == "", argv
            assert captured.err.startswith("error:"), argv
            assert reason in captured.err, argv

    def test_success_dry_run_stdout_is_parseable_json(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        assert (
            main(
                [
                    "spawn",
                    "--root",
                    str(tmp_path),
                    "--next",
                    "framework",
                    "--host",
                    "pi",
                    "--dry-run",
                    "--format",
                    "json",
                ]
            )
            == 0
        )
        payload = json.loads(capsys.readouterr().out)
        assert payload["dry_run"] is True
        assert payload["kind"] == "framework"


class TestStatusObservability:
    """Lock write/probe, refusal audit, and the resume skeleton (F2)."""

    def test_spawn_writes_holder_content(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        code = main(["spawn", "--root", str(tmp_path), "--next", "review", "--host", "pi", "--dry-run"])
        assert code == 0
        lock_file = tmp_path / ".spec" / "autoplan" / "chain.lock"
        lines = [line for line in lock_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert lines, "a holder line must be written once flock succeeds"
        holder = json.loads(lines[-1])
        assert holder["chain"] == "autoplan"
        assert holder["pid"] == os.getpid()
        assert holder["at"]

    def test_status_surfaces_lock_and_resume_keys(self, tmp_path, capsys):
        seed_master(tmp_path)
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["lock"] == {"status": "free"}
        assert payload["resume"]["action"] == "fresh_chain"
        assert payload["last_refusal"] is None
        assert payload["refusals_recorded"] == 0
        assert main(["status", "--root", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "lock: free" in out
        assert "resume: fresh_chain (" in out

    def test_lock_refusal_records_event(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        lock_file = tmp_path / ".spec" / "autoplan" / "chain.lock"
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_file, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
                code = main(["spawn", "--root", str(tmp_path), "--next", "review", "--dry-run"])
                assert code == 1
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        err = capsys.readouterr().err
        assert err.startswith("error:")
        assert "parallel chains are refused" in err
        events = (tmp_path / ".spec" / "autoplan" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(events) == 1
        event = json.loads(events[0])
        assert event["kind"] == "spawn_refusal"
        assert event["chain"] == "autoplan"
        assert event["type"] == "AutoplanSpawnError"
        # matrix row "锁冲突": the refusal carries the refusing reason, not just the class
        assert "parallel chains are refused" in event["message"]
        assert event["at"]

    def test_lock_conflict_status_surfaces_holder(self, tmp_path, capsys, monkeypatch):
        # matrix row "锁冲突" status 面: while the lock is held, status renders the
        # live holder (pid + command) instead of free
        seed_master(tmp_path)
        lock_file = tmp_path / ".spec" / "autoplan" / "chain.lock"
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        holder_line = (
            json.dumps(
                {
                    "pid": os.getpid(),
                    "command": "python3 scripts/autoplan_spawn.py spawn --root .",
                    "at": "t0",
                    "chain": "autoplan",
                }
            )
            + "\n"
        )
        with open(lock_file, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                handle.seek(0)
                handle.truncate()
                handle.write(holder_line)
                handle.flush()
                assert main(["status", "--root", str(tmp_path)]) == 0
                out = capsys.readouterr().out
                assert f"lock: held by pid {os.getpid()}" in out
                assert "autoplan_spawn.py spawn" in out
                assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
                payload = json.loads(capsys.readouterr().out)
                assert payload["lock"]["status"] == "held"
                assert payload["lock"]["pid"] == os.getpid()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        assert json.loads(capsys.readouterr().out)["lock"] == {"status": "free"}

    def test_guard_refusal_records_event(self, tmp_path, capsys, monkeypatch):
        # framework refuses without the seeded master document — a guard refusal
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        (tmp_path / ".spec").mkdir(parents=True, exist_ok=True)
        code = main(["spawn", "--root", str(tmp_path), "--next", "framework", "--dry-run"])
        assert code == 1
        assert "seeded master document" in capsys.readouterr().err
        events = (tmp_path / ".spec" / "autoplan" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(events) == 1
        event = json.loads(events[0])
        assert event["kind"] == "spawn_refusal"
        assert event["type"] == "AutoplanSpawnError"
        assert "seeded master document" in event["message"]

    def test_status_reports_refusal_without_chain_state(self, tmp_path, capsys):
        seed_master(tmp_path)
        events = tmp_path / ".spec" / "autoplan" / "events.jsonl"
        events.parent.mkdir(parents=True, exist_ok=True)
        events.write_text(
            json.dumps(
                {
                    "kind": "spawn_refusal",
                    "at": "t1",
                    "chain": "autoplan",
                    "type": "AutoplanSpawnError",
                    "message": "seeded master document missing",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        assert main(["status", "--root", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "pass chain: not started (1 refusal recorded: seeded master document missing)" in out
        assert "last_refusal: seeded master document missing" in out


class TestStatusResumeDecision:
    """The F4 resume decision wired into autoplan status: one positive render
    per autoplan-side decision-table class plus the shared words (R5 closure)."""

    SECTIONS = (
        "# detail\n\n## 业务逻辑\n- a\n\n## 数据模型\n- b\n\n"
        "## 数据流与控制流\n- c\n\n## 接口与边界\n- d\n\n## 验收钩子\n- e\n"
    )

    def seed_cluster(self, root: Path, complete_01: bool, complete_02: bool):
        """A master linking two detail docs plus a checkbox to keep the plan unfinished."""
        plans = root / ".spec" / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        (plans / "00-master.md").write_text(
            "# master\n\n索引：[README](README.md) · 细节：[01](01-a.md) · [02](02-b.md)\n\n- [ ] open feature\n",
            encoding="utf-8",
        )
        (plans / "README.md").write_text("# index\n", encoding="utf-8")
        body = self.SECTIONS if complete_01 else "# a\n"
        (plans / "01-a.md").write_text(body, encoding="utf-8")
        if complete_02:
            (plans / "02-b.md").write_text(self.SECTIONS, encoding="utf-8")

    def write_state(self, root: Path, state):
        target = root / ".spec" / "autoplan" / "chain.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        base = {"max_passes": 12, "plan_docs": [".spec/plans/00-master.md"], "updated_at": "t"}
        target.write_text(json.dumps({**base, **state}) + "\n", encoding="utf-8")

    def status_json(self, tmp_path, capsys):
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        return json.loads(capsys.readouterr().out)

    def status_text(self, tmp_path, capsys):
        assert main(["status", "--root", str(tmp_path)]) == 0
        return capsys.readouterr().out

    def test_fresh_chain_render(self, tmp_path, capsys):
        self.seed_cluster(tmp_path, complete_01=False, complete_02=False)
        payload = self.status_json(tmp_path, capsys)
        assert payload["resume"]["action"] == "fresh_chain"
        assert "resume: fresh_chain (" in self.status_text(tmp_path, capsys)

    def test_state_corrupt_render(self, tmp_path, capsys):
        self.seed_cluster(tmp_path, complete_01=False, complete_02=False)
        state = tmp_path / ".spec" / "autoplan" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("{half", encoding="utf-8")
        payload = self.status_json(tmp_path, capsys)
        assert payload["state_status"] == "corrupt"
        assert payload["resume"]["action"] == "state_corrupt"
        assert "pass chain: state unreadable (corrupt; see resume)" in self.status_text(tmp_path, capsys)

    def test_cap_reached_render(self, tmp_path, capsys):
        self.seed_cluster(tmp_path, complete_01=False, complete_02=False)
        self.write_state(tmp_path, {"pass": 12, "kind": "review", "target": None})
        payload = self.status_json(tmp_path, capsys)
        assert payload["resume"]["action"] == "cap_reached"
        assert "resume: cap_reached (" in self.status_text(tmp_path, capsys)

    def test_resume_pass_assignment_render(self, tmp_path, capsys):
        self.seed_cluster(tmp_path, complete_01=False, complete_02=True)
        self.write_state(tmp_path, {"pass": 2, "kind": "detail", "target": ".spec/plans/01-a.md"})
        payload = self.status_json(tmp_path, capsys)
        assert payload["resume"]["action"] == "resume_pass_assignment"
        assert payload["resume"]["detail"]["target"] == ".spec/plans/01-a.md"
        assert "resume: resume_pass_assignment (" in self.status_text(tmp_path, capsys)

    def test_resume_review_render(self, tmp_path, capsys):
        self.seed_cluster(tmp_path, complete_01=True, complete_02=True)
        self.write_state(tmp_path, {"pass": 4, "kind": "review", "target": None})
        payload = self.status_json(tmp_path, capsys)
        assert payload["resume"]["action"] == "resume_review"
        assert "resume: resume_review (" in self.status_text(tmp_path, capsys)

    def test_advance_detail_render(self, tmp_path, capsys):
        self.seed_cluster(tmp_path, complete_01=True, complete_02=False)
        self.write_state(tmp_path, {"pass": 3, "kind": "detail", "target": ".spec/plans/01-a.md"})
        payload = self.status_json(tmp_path, capsys)
        assert payload["resume"]["action"] == "advance_detail"
        assert payload["resume"]["detail"]["target"] == ".spec/plans/02-b.md"
        assert "resume: advance_detail (" in self.status_text(tmp_path, capsys)

    def test_advance_review_render(self, tmp_path, capsys):
        self.seed_cluster(tmp_path, complete_01=True, complete_02=True)
        self.write_state(tmp_path, {"pass": 3, "kind": "detail", "target": ".spec/plans/01-a.md"})
        payload = self.status_json(tmp_path, capsys)
        assert payload["resume"]["action"] == "advance_review"
        assert payload["resume"]["detail"]["detail_docs"] == 2
        assert "resume: advance_review (" in self.status_text(tmp_path, capsys)

    def test_state_diverged_render_audits_once(self, tmp_path, capsys):
        self.seed_cluster(tmp_path, complete_01=True, complete_02=True)
        self.write_state(tmp_path, {"pass": 2, "kind": "detail", "target": ".spec/plans/99-ghost.md"})
        payload = self.status_json(tmp_path, capsys)
        assert payload["resume"]["action"] == "state_diverged"
        assert payload["resume"]["detail"]["target"] == ".spec/plans/99-ghost.md"
        assert "resume: state_diverged (" in self.status_text(tmp_path, capsys)
        # the divergence is audited exactly once until the state changes
        events_path = tmp_path / ".spec" / "autoplan" / "events.jsonl"
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        diverged = [e for e in events if e.get("kind") == "recovery" and e.get("action") == "state_diverged"]
        assert len(diverged) == 1
        assert diverged[0]["chain"] == "autoplan"
        assert diverged[0]["detail"]["target"] == ".spec/plans/99-ghost.md"
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        assert len([e for e in events if e.get("action") == "state_diverged"]) == 1

    def test_recovered_state_render(self, tmp_path, capsys):
        self.seed_cluster(tmp_path, complete_01=False, complete_02=True)
        chain_dir = tmp_path / ".spec" / "autoplan"
        chain_dir.mkdir(parents=True, exist_ok=True)
        (chain_dir / "chain.json").write_text("{half", encoding="utf-8")
        (chain_dir / "spawns.jsonl").write_text(
            json.dumps(
                {
                    "pass": 2,
                    "kind": "detail",
                    "target": ".spec/plans/01-a.md",
                    "max_passes": 12,
                    "plan_docs": [".spec/plans/00-master.md"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        payload = self.status_json(tmp_path, capsys)
        assert payload["state_status"] == "recovered"
        assert payload["resume"]["action"] == "resume_pass_assignment"
        assert payload["resume"]["detail"]["recovered_state"] is True
        assert payload["recovered_from"] == "spawns.jsonl line 1"
        out = self.status_text(tmp_path, capsys)
        assert "state: recovered from spawns.jsonl line 1" in out

    def test_chain_complete_render(self, tmp_path, capsys):
        plans = tmp_path / ".spec" / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        (plans / "00-master.md").write_text("# master\n\n细节：[01](01-a.md)\n\n- [x] done feature\n", encoding="utf-8")
        (plans / "01-a.md").write_text(self.SECTIONS, encoding="utf-8")
        self.write_state(tmp_path, {"pass": 2, "kind": "review", "target": None})
        payload = self.status_json(tmp_path, capsys)
        assert payload["resume"]["action"] == "chain_complete"
        assert "resume: chain_complete (" in self.status_text(tmp_path, capsys)

    def test_resume_json_shape_is_action_reason_detail(self, tmp_path, capsys):
        self.seed_cluster(tmp_path, complete_01=False, complete_02=False)
        payload = self.status_json(tmp_path, capsys)
        assert set(payload["resume"]) == {"action", "reason", "detail"}


class TestStatusDetailDocs:
    """The autoplan detail_docs derived key (F2 task-status-derived; the
    complete flag arrives with F4's five-section heuristic)."""

    def test_from_recorded_plan_docs(self, tmp_path, capsys):
        seed_master(tmp_path)
        (tmp_path / ".spec" / "plans" / "01-auth.md").write_text("# auth\n", encoding="utf-8")
        state = tmp_path / ".spec" / "autoplan" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(
            json.dumps(
                {
                    "pass": 2,
                    "kind": "detail",
                    "target": ".spec/plans/01-auth.md",
                    "plan_docs": [".spec/plans/README.md", ".spec/plans/01-auth.md", ".spec/plans/02-api.md"],
                    "updated_at": "t",
                }
            ),
            encoding="utf-8",
        )
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["detail_docs"] == [
            {"path": ".spec/plans/README.md", "exists": True, "complete": False},
            {"path": ".spec/plans/01-auth.md", "exists": True, "complete": False},
            {"path": ".spec/plans/02-api.md", "exists": False, "complete": False},
        ]
        assert main(["status", "--root", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert (
            "detail docs: .spec/plans/README.md (exists), .spec/plans/01-auth.md (exists),"
            " .spec/plans/02-api.md (missing)" in out
        )

    def test_includes_master_plan_links(self, tmp_path, capsys):
        master = tmp_path / ".spec" / "plans" / "00-master-plan.md"
        master.parent.mkdir(parents=True, exist_ok=True)
        master.write_text(
            "# master\n\n"
            "索引：[README.md](README.md) · 细节：[01-a.md](01-a.md) · [02-b.md](02-b.md)\n"
            "归档：[old](archive/2026-01-01_old.md) · 外链：[x](https://example.com/y.md)\n",
            encoding="utf-8",
        )
        (tmp_path / ".spec" / "plans" / "README.md").write_text("# index\n", encoding="utf-8")
        (tmp_path / ".spec" / "plans" / "01-a.md").write_text("# a\n", encoding="utf-8")
        state = tmp_path / ".spec" / "autoplan" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(
            json.dumps(
                {"pass": 5, "kind": "review", "plan_docs": [".spec/plans/00-master-plan.md"], "updated_at": "t"}
            ),
            encoding="utf-8",
        )
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        # recorded docs first, then the master's sibling detail links; archive
        # and external links stay out
        assert payload["detail_docs"] == [
            {"path": ".spec/plans/00-master-plan.md", "exists": True, "complete": False},
            {"path": ".spec/plans/README.md", "exists": True, "complete": False},
            {"path": ".spec/plans/01-a.md", "exists": True, "complete": False},
            {"path": ".spec/plans/02-b.md", "exists": False, "complete": False},
        ]

    def test_falls_back_to_canonical_scan(self, tmp_path, capsys):
        seed_master(tmp_path)
        (tmp_path / ".spec" / "plans" / "01-auth.md").write_text("# auth\n", encoding="utf-8")
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["detail_docs"] == [
            {"path": ".spec/plans/01-auth.md", "exists": True, "complete": False},
            {"path": ".spec/plans/README.md", "exists": True, "complete": False},
        ]

    def test_empty_without_plans_or_state(self, tmp_path, capsys):
        seed_master(tmp_path)
        (tmp_path / ".spec" / "plans" / "README.md").unlink()
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["detail_docs"] == []
        assert main(["status", "--root", str(tmp_path)]) == 0
        assert "detail docs: none" in capsys.readouterr().out


# --- F13: the pass-chain audit field contract, frozen in tests --------------------
#
# plans/04 §F13: chain reads stay tolerant by design (missing keys get read
# defaults, unknown keys pass through, old files are never rewritten), so the
# contract has no runtime validator — it lives here as frozen sets. Add-only:
# a new key joins alongside the old (F14 will add model / model_injection);
# a renamed or dropped key is a contract break. These tests pin key sets and
# value domains only; window sizes and exact message texts are behavior, not
# contract, and stay out. This chain's own faces: pass/kind/target/max_passes,
# the detail_docs status key, the pass-indexed recycle event, and both recovery
# actions (state_diverged is audited here, not on the autorun chain).

ISO_Z_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

AUDIT_FIELD_CONTRACT = {
    # chain.json after a real pass spawn (updated_at form; window_recycle
    # always recorded, whatever the recycle outcome)
    "chain_state_required": frozenset(
        {
            "pass",
            "kind",
            "target",
            "host",
            "host_source",
            "plan_docs",
            "max_passes",
            "updated_at",
            "shell_command",
            "prev_tty",
            "window_recycle",
            "model",
        }
    ),
    # written only by the corrupt-rebuild path, with a recovery event
    "chain_state_conditional": frozenset({"recovered_from"}),
    # spawns.jsonl row after a real pass spawn (spawned_at form)
    "spawns_row_required": frozenset(
        {
            "pass",
            "kind",
            "target",
            "host",
            "host_source",
            "plan_docs",
            "max_passes",
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
    "plan_kind_domain": frozenset({"framework", "detail", "review"}),
    "window_recycle_status_domain": frozenset({"scheduled", "skipped", "planned"}),
    "window_recycle_keys": {
        "scheduled": frozenset({"status", "window_id", "delay_seconds", "close_wait_seconds"}),
        "skipped": frozenset({"status", "reason"}),
        "planned": frozenset({"status"}),
    },
    "event_kind_domain": frozenset({"spawn_refusal", "recycle_close", "refusal_close", "recovery"}),
    "event_keys": {
        "spawn_refusal": frozenset({"kind", "at", "chain", "type", "message"}),
        "recycle_close": frozenset({"kind", "at", "chain", "pass", "window_id", "prev_tty", "result", "waited_ms"}),
        "refusal_close": frozenset({"kind", "at", "chain", "pass", "window_id", "prev_tty", "result", "waited_ms"}),
        "recovery": frozenset({"kind", "at", "chain", "action", "detail"}),
    },
    "recycle_close_result_domain": frozenset({"closed", "multi-tab", "window-gone", "busy-timeout", "osascript-error"}),
    "recovery_action_domain": frozenset({"recovered_state", "state_diverged"}),
    # both actions are written on this chain: recovered_state by the corrupt
    # rebuild, state_diverged by the status divergence audit
    "recovery_detail_keys": {
        "recovered_state": frozenset({"source", "state_file"}),
        "state_diverged": frozenset({"target", "master_links"}),
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
            "detail_docs",
            "resume",
            "model",
        }
    ),
    # this chain has no conditional derived key (no active-package fanout)
    "status_derived_conditional": frozenset(),
    # the autorun faces never arrive on this chain's status
    "status_foreign_keys": frozenset({"plan", "active_package", "active_packages", "round", "max_rounds"}),
}


# --- F13 legacy-format samples ---------------------------------------------------
#
# Real historical shapes only: committed bytes come from git 4031293 (the F1
# window-recycle commit, which first carried chain runtime state; verified
# byte-exact); older shapes do not survive (the 2241a13-era pass chain never
# committed its state), so those samples are constructed from that era's
# committed writer code. Every sample must read back without a crash and
# without a rewrite — the corrupt-rebuild is the only write a read performs,
# and it leaves a recovery event behind.
#
# Redaction: the private repository absolute path inside committed samples
# is mechanically rewritten to ~/dev/spec (the public-export gate forbids home
# paths in the exported tree; tests/ ships publicly). Equivalence proof:
# fixture bytes == committed bytes after that single-point substitution —
# no other byte differs, and the substitution site count is exactly one.

# git 4031293:.spec/autoplan/chain.json — pass 14 of the 2026-10-07 pass
# chain, kind review: pretty JSON, sorted keys, trailing newline; prev_tty
# null (the spawning session had no controlling Terminal, so the recycle was
# skipped and nothing could be closed).
LEGACY_AUTOPLAN_STATE_BYTES = (
    b'{\n  "host": "pi",\n  "host_source": "session",\n  "kind": "review",\n'
    b'  "max_passes": 14,\n  "pass": 14,\n  "plan_docs": [\n'
    b'    ".spec/plans/00-master-plan.md"\n  ],\n  "prev_tty": null,\n'
    b'  "shell_command": "cd ~/dev/spec && pi --mode text -- \'$spec autoplan continue'
    b" --plan plans/00-master-plan.md --max-passes 14'\",\n"
    b'  "target": null,\n  "updated_at": "2026-10-07T13:06:41Z",\n'
    b'  "window_recycle": {\n    "reason": "spawning session has no controlling Terminal",\n'
    b'    "status": "skipped"\n  }\n}\n'
)

# git 4031293:.spec/autoplan/spawns.jsonl — the committed pass-2 row (the
# kind-detail pass that authored plans/01-chain-failure-recovery.md): sort_keys
# single-line JSONL, one row per pass.
LEGACY_AUTOPLAN_SPAWNS_ROW_BYTES = (
    b'{"host": "pi", "host_source": "flag", "kind": "detail", "max_passes": 12, "pass": 2, '
    b'"plan_docs": ["plans/00-master-plan.md"], "prev_tty": null, '
    b'"shell_command": "cd ~/dev/spec && pi --mode text -- \'$spec autoplan continue '
    b"--plan plans/00-master-plan.md --max-passes 12'\", "
    b'"spawned_at": "2026-10-07T10:02:58Z", "target": "plans/01-chain-failure-recovery.md", '
    b'"window_recycle": {"reason": "spawning session has no controlling Terminal", "status": "skipped"}}\n'
)

# The 2241a13-era writer shape (git 2241a13:scripts/autoplan_spawn.py): the
# pass record predates host_source, and that era's scheduled recycle had no
# bounded close wait (no committed state survives from before F1 — the pass
# chain first committed chain.json at 4031293 — so this sample is constructed
# from that era's writer code).
LEGACY_AUTOPLAN_PRE_F1_STATE = {
    "pass": 2,
    "kind": "detail",
    "target": "plans/01-chain-failure-recovery.md",
    "host": "pi",
    "plan_docs": ["plans/00-master-plan.md"],
    "max_passes": 12,
    "updated_at": "2026-10-07T10:02:58Z",
    "shell_command": (
        "cd ~/dev/spec && pi --mode text -- '$spec autoplan continue --plan plans/00-master-plan.md --max-passes 12'"
    ),
    "prev_tty": None,
    "window_recycle": {"status": "scheduled", "window_id": 44468, "delay_seconds": 3},
}

# The bare-minimum hand-written state: an index and a timestamp.
LEGACY_AUTOPLAN_MINIMAL_STATE = {"pass": 3, "updated_at": "2026-10-06T09:15:00Z"}

# Forward-compat: keys no current writer produces (F14 adds `model` to the
# row; future_field stands in for anything later) pass through reads and
# renders untouched — reads never drop what they do not know.
LEGACY_AUTOPLAN_FORWARD_COMPAT_STATE = dict(
    json.loads(LEGACY_AUTOPLAN_STATE_BYTES.decode("utf-8")),
    model={"id": "gpt-5.2", "reasoning": "high"},
    future_field=1,
)

# The pass chain's recycle-close event shape (no committed events.jsonl
# survives from the 4031293 pass chain — the tail predates the F1 anti-glue
# guard, so the line is unterminated): constructed in the writer's own key
# order, pass-indexed instead of round-indexed.
LEGACY_AUTOPLAN_RECYCLE_CLOSE_BYTES = (
    b'{"kind": "recycle_close", "at": "2026-10-07T13:06:44Z", "chain": "autoplan", '
    b'"pass": 14, "window_id": 44468, "prev_tty": "/dev/ttys013", '
    b'"result": "closed", "waited_ms": 3000}'
)

# The defect residue the F1 anti-glue guard fixed: two records glued onto one
# unterminated tail line form one unparseable line, skipped whole — no
# phantom refusal may be counted out of half a record.
LEGACY_AUTOPLAN_GLUED_EVENTS_BYTES = (
    b'{"kind": "recycle_close", "at": "2026-10-07T13:06:44Z", "chain": "autoplan", '
    b'"pass": 14, "window_id": 44468, "prev_tty": "/dev/ttys013", "result": "closed", "waited_ms": 3000}'
    b'{"kind": "spawn_refusal", "at": "2026-10-07T14:00:00Z", "chain": "autoplan", '
    b'"type": "AutoplanSpawnError", "message": "glued onto the unguarded tail"}'
)

# git 4031293 committed .spec/autoplan/chain.lock as a 0-byte file: the lock
# is a flock mutex; the content is advice for status.
LEGACY_AUTOPLAN_LOCK_EMPTY_BYTES = b""

# A crashed holder's line (write_lock_holder sort_keys form): the pid is
# dead, so the probe degrades to stale-content advice.
LEGACY_AUTOPLAN_STALE_HOLDER_LINE = (
    b'{"at": "2026-10-07T13:00:00Z", "chain": "autoplan", '
    b'"command": "python3 scripts/autoplan_spawn.py spawn --root .", "pid": 999999}\n'
)

AUTOPLAN_TEST_EVENTS_PATH = Path("/tmp/spec-autoplan-test-events.jsonl")


class TestAuditFieldContract:
    """.spec/plans/04 §F13: the frozen pass-chain audit field contract, driven
    through every writer face (payload, state, audit row, events, lock, status
    derivation)."""

    def test_contract_dict_is_internally_consistent(self):
        contract = AUDIT_FIELD_CONTRACT
        # the state and the audit row differ only in the timestamp key
        assert contract["chain_state_required"] - {"updated_at"} == (
            contract["spawns_row_required"] - {"spawned_at", "model_injection"}
        )
        assert "updated_at" in contract["chain_state_required"]
        assert "spawned_at" in contract["spawns_row_required"]
        assert contract["chain_state_conditional"].isdisjoint(contract["chain_state_required"])
        assert "window_recycle" in contract["chain_state_required"]
        # the pass-chain faces are on both state and row
        for key in ("pass", "kind", "target", "max_passes"):
            assert key in contract["chain_state_required"]
            assert key in contract["spawns_row_required"]
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
        # this chain has no conditional derived key (no active-package fanout)
        assert contract["status_derived_conditional"] == frozenset()
        assert contract["status_derived_conditional"].isdisjoint(contract["status_derived_required"])
        # derived status keys never collide with state keys, and the autorun
        # faces never arrive here
        assert contract["status_foreign_keys"].isdisjoint(contract["status_derived_required"])
        assert contract["status_foreign_keys"].isdisjoint(contract["chain_state_required"])
        # round|pass is per-chain: this chain's recycle event carries pass
        assert "pass" in contract["event_keys"]["recycle_close"]
        assert "round" not in contract["event_keys"]["recycle_close"]
        # both recovery actions are pinned, each with its own detail shape
        assert set(contract["recovery_detail_keys"]) == contract["recovery_action_domain"]
        assert contract["recovery_detail_keys"]["recovered_state"] == frozenset({"source", "state_file"})
        assert contract["recovery_detail_keys"]["state_diverged"] == frozenset({"target", "master_links"})
        # the pass kinds are this chain's own frozen domain
        assert contract["plan_kind_domain"] == frozenset({"framework", "detail", "review"})

    def test_dry_run_payload_matches_contract(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--next",
            "framework",
            "--host",
            "pi",
            "--dry-run",
            "--format",
            "json",
        ]
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        contract = AUDIT_FIELD_CONTRACT
        assert set(payload) == contract["spawns_row_required"] | contract["payload_mode_extra"]["dry-run"]
        assert payload["dry_run"] is True
        assert payload["pass"] == 1
        assert isinstance(payload["pass"], int) and not isinstance(payload["pass"], bool)
        assert payload["kind"] in contract["plan_kind_domain"]
        assert payload["kind"] == "framework"
        assert payload["target"] is None
        # the cap is opt-in: the no-flag spawn records null and forwards none
        assert payload["max_passes"] is None
        assert ISO_Z_PATTERN.fullmatch(payload["spawned_at"])
        assert payload["host"] == "pi"
        assert payload["host_source"] in contract["host_source_domain"]
        assert payload["prev_tty"] == "/dev/ttys012"
        assert payload["osascript"][0] == "osascript"
        assert "Terminal" in payload["osascript"][2]
        assert "$spec autoplan continue" in payload["shell_command"]
        assert payload["window_recycle"] == {"status": "planned"}
        assert set(payload["window_recycle"]) == contract["window_recycle_keys"]["planned"]
        # the dry run still takes the lock but never writes chain state
        holder_line = (tmp_path / ".spec" / "autoplan" / "chain.lock").read_text(encoding="utf-8")
        assert set(json.loads(holder_line)) == contract["lock_holder_keys"]
        assert not (tmp_path / ".spec" / "autoplan" / "chain.json").exists()
        assert not (tmp_path / ".spec" / "autoplan" / "spawns.jsonl").exists()

    def test_real_spawn_state_row_and_payload_match_contract(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="4421 /dev/ttys042", stderr=""),
        )
        monkeypatch.setattr("autoplan_spawn.worker_running_on_tty", lambda worker, tty: True)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--next",
            "detail",
            "--target",
            ".spec/plans/01-auth.md",
            "--host",
            "pi",
            "--format",
            "json",
        ]
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        contract = AUDIT_FIELD_CONTRACT
        assert set(payload) == contract["spawns_row_required"] | contract["payload_mode_extra"]["real"]
        assert payload["dry_run"] is False
        assert payload["terminal"] == "4421 /dev/ttys042"
        assert payload["kind"] == "detail"
        assert payload["target"] == ".spec/plans/01-auth.md"
        assert payload["pass"] == 1
        assert ISO_Z_PATTERN.fullmatch(payload["spawned_at"])
        assert payload["host_source"] in contract["host_source_domain"]

        state = json.loads((tmp_path / ".spec" / "autoplan" / "chain.json").read_text(encoding="utf-8"))
        assert set(state) == contract["chain_state_required"]
        assert ISO_Z_PATTERN.fullmatch(state["updated_at"])
        assert state["pass"] == 1
        recycle = state["window_recycle"]
        assert recycle["status"] in contract["window_recycle_status_domain"]
        assert set(recycle) == contract["window_recycle_keys"][recycle["status"]]

        rows = (tmp_path / ".spec" / "autoplan" / "spawns.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(rows) == 1
        row = json.loads(rows[0])
        assert set(row) == contract["spawns_row_required"]
        assert ISO_Z_PATTERN.fullmatch(row["spawned_at"])
        assert row["window_recycle"] == recycle

    def test_spawn_refusal_event_matches_contract(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout="", stderr="not authorized"),
        )
        assert main(["spawn", "--root", str(tmp_path), "--next", "review", "--host", "pi"]) == 1
        capsys.readouterr()
        contract = AUDIT_FIELD_CONTRACT
        events = (tmp_path / ".spec" / "autoplan" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(events) == 1
        event = json.loads(events[0])
        assert set(event) == contract["event_keys"]["spawn_refusal"]
        assert event["kind"] == "spawn_refusal"
        assert event["chain"] == "autoplan"
        assert event["type"] == "AutoplanSpawnError"
        assert ISO_Z_PATTERN.fullmatch(event["at"])
        assert isinstance(event["message"], str)
        # a refused spawn never reaches the state or the audit row
        assert not (tmp_path / ".spec" / "autoplan" / "chain.json").exists()
        assert not (tmp_path / ".spec" / "autoplan" / "spawns.jsonl").exists()

    def test_corrupt_state_rebuild_writes_contract_shaped_state_and_recovery_event(self, tmp_path, capsys):
        chain_dir = tmp_path / ".spec" / "autoplan"
        chain_dir.mkdir(parents=True, exist_ok=True)
        (chain_dir / "chain.json").write_text("{half-written", encoding="utf-8")
        (chain_dir / "spawns.jsonl").write_bytes(LEGACY_AUTOPLAN_SPAWNS_ROW_BYTES)
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        contract = AUDIT_FIELD_CONTRACT
        assert payload["state_status"] == "recovered"
        assert payload["recovered_from"] == "spawns.jsonl line 1"
        state = json.loads((chain_dir / "chain.json").read_text(encoding="utf-8"))
        # the rebuilt state is the audit row (spawned_at intact) plus provenance;
        # the legacy row predates F14's model keys, so the expected set comes
        # from the row itself (the rebuild adds provenance, invents nothing)
        row_keys = set(json.loads(LEGACY_AUTOPLAN_SPAWNS_ROW_BYTES.decode("utf-8")))
        assert set(state) == row_keys | contract["chain_state_conditional"]
        assert state["recovered_from"] == "spawns.jsonl line 1"
        assert state["pass"] == 2
        assert state["kind"] == "detail"
        events = (chain_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(events) == 1
        event = json.loads(events[0])
        assert set(event) == contract["event_keys"]["recovery"]
        assert event["kind"] == "recovery"
        assert event["chain"] == "autoplan"
        assert event["action"] == "recovered_state"
        assert event["action"] in contract["recovery_action_domain"]
        assert ISO_Z_PATTERN.fullmatch(event["at"])
        assert set(event["detail"]) == contract["recovery_detail_keys"]["recovered_state"]
        assert event["detail"] == {"source": "spawns.jsonl line 1", "state_file": "chain.json"}

    def test_state_diverged_audit_writes_contract_shaped_recovery_event(self, tmp_path, capsys):
        plans = tmp_path / ".spec" / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        (plans / "00-master.md").write_text(
            "# master\n\n索引：[README](README.md) · 细节：[01](01-a.md) · [02](02-b.md)\n\n- [ ] open feature\n",
            encoding="utf-8",
        )
        (plans / "README.md").write_text("# index\n", encoding="utf-8")
        sections = (
            "# detail\n\n## 业务逻辑\n- a\n\n## 数据模型\n- b\n\n"
            "## 数据流与控制流\n- c\n\n## 接口与边界\n- d\n\n## 验收钩子\n- e\n"
        )
        (plans / "01-a.md").write_text(sections, encoding="utf-8")
        (plans / "02-b.md").write_text(sections, encoding="utf-8")
        state_file = tmp_path / ".spec" / "autoplan" / "chain.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps(
                {
                    "pass": 2,
                    "kind": "detail",
                    "target": ".spec/plans/99-ghost.md",
                    "max_passes": 12,
                    "plan_docs": [".spec/plans/00-master.md"],
                    "updated_at": "2026-10-07T10:00:00Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["resume"]["action"] == "state_diverged"
        contract = AUDIT_FIELD_CONTRACT
        events = (tmp_path / ".spec" / "autoplan" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        event = json.loads(events[-1])
        assert set(event) == contract["event_keys"]["recovery"]
        assert event["kind"] == "recovery"
        assert event["chain"] == "autoplan"
        assert event["action"] == "state_diverged"
        assert event["action"] in contract["recovery_action_domain"]
        assert set(event["detail"]) == contract["recovery_detail_keys"]["state_diverged"]
        assert event["detail"]["target"] == ".spec/plans/99-ghost.md"
        assert isinstance(event["detail"]["master_links"], list)

    def test_close_argv_produces_contract_recycle_close_lines(self):
        contract = AUDIT_FIELD_CONTRACT
        argv = build_close_argv(
            42,
            3,
            120,
            prev_tty="/dev/ttys012",
            chain="autoplan",
            pass_index=7,
            events_path=AUTOPLAN_TEST_EVENTS_PATH,
        )
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
            assert event["chain"] == "autoplan"
            assert event["pass"] == 7
            assert event["window_id"] == 42
            assert event["prev_tty"] == "/dev/ttys012"
            assert event["waited_ms"] == 3000

    def test_refusal_close_template_renders_contract_lines(self):
        contract = AUDIT_FIELD_CONTRACT
        argv = build_close_argv(
            88,
            0,
            chain_support.REFUSAL_CLOSE_WAIT_SECONDS,
            prev_tty="/dev/ttys055",
            session_pids=[5151],
            worker_name="pi",
            chain="autoplan",
            pass_index=4,
            events_path=AUTOPLAN_TEST_EVENTS_PATH,
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
            assert event["chain"] == "autoplan"
            assert event["pass"] == 4
            assert event["window_id"] == 88
            assert event["prev_tty"] == "/dev/ttys055"
            assert event["result"] == result


class TestRefusalClosesSpawnedWindow:
    """2026-10-09 交接未确认 = 不留进程, autoplan 链: a refused pass spawn
    closes the window it just opened (A2 recovers it behind the identity
    interlock; A5 closes the confirmed window; A6 after the state write
    stays outside the guard — the handoff is confirmed there)."""

    @staticmethod
    def _probe(monkeypatch, open_reply, front_reply, worker_runs, pids):
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
        monkeypatch.setattr("autoplan_spawn.worker_running_on_tty", lambda worker, tty: worker_runs)
        monkeypatch.setattr("autoplan_spawn.worker_pids_on_tty", lambda worker, tty: list(pids))
        return close_argv

    def test_unparseable_reply_closes_the_recovered_window(self, tmp_path, capsys, monkeypatch):
        # A2: the pass open reply carries no id/tty — the front window is
        # recovered (accepted only because it verifiably runs OUR worker)
        # and closed synchronously before the refusal raises.
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        close_argv = self._probe(monkeypatch, "front window opened\n", "88 /dev/ttys055\n", True, [5151])
        code = main(["spawn", "--root", str(tmp_path), "--next", "framework", "--host", "pi", "--format", "json"])
        captured = capsys.readouterr()
        assert code == 1
        assert "cannot confirm the handoff" in captured.err  # refusal text frozen verbatim
        assert captured.out == ""
        assert not (tmp_path / ".spec" / "autoplan" / "chain.json").exists()
        assert not (tmp_path / ".spec" / "autoplan" / "spawns.jsonl").exists()
        assert len(close_argv) == 1
        script = close_argv[0][2]
        assert "sleep 0" in script
        assert '"kind": "refusal_close"' in script
        assert '"pass": 1' in script
        assert '"window_id": 88' in script
        assert '"prev_tty": "/dev/ttys055"' in script
        assert 'case "$cmd" in *"pi "*|*/pi)' in script
        events = (tmp_path / ".spec" / "autoplan" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        event = json.loads(events[-1])
        assert event["kind"] == "spawn_refusal"
        assert "cannot confirm the handoff" in event["message"]

    def test_unparseable_reply_without_recovery_skips_the_close(self, tmp_path, capsys, monkeypatch):
        # A2 fail-open: the front window is not OUR worker — no close target,
        # the refusal proceeds verbatim, nothing is force-closed
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        close_argv = self._probe(monkeypatch, "front window opened\n", "", False, [5151])
        code = main(["spawn", "--root", str(tmp_path), "--next", "framework", "--host", "pi"])
        captured = capsys.readouterr()
        assert code == 1
        assert "cannot confirm the handoff" in captured.err
        assert close_argv == []
        assert not (tmp_path / ".spec" / "autoplan" / "chain.json").exists()

    def test_failure_between_open_and_state_closes_the_window(self, tmp_path, capsys, monkeypatch):
        # A5: the open reply parsed fine but the guarded stretch fails
        # before the pass state is written — the confirmed window closes
        # before the failure propagates
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        close_argv = self._probe(monkeypatch, "42 /dev/ttys009\n", "", True, [991])
        monkeypatch.setattr("autoplan_spawn.read_window_geometry", lambda tty: (10, 20, 30, 40))

        def broken_geometry(window_id, geometry):
            raise AutoplanSpawnError("geometry boom")

        monkeypatch.setattr("autoplan_spawn.apply_window_geometry", broken_geometry)
        code = main(["spawn", "--root", str(tmp_path), "--next", "framework", "--host", "pi", "--format", "json"])
        captured = capsys.readouterr()
        assert code == 1
        assert "geometry boom" in captured.err
        assert len(close_argv) == 1
        script = close_argv[0][2]
        assert '"kind": "refusal_close"' in script
        assert '"window_id": 42' in script
        assert '"prev_tty": "/dev/ttys009"' in script
        assert not (tmp_path / ".spec" / "autoplan" / "chain.json").exists()
        assert not (tmp_path / ".spec" / "autoplan" / "spawns.jsonl").exists()

    def test_state_written_then_audit_failure_keeps_the_window(self, tmp_path, capsys, monkeypatch):
        # A6 (裁定不修): the state write confirms the handoff — the window
        # stays open even when the audit append fails afterwards
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        close_argv = self._probe(monkeypatch, "42 /dev/ttys009\n", "", True, [991])

        def broken_append(path, record):
            raise RuntimeError("audit disk full")

        monkeypatch.setattr("autoplan_spawn.append_audit", broken_append)
        with pytest.raises(RuntimeError, match="audit disk full"):
            main(["spawn", "--root", str(tmp_path), "--next", "framework", "--host", "pi"])
        assert close_argv == []  # confirmed handoff → never closed
        assert (tmp_path / ".spec" / "autoplan" / "chain.json").exists()
        assert not (tmp_path / ".spec" / "autoplan" / "spawns.jsonl").exists()

    def test_window_recycle_shapes_cover_the_status_domain(self, tmp_path, capsys, monkeypatch):
        contract = AUDIT_FIELD_CONTRACT
        observed = set()
        # scheduled: one-tab previous window; pid_lookup/schedule_close are
        # the injectable caller seams (the worker poll moved to the F17
        # verify gate)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="7 1 false\n", stderr=""),
        )
        result = chain_support.recycle_previous_window(
            "/dev/ttys012",
            42,
            "/dev/ttys009",
            "pi",
            pid_lookup=lambda worker, tty: [],
            schedule_close=lambda window_id, delay, wait, **kwargs: None,
            chain="autoplan",
            pass_index=1,
            events_path=AUTOPLAN_TEST_EVENTS_PATH,
        )
        assert set(result) == contract["window_recycle_keys"]["scheduled"]
        assert result["status"] == "scheduled"
        assert isinstance(result["window_id"], int) and not isinstance(result["window_id"], bool)
        assert result["window_id"] > 0
        assert isinstance(result["delay_seconds"], int)
        assert isinstance(result["close_wait_seconds"], int)
        observed.add(result["status"])
        # skipped: no controlling Terminal in the spawning session
        result = chain_support.recycle_previous_window(
            None,
            42,
            "/dev/ttys009",
            "pi",
            chain="autoplan",
            pass_index=1,
            events_path=AUTOPLAN_TEST_EVENTS_PATH,
        )
        assert set(result) == contract["window_recycle_keys"]["skipped"]
        assert result["status"] == "skipped"
        assert isinstance(result["reason"], str)
        observed.add(result["status"])
        # planned: the dry-run face reports what a real spawn would record
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        argv = ["spawn", "--root", str(tmp_path), "--next", "review", "--dry-run", "--format", "json"]
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        assert set(payload["window_recycle"]) == contract["window_recycle_keys"]["planned"]
        assert payload["window_recycle"]["status"] == "planned"
        observed.add(payload["window_recycle"]["status"])
        # the three shapes exhaust the status domain
        assert observed == contract["window_recycle_status_domain"]

    def test_status_derived_key_set_matches_contract(self, tmp_path, capsys):
        chain_dir = tmp_path / ".spec" / "autoplan"
        chain_dir.mkdir(parents=True, exist_ok=True)
        (chain_dir / "chain.json").write_bytes(LEGACY_AUTOPLAN_STATE_BYTES)
        contract = AUDIT_FIELD_CONTRACT
        state_keys = set(json.loads(LEGACY_AUTOPLAN_STATE_BYTES.decode("utf-8")))
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert set(payload) == contract["status_derived_required"] | state_keys
        assert "active_packages" not in payload
        assert set(payload).isdisjoint(contract["status_foreign_keys"])
        assert payload["lock"] == {"status": "free"}
        assert payload["started"] is True
        assert payload["state_status"] == "ok"
        assert isinstance(payload["detail_docs"], list)
        assert isinstance(payload["resume"], dict)

    def test_last_refusal_derives_from_spawn_refusals_only(self, tmp_path, capsys):
        chain_dir = tmp_path / ".spec" / "autoplan"
        chain_dir.mkdir(parents=True, exist_ok=True)
        (chain_dir / "chain.json").write_text(
            json.dumps({"pass": 3, "updated_at": "2026-10-07T12:00:00Z"}) + "\n", encoding="utf-8"
        )
        stream = [
            {
                "kind": "spawn_refusal",
                "at": "2026-10-07T12:00:01Z",
                "chain": "autoplan",
                "type": "AutoplanSpawnError",
                "message": "first refusal",
            },
            json.loads(LEGACY_AUTOPLAN_RECYCLE_CLOSE_BYTES.decode("utf-8")),
            {
                "kind": "recovery",
                "at": "2026-10-07T12:00:03Z",
                "chain": "autoplan",
                "action": "recovered_state",
                "detail": {"source": "spawns.jsonl line 1", "state_file": "chain.json"},
            },
            {
                "kind": "spawn_refusal",
                "at": "2026-10-07T12:00:04Z",
                "chain": "autoplan",
                "type": "AutoplanSpawnError",
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
            "type": "AutoplanSpawnError",
            "message": "second refusal",
        }


class TestModelIdentity:
    """.spec/plans/04 §F14 mirrored on the pass chain: lock, propagation, the four
    conflict refusals, injection levels, and status visibility. No bypass
    exists here, so every spawn resolves a worker host. Flag literals render
    through chain_support.HOST_MODEL_FLAGS, never hardcoded."""

    IDENTITY = {"id": "anthropic/opus-5.5", "reasoning": "high"}

    # sentinel: the pre-F14 chain.json carries no model key at all
    NO_MODEL_KEY = object()

    @staticmethod
    def write_state(root: Path, model, host="pi") -> Path:
        """chain.json with a locked pass-3 review chain; ``model`` dict |
        None (null lock) | NO_MODEL_KEY (pre-F14), ``host`` as recorded."""
        state_file = root / ".spec" / "autoplan" / "chain.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "pass": 3,
            "kind": "review",
            "target": None,
            "host": host,
            "host_source": "flag",
            "plan_docs": [".spec/plans/00-master-plan.md"],
            "max_passes": 12,
            "updated_at": "2026-10-08T00:00:00Z",
            "shell_command": "cd /p && pi --mode text -- '$spec autoplan continue'",
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
        state_file = tmp_path / ".spec" / "autoplan" / "chain.json"
        before = state_file.read_bytes()
        events_file = tmp_path / ".spec" / "autoplan" / "events.jsonl"
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
        assert event["chain"] == "autoplan"
        assert event["type"] == "AutoplanSpawnError"
        assert match in event["message"]
        # refusals write no state
        assert state_file.read_bytes() == before

    # --- lock ------------------------------------------------------------------

    def test_first_spawn_with_flags_locks_chain_state_and_audit_row(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="4421 /dev/ttys042", stderr=""),
        )
        monkeypatch.setattr("autoplan_spawn.worker_running_on_tty", lambda worker, tty: True)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--next",
            "review",
            "--host",
            "pi",
            "--format",
            "json",
            "--model",
            "anthropic/opus-5.5",
            "--reasoning",
            "high",
        ]
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["model"] == self.IDENTITY
        assert payload["model_injection"] == "full"
        state = json.loads((tmp_path / ".spec" / "autoplan" / "chain.json").read_text(encoding="utf-8"))
        assert state["model"] == self.IDENTITY
        assert "model_injection" not in state
        row = json.loads((tmp_path / ".spec" / "autoplan" / "spawns.jsonl").read_text(encoding="utf-8").splitlines()[0])
        assert row["model"] == self.IDENTITY
        assert row["model_injection"] in AUDIT_FIELD_CONTRACT["model_injection_domain"]

    def test_first_spawn_without_flags_locks_null(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="4421 /dev/ttys042", stderr=""),
        )
        monkeypatch.setattr("autoplan_spawn.worker_running_on_tty", lambda worker, tty: True)
        argv = ["spawn", "--root", str(tmp_path), "--next", "review", "--host", "pi", "--format", "json"]
        assert main(argv) == 0
        json.loads(capsys.readouterr().out)
        state = json.loads((tmp_path / ".spec" / "autoplan" / "chain.json").read_text(encoding="utf-8"))
        assert state["model"] is None
        row = json.loads((tmp_path / ".spec" / "autoplan" / "spawns.jsonl").read_text(encoding="utf-8").splitlines()[0])
        assert row["model"] is None
        assert row["model_injection"] == "none"

    # --- propagation ------------------------------------------------------------

    @pytest.mark.parametrize("host", ["pi", "codex", "claude"])
    def test_no_flags_continuation_injects_the_locked_value(self, tmp_path, capsys, monkeypatch, host):
        seed_master(tmp_path)
        self.write_state(tmp_path, self.IDENTITY, host=host)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--next",
            "review",
            "--host",
            host,
            "--format",
            "json",
            "--dry-run",
        ]
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["model"] == self.IDENTITY
        assert payload["model_injection"] == "full"
        shell = payload["shell_command"]
        assert self.model_flag_segment(host, "id", "anthropic/opus-5.5") in shell
        assert self.model_flag_segment(host, "reasoning", "high") in shell

    def test_propagation_survives_a_real_second_pass(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        self.write_state(tmp_path, self.IDENTITY)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="4421 /dev/ttys042", stderr=""),
        )
        monkeypatch.setattr("autoplan_spawn.worker_running_on_tty", lambda worker, tty: True)
        argv = ["spawn", "--root", str(tmp_path), "--next", "review", "--host", "pi", "--format", "json"]
        assert main(argv) == 0
        json.loads(capsys.readouterr().out)
        state = json.loads((tmp_path / ".spec" / "autoplan" / "chain.json").read_text(encoding="utf-8"))
        assert state["pass"] == 4
        assert state["model"] == self.IDENTITY  # the lock carried unchanged
        rows = (tmp_path / ".spec" / "autoplan" / "spawns.jsonl").read_text(encoding="utf-8").splitlines()
        row = json.loads(rows[-1])
        assert row["model"] == self.IDENTITY
        assert row["model_injection"] == "full"
        assert self.model_flag_segment("pi", "id", "anthropic/opus-5.5") in row["shell_command"]

    # --- the four conflict refusals ---------------------------------------------

    def test_conflict_id_refuses_with_both_values(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        self.write_state(tmp_path, self.IDENTITY)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--next",
            "review",
            "--host",
            "pi",
            "--dry-run",
            "--model",
            "openai/gpt-5.2",
        ]
        self.refused(
            tmp_path,
            capsys,
            argv,
            match="locked id 'anthropic/opus-5.5' vs incoming id 'openai/gpt-5.2'",
        )

    def test_conflict_reasoning_refuses_with_both_values(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        self.write_state(tmp_path, self.IDENTITY)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--next",
            "review",
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
        seed_master(tmp_path)
        self.write_state(tmp_path, {"id": "anthropic/opus-5.5"})
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--next",
            "review",
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
        seed_master(tmp_path)
        self.write_state(tmp_path, None)  # model key present, locked null
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--next",
            "review",
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
        seed_master(tmp_path)
        self.write_state(tmp_path, self.IDENTITY)  # chain host recorded as pi
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--next",
            "review",
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
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--next",
            "review",
            "--host",
            "pi",
            "--dry-run",
            "--reasoning",
            "high",
        ]
        code = main(argv)
        captured = capsys.readouterr()
        assert code == 1
        assert "--reasoning requires --model" in captured.err

    def test_empty_model_refuses_through_refusal_path(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        self.write_state(tmp_path, self.IDENTITY)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--next",
            "review",
            "--host",
            "pi",
            "--dry-run",
            "--model",
            "",
        ]
        self.refused(tmp_path, capsys, argv, match="--model must be a non-empty model id")

    def test_whitespace_reasoning_refuses_through_refusal_path(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        self.write_state(tmp_path, self.IDENTITY)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--next",
            "review",
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
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="4421 /dev/ttys042", stderr=""),
        )
        monkeypatch.setattr("autoplan_spawn.worker_running_on_tty", lambda worker, tty: True)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--next",
            "review",
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
        seed_master(tmp_path)
        self.write_state(tmp_path, "not-an-object")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--next",
            "review",
            "--host",
            "pi",
            "--dry-run",
            "--model",
            "anthropic/opus-5.5",
        ]
        self.refused(tmp_path, capsys, argv, match="malformed model identity lock")

    # --- the no-lock no-flags default stays byte-identical ----------------------

    def test_no_lock_no_flags_default_argv_is_unchanged(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        argv = ["spawn", "--root", str(tmp_path), "--next", "review", "--host", "pi", "--format", "json", "--dry-run"]
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["model"] is None
        assert payload["model_injection"] == "none"
        assert payload["shell_command"] == f"cd {tmp_path} && pi --mode text -- '$spec autoplan continue'"

    # --- partial injection ------------------------------------------------------

    def test_partial_when_host_lacks_a_reasoning_flag(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        # construct the host-lacking-reasoning-flag form straight in the table
        monkeypatch.setitem(chain_support.HOST_MODEL_FLAGS["claude"], "reasoning", None)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--next",
            "review",
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
        seed_master(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--next",
            "review",
            "--host",
            "claude",
            "--format",
            "json",
            "--dry-run",
        ]
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["model"] is None
        assert payload["model_injection"] == "none"

    # --- status visibility -------------------------------------------------------

    def test_status_renders_the_locked_model_line(self, tmp_path, capsys):
        seed_master(tmp_path)
        self.write_state(tmp_path, self.IDENTITY)
        spawns_file = tmp_path / ".spec" / "autoplan" / "spawns.jsonl"
        spawns_file.write_text(
            json.dumps(
                {
                    "pass": 3,
                    "kind": "review",
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
        seed_master(tmp_path)
        self.write_state(tmp_path, None)
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["model"] is None
        assert main(["status", "--root", str(tmp_path)]) == 0
        assert "model: none (worker default)" in capsys.readouterr().out

    # --- old-chain compatibility --------------------------------------------------

    def test_legacy_state_without_model_key_first_locks_on_flags(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        state_file = tmp_path / ".spec" / "autoplan" / "chain.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_bytes(LEGACY_AUTOPLAN_STATE_BYTES)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="4421 /dev/ttys042", stderr=""),
        )
        monkeypatch.setattr("autoplan_spawn.worker_running_on_tty", lambda worker, tty: True)
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--next",
            "review",
            "--host",
            "pi",
            "--max-passes",
            "20",
            "--format",
            "json",
            "--model",
            "anthropic/opus-5.5",
            "--reasoning",
            "high",
        ]
        assert main(argv) == 0
        json.loads(capsys.readouterr().out)
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["model"] == self.IDENTITY

    def test_legacy_state_without_model_key_reads_as_unlocked_status(self, tmp_path, capsys):
        seed_master(tmp_path)
        state_file = tmp_path / ".spec" / "autoplan" / "chain.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_bytes(LEGACY_AUTOPLAN_STATE_BYTES)
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["model"] is None  # absent key reads as the worker default


class TestLegacyStateSamples:
    """F13 acceptance: every old-format pass-chain sample (committed bytes or
    a shape constructed from that era's writer code) reads back through
    status without a crash and without a rewrite — the corrupt-rebuild
    (covered above) is the only write a read performs."""

    @staticmethod
    def write_state(root: Path, data) -> Path:
        state_file = root / ".spec" / "autoplan" / "chain.json"
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
        state_file = root / ".spec" / "autoplan" / "chain.json"
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
        self.write_state(tmp_path, LEGACY_AUTOPLAN_STATE_BYTES)
        payload = self.assert_status_preserves_state_bytes(
            tmp_path,
            capsys,
            renders=("pass: 14", "kind: review", "host: pi", "updated_at: 2026-10-07T13:06:41Z"),
        )
        assert payload["pass"] == 14
        assert payload["kind"] == "review"
        assert payload["target"] is None
        assert payload["prev_tty"] is None
        assert payload["window_recycle"] == {
            "status": "skipped",
            "reason": "spawning session has no controlling Terminal",
        }
        # direct read: the parsed state is the file, nothing invented
        state, read_status = chain_support.read_state(tmp_path / ".spec" / "autoplan", "autoplan")
        assert read_status == "ok"
        assert state == json.loads(LEGACY_AUTOPLAN_STATE_BYTES.decode("utf-8"))

    def test_pre_f1_era_state_without_host_source_reads_clean(self, tmp_path, capsys):
        self.write_state(tmp_path, LEGACY_AUTOPLAN_PRE_F1_STATE)
        payload = self.assert_status_preserves_state_bytes(
            tmp_path,
            capsys,
            renders=(
                "pass: 2",
                "kind: detail",
                "target: plans/01-chain-failure-recovery.md",
                "host: pi",
            ),
        )
        # the 2241a13-era shape predates host_source: reads never invent it
        assert "host_source" not in payload
        # the pre-F1 scheduled shape reads as-is: no key invented, none dropped
        assert payload["window_recycle"] == {
            "status": "scheduled",
            "window_id": 44468,
            "delay_seconds": 3,
        }

    def test_minimal_state_gets_read_defaults_in_memory(self, tmp_path, capsys):
        self.write_state(tmp_path, LEGACY_AUTOPLAN_MINIMAL_STATE)
        payload = self.assert_status_preserves_state_bytes(
            tmp_path, capsys, renders=("pass: 3", "host: unknown", "updated_at: 2026-10-06T09:15:00Z")
        )
        # defaults exist only in memory — the file keeps its two keys
        assert payload["host"] == "unknown"
        assert payload["window_recycle"] == {}
        state, read_status = chain_support.read_state(tmp_path / ".spec" / "autoplan", "autoplan")
        assert read_status == "ok"
        assert state == {
            "pass": 3,
            "updated_at": "2026-10-06T09:15:00Z",
            "host": "unknown",
            "window_recycle": {},
        }

    def test_forward_compat_unknown_keys_pass_through(self, tmp_path, capsys):
        self.write_state(tmp_path, LEGACY_AUTOPLAN_FORWARD_COMPAT_STATE)
        payload = self.assert_status_preserves_state_bytes(tmp_path, capsys)
        # unknown keys ride through reads and renders untouched
        assert payload["model"] == {"id": "gpt-5.2", "reasoning": "high"}
        assert payload["future_field"] == 1

    def test_committed_zero_byte_lock_reads_free(self, tmp_path, capsys):
        self.write_state(tmp_path, LEGACY_AUTOPLAN_MINIMAL_STATE)
        lock_file = tmp_path / ".spec" / "autoplan" / "chain.lock"
        lock_file.write_bytes(LEGACY_AUTOPLAN_LOCK_EMPTY_BYTES)
        payload = self.status_payload(tmp_path, capsys)
        assert payload["lock"] == {"status": "free"}
        assert main(["status", "--root", str(tmp_path)]) == 0
        assert "lock: free" in capsys.readouterr().out

    def test_stale_holder_line_reports_stale(self, tmp_path, capsys, monkeypatch):
        self.write_state(tmp_path, LEGACY_AUTOPLAN_MINIMAL_STATE)
        lock_file = tmp_path / ".spec" / "autoplan" / "chain.lock"
        lock_file.write_bytes(LEGACY_AUTOPLAN_STALE_HOLDER_LINE)
        monkeypatch.setattr(chain_support, "pid_alive", lambda pid: False)
        with open(lock_file, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                payload = self.status_payload(tmp_path, capsys)
                assert payload["lock"] == {"status": "held", "holder": "stale", "pid": 999999}
                assert main(["status", "--root", str(tmp_path)]) == 0
                assert "lock: held (stale content)" in capsys.readouterr().out
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def test_events_tail_tolerated_and_spawns_index_reads(self, tmp_path, capsys):
        chain_dir = tmp_path / ".spec" / "autoplan"
        self.write_state(tmp_path, LEGACY_AUTOPLAN_MINIMAL_STATE)
        events_file = chain_dir / "events.jsonl"
        events_file.write_bytes(LEGACY_AUTOPLAN_RECYCLE_CLOSE_BYTES)
        spawns_file = chain_dir / "spawns.jsonl"
        spawns_file.write_bytes(LEGACY_AUTOPLAN_SPAWNS_ROW_BYTES)
        payload = self.status_payload(tmp_path, capsys)
        # a recycle_close tail is not a refusal
        assert payload["refusals_recorded"] == 0
        assert payload["last_refusal"] is None
        assert main(["status", "--root", str(tmp_path)]) == 0
        capsys.readouterr()
        # reads never rewrite the audit files either
        assert events_file.read_bytes() == LEGACY_AUTOPLAN_RECYCLE_CLOSE_BYTES
        assert spawns_file.read_bytes() == LEGACY_AUTOPLAN_SPAWNS_ROW_BYTES
        # the committed row parses as a contract row; it is the pass index.
        # Add-only: the legacy row predates F14's model keys, so it carries a
        # subset of the frozen required set (the F14 writer's exact set is
        # pinned by the real-spawn contract test)
        record, line_number = chain_support.last_audit_record(spawns_file)
        assert line_number == 1
        assert set(record) <= AUDIT_FIELD_CONTRACT["spawns_row_required"]
        assert record["pass"] == 2

    def test_glued_events_pair_counts_no_refusals(self, tmp_path, capsys):
        self.write_state(tmp_path, LEGACY_AUTOPLAN_MINIMAL_STATE)
        events_file = tmp_path / ".spec" / "autoplan" / "events.jsonl"
        events_file.write_bytes(LEGACY_AUTOPLAN_GLUED_EVENTS_BYTES)
        assert main(["status", "--root", str(tmp_path), "--format", "json"]) == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["refusals_recorded"] == 0
        assert payload["last_refusal"] is None
        assert "skipping unparseable events line" in captured.err
        assert events_file.read_bytes() == LEGACY_AUTOPLAN_GLUED_EVENTS_BYTES

    def test_corrupt_state_without_audit_row_is_never_rewritten(self, tmp_path, capsys):
        chain_dir = tmp_path / ".spec" / "autoplan"
        chain_dir.mkdir(parents=True, exist_ok=True)
        corrupt_bytes = b"{half-written"
        (chain_dir / "chain.json").write_bytes(corrupt_bytes)
        payload = self.status_payload(tmp_path, capsys)
        assert payload["state_status"] == "corrupt"
        assert payload["started"] is False
        assert payload["resume"]["action"] == "state_corrupt"
        assert main(["status", "--root", str(tmp_path)]) == 0
        assert "pass chain: state unreadable (corrupt; see resume)" in capsys.readouterr().out
        # no audit row to rebuild from: the bytes stay exactly as found
        assert (chain_dir / "chain.json").read_bytes() == corrupt_bytes
        assert not (chain_dir / "events.jsonl").exists()
        assert not (chain_dir / "spawns.jsonl").exists()


class TestOptInPassCap:
    """The pass cap is opt-in (the F17 adaptive default was removed): without
    an explicit flag the chain is unbounded — max_passes records as null and
    the continuation prompt carries no cap flag."""

    @staticmethod
    def seed_master_with_details(root, detail_count):
        master = seed_master(root)
        links = "\n".join(f"- [phase {i + 1}]({i + 1:02d}-phase.md)" for i in range(detail_count))
        master.write_text(
            "# plan\n\n## confirmed facts\n- fact A\n\n## phases\n\n{links}\n\n- [ ] open feature 1\n".format(
                links=links
            ),
            encoding="utf-8",
        )
        return master

    def test_no_flag_means_unbounded(self, tmp_path, capsys, monkeypatch):
        self.seed_master_with_details(tmp_path, 4)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        argv = ["spawn", "--root", str(tmp_path), "--next", "review", "--host", "pi", "--format", "json", "--dry-run"]
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["max_passes"] is None
        assert "--max-passes" not in payload["shell_command"]
        assert "--max-passes" not in payload["osascript"][2]

    def test_explicit_flag_wins_exactly(self, tmp_path, capsys, monkeypatch):
        self.seed_master_with_details(tmp_path, 4)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        argv = [
            "spawn",
            "--root",
            str(tmp_path),
            "--next",
            "review",
            "--host",
            "pi",
            "--format",
            "json",
            "--dry-run",
            "--max-passes",
            "6",
        ]
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["max_passes"] == 6
        assert "--max-passes 6" in payload["shell_command"]

    def test_recorded_cap_is_not_sticky(self, tmp_path, capsys, monkeypatch):
        # a cap recorded by an earlier spawn no longer binds later spawns:
        # the flag alone decides, so a no-flag continuation goes unbounded
        seed_master(tmp_path)
        state_file = tmp_path / ".spec" / "autoplan" / "chain.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"pass": 3, "max_passes": 30}) + "\n", encoding="utf-8")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autoplan_spawn.session_tty", lambda: "/dev/ttys012")
        argv = ["spawn", "--root", str(tmp_path), "--next", "review", "--host", "pi", "--format", "json", "--dry-run"]
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["max_passes"] is None
        assert "--max-passes" not in payload["shell_command"]
