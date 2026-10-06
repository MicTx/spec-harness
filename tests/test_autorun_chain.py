"""Tests for scripts/autorun_spawn.py (the /spec:autorun chain facts and spawn)."""

import fcntl
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from autorun_spawn import (  # noqa: E402  # type: ignore
    AutorunError,
    NoPlanError,
    build_prompt,
    build_terminal_command,
    build_worker_command,
    count_features,
    discover_plan_docs,
    main,
    plan_payload,
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

    def test_worker_commands(self, tmp_path):
        codex = build_worker_command("codex", tmp_path, "PROMPT")
        assert codex[0] == "codex" and codex[1] == "exec"
        assert "--cd" in codex and str(tmp_path) in codex
        assert codex[-1] == "PROMPT"
        pi = build_worker_command("pi", tmp_path, "PROMPT")
        assert pi[:4] == ["pi", "-p", "--no-session", "--mode"]
        claude = build_worker_command("claude", tmp_path, "PROMPT")
        assert claude[0] == "claude" and claude[-1] == "PROMPT"

    def test_terminal_command_quotes_paths_and_prompts(self, tmp_path):
        spacey = tmp_path / "pro ject"
        spacey.mkdir(exist_ok=True)
        argv, shell = build_terminal_command(spacey, ["echo", "a b", 'q"uote'])
        assert argv[0] == "osascript"
        assert argv[2].startswith('tell application "Terminal" to do script "')
        assert argv[2].endswith('"')
        assert shell.startswith("cd ")
        assert "pro ject" in shell
        # Every double quote the shell command carries must arrive escaped in
        # the AppleScript literal, so osascript never sees a broken string.
        body = argv[2][len('tell application "Terminal" to do script "') : -1]
        assert body.replace('\\"', '"') == shell


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
