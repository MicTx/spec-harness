"""Tests for scripts/autorun_spawn.py (the /spec:autorun chain facts and spawn)."""

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import autorun_spawn  # noqa: E402  # type: ignore
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
    recycle_previous_window,
    resolve_worker_host,
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
    def test_conventional_candidates(self, tmp_path):
        write_plan(tmp_path / ".spec" / "plan.md", unchecked=2)
        write_plan(tmp_path / "docs" / "plans" / "b.md", checked=1)
        (tmp_path / "docs" / "plans" / "a-empty.md").write_text("# no boxes\n", encoding="utf-8")
        (tmp_path / "ROADMAP.md").write_text("- [x] legacy\n", encoding="utf-8")
        docs, scanned = discover_plan_docs(tmp_path, None)
        assert {doc.name for doc in docs} == {"plan.md", "b.md", "ROADMAP.md"}
        assert any("a-empty.md" in item for item in scanned)
        assert all(doc.name != "a-empty.md" for doc in docs)

    def test_prd_and_design_candidates(self, tmp_path):
        write_plan(tmp_path / "PRD.md", unchecked=1)
        write_plan(tmp_path / "docs" / "prd.md", checked=1)
        write_plan(tmp_path / "docs" / "design" / "schema.md", unchecked=1)
        (tmp_path / "docs" / "design" / "notes.md").write_text("# no boxes\n", encoding="utf-8")
        docs, _ = discover_plan_docs(tmp_path, None)
        assert {str(doc.relative_to(tmp_path)) for doc in docs} == {
            "PRD.md",
            "docs/prd.md",
            "docs/design/schema.md",
        }

    def test_explicit_paths_resolve_under_root(self, tmp_path):
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
        (tmp_path / "PLAN.md").write_text("# just prose\n", encoding="utf-8")
        assert main(["plan", "--root", str(tmp_path)]) == 3

    def test_json_payload_aggregates_docs(self, tmp_path, capsys):
        write_plan(tmp_path / ".spec" / "plan.md", unchecked=2, checked=1)
        write_plan(tmp_path / "docs" / "plan.md", unchecked=0, checked=2)
        assert main(["plan", "--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["totals"] == {"checked": 3, "unchecked": 2, "total": 5}
        assert payload["complete"] is False
        assert [doc["path"] for doc in payload["docs"]] == [".spec/plan.md", "docs/plan.md"]

    def test_complete_when_all_checked(self, tmp_path):
        write_plan(tmp_path / "PLAN.md", unchecked=0, checked=3)
        payload = plan_payload(tmp_path, None)
        assert payload["complete"] is True


class TestSpawnCommand:
    def test_refuses_without_active_package(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / "PLAN.md", unchecked=1)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        code = main(["spawn", "--root", str(tmp_path), "--dry-run"])
        assert code == 1
        assert "no active task package" in capsys.readouterr().err

    def test_refuses_when_plan_complete(self, tmp_path, capsys):
        write_plan(tmp_path / "PLAN.md", unchecked=0, checked=1)
        make_active_package(tmp_path)
        code = main(["spawn", "--root", str(tmp_path), "--dry-run"])
        assert code == 1
        assert "complete" in capsys.readouterr().err

    def test_dry_run_prints_osascript_without_side_effects(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / "PLAN.md", unchecked=2)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
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
        write_plan(tmp_path / "PLAN.md", unchecked=2)
        make_active_package(tmp_path)
        state = tmp_path / ".spec" / "autorun" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"round": 4, "host": "pi"}), encoding="utf-8")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        code = main(["spawn", "--root", str(tmp_path), "--host", "pi", "--dry-run", "--format", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["round"] == 5

    def test_refuses_at_round_cap(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / "PLAN.md", unchecked=2)
        make_active_package(tmp_path)
        state = tmp_path / ".spec" / "autorun" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"round": 20}), encoding="utf-8")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        code = main(["spawn", "--root", str(tmp_path), "--dry-run"])
        assert code == 1
        assert "round cap" in capsys.readouterr().err

    def test_refuses_when_lock_held(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / "PLAN.md", unchecked=1)
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
        write_plan(tmp_path / "PLAN.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="tab 1 of window 1", stderr=""),
        )
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
        write_plan(tmp_path / "PLAN.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout="", stderr="not authorized"),
        )
        code = main(["spawn", "--root", str(tmp_path)])
        assert code == 1
        assert "osascript" in capsys.readouterr().err
        assert not (tmp_path / ".spec" / "autorun" / "chain.json").exists()

    def test_unknown_env_host_refuses(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / "PLAN.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setenv("SPEC_AUTORUN_HOST", "zcode")
        code = main(["spawn", "--root", str(tmp_path), "--dry-run"])
        assert code == 1
        assert "unknown worker host" in capsys.readouterr().err

    def test_env_host_selected(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / "PLAN.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setenv("SPEC_AUTORUN_HOST", "pi")
        code = main(["spawn", "--root", str(tmp_path), "--dry-run", "--format", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["host"] == "pi"
        assert payload["host_source"] == "env"

    def test_session_detection_selects_running_host(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / "PLAN.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.delenv("SPEC_AUTORUN_HOST", raising=False)
        monkeypatch.setenv("PI_CODING_AGENT", "true")
        monkeypatch.setattr(
            "shutil.which",
            lambda name: {"codex": "/codex", "pi": "/pi"}.get(name),
        )
        code = main(["spawn", "--root", str(tmp_path), "--dry-run", "--format", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["host"] == "pi"
        assert payload["host_source"] == "session"

    def test_command_override_skips_host_resolution(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / "PLAN.md", unchecked=1)
        make_active_package(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: None)
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
        write_plan(tmp_path / "PLAN.md", unchecked=1)
        code = main(["status", "--root", str(tmp_path), "--format", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["started"] is False

    def test_with_chain_state(self, tmp_path, capsys):
        write_plan(tmp_path / "PLAN.md", unchecked=1)
        state = tmp_path / ".spec" / "autorun" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"round": 2, "host": "codex", "updated_at": "t"}), encoding="utf-8")
        code = main(["status", "--root", str(tmp_path), "--format", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["started"] is True
        assert payload["round"] == 2


class TestCommandBuilders:
    def test_prompt_shapes_per_host(self):
        assert build_prompt("claude", ["--plan a.md"], 5) == "/spec:autorun --plan a.md --max-rounds 5"
        assert build_prompt("codex", [], 5) == "$spec autorun --max-rounds 5"
        assert build_prompt("pi", ["--plan a.md,b.md"], 5) == "$spec autorun --plan a.md,b.md --max-rounds 5"

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

    def test_worker_running_on_tty_survives_ps_failure(self, monkeypatch):
        def fake_run(*a, **k):
            raise subprocess.TimeoutExpired(a[0], 5)

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert worker_running_on_tty("codex", "/dev/ttys009") is False

    def test_close_argv_shape_and_validation(self):
        argv = build_close_argv(42, 3)
        assert argv[:2] == ["/bin/sh", "-c"]
        assert argv[2].startswith("sleep 3;")
        assert "close window id 42" in argv[2]
        # the close helper re-checks both guards before touching the window:
        # tab count (Terminal cannot close one tab) and a still-running session
        assert "count of tabs of window id 42" in argv[2]
        assert "busy of tab 1 of window id 42" in argv[2]
        with pytest.raises(AutorunError):
            build_close_argv(-1, 3)
        with pytest.raises(AutorunError):
            build_close_argv("42", 3)

    def test_recycle_schedules_close(self, monkeypatch):
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: True)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="7 1 false\n", stderr=""),
        )
        scheduled: list = []
        monkeypatch.setattr("autorun_spawn.schedule_window_close", lambda wid, delay: scheduled.append((wid, delay)))
        result = recycle_previous_window("/dev/ttys012", 42, "/dev/ttys009", "claude")
        assert result == {"status": "scheduled", "window_id": 7, "delay_seconds": 3}
        assert scheduled == [(7, 3)]

    def test_recycle_skips_busy_window(self, monkeypatch):
        # a tab whose process still runs cannot be closed without Terminal's
        # cancel/terminate sheet: fail open with a recorded reason instead
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: True)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="7 1 true\n", stderr=""),
        )
        scheduled: list = []
        monkeypatch.setattr("autorun_spawn.schedule_window_close", lambda wid, delay: scheduled.append(wid))
        result = recycle_previous_window("/dev/ttys012", 42, "/dev/ttys009", "claude")
        assert result["status"] == "skipped"
        assert "still running" in result["reason"]
        assert scheduled == []

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
        monkeypatch.setattr("autorun_spawn.schedule_window_close", lambda wid, delay: scheduled.append(wid))
        result = recycle_previous_window("/dev/ttys012", 42, "/dev/ttys009", "claude")
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
        monkeypatch.setattr("autorun_spawn.schedule_window_close", lambda wid, delay: scheduled.append(wid))
        result = recycle_previous_window("/dev/ttys012", 42, "/dev/ttys009", "claude")
        assert result["status"] == "skipped"
        assert "did not answer" in result["reason"]
        assert scheduled == []

    def test_recycle_skips_when_worker_not_observed(self, monkeypatch):
        monkeypatch.setattr("autorun_spawn.WORKER_START_TIMEOUT_SECONDS", 0)
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: False)
        result = recycle_previous_window("/dev/ttys012", 42, "/dev/ttys009", "claude")
        assert result["status"] == "skipped"
        assert "not observed" in result["reason"]

    def test_recycle_skips_without_prev_tty(self):
        result = recycle_previous_window(None, 42, "/dev/ttys009", "claude")
        assert result["status"] == "skipped"
        assert "no controlling Terminal" in result["reason"]

    def test_recycle_skips_without_spawn_ids(self):
        result = recycle_previous_window("/dev/ttys012", None, None, "claude")
        assert result["status"] == "skipped"
        assert "unavailable" in result["reason"]

    def test_recycle_skips_when_prev_window_missing(self, monkeypatch):
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: True)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="\n", stderr=""),
        )
        result = recycle_previous_window("/dev/ttys012", 42, "/dev/ttys009", "claude")
        assert result["status"] == "skipped"
        assert "no Terminal window hosts" in result["reason"]

    def test_recycle_skips_same_window(self, monkeypatch):
        monkeypatch.setattr("autorun_spawn.worker_running_on_tty", lambda worker, tty: True)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="42 1 false\n", stderr=""),
        )
        result = recycle_previous_window("/dev/ttys012", 42, "/dev/ttys009", "claude")
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
        monkeypatch.setattr("autorun_spawn.schedule_window_close", lambda wid, delay: scheduled.append(wid))
        result = recycle_previous_window("/dev/ttys012", 42, "/dev/ttys009", "")
        assert result["status"] == "scheduled"
        assert scheduled == [7]
        assert all(argv[0] != "ps" for argv in calls)

    def test_spawn_records_recycle_state(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / "PLAN.md", unchecked=1)
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
        monkeypatch.setattr("autorun_spawn.schedule_window_close", lambda wid, delay: scheduled.append((wid, delay)))
        code = main(["spawn", "--root", str(tmp_path), "--host", "claude", "--format", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["window_recycle"] == {"status": "scheduled", "window_id": 7, "delay_seconds": 3}
        assert scheduled == [(7, 3)]
        state = json.loads((tmp_path / ".spec" / "autorun" / "chain.json").read_text(encoding="utf-8"))
        assert state["window_recycle"]["window_id"] == 7
        assert state["prev_tty"] == "/dev/ttys012"

    def test_spawn_osascript_timeout_refuses_without_recording(self, tmp_path, capsys, monkeypatch):
        write_plan(tmp_path / "PLAN.md", unchecked=1)
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


class TestChannelSplit:
    def test_errors_on_stderr_stdout_parseable(self, tmp_path, capsys):
        code = main(["plan", "--root", str(tmp_path)])
        captured = capsys.readouterr()
        assert code == 3
        assert captured.err.startswith("error:")
        assert captured.out == ""

    def test_missing_root_refuses(self, tmp_path, capsys):
        code = main(["plan", "--root", str(tmp_path / "nope")])
        assert code == 1
        assert "not a directory" in capsys.readouterr().err


class TestNoPlanErrorContract:
    def test_no_plan_error_is_exit_three(self):
        assert isinstance(NoPlanError("x"), AutorunError)
