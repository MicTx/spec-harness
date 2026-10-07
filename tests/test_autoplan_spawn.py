"""Tests for scripts/autoplan_spawn.py (the /spec:autoplan serial pass-chain spawn)."""

import fcntl
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from autoplan_spawn import (  # noqa: E402  # type: ignore
    AutoplanSpawnError,
    build_pass_prompt,
    main,
    master_doc_path,
    resolve_target,
)
from autorun_spawn import AutorunError, plan_args_for_prompt  # noqa: E402  # type: ignore


def seed_master(root: Path, path=".spec/plan.md"):
    doc = root / path
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("# plan\n\n## confirmed facts\n- fact A\n\n- [ ] open feature 1\n", encoding="utf-8")
    return doc


class TestMasterDocPath:
    def test_default_master_doc(self, tmp_path):
        assert master_doc_path(tmp_path, None) == (tmp_path / ".spec" / "plan.md").resolve()

    def test_first_explicit_plan_entry_wins(self, tmp_path):
        resolved = master_doc_path(tmp_path, "PLAN.md,docs/plans/01-a.md")
        assert resolved == (tmp_path / "PLAN.md").resolve()


class TestResolveTarget:
    def test_detail_requires_target(self, tmp_path):
        with pytest.raises(AutoplanSpawnError, match="--next detail requires --target"):
            resolve_target(tmp_path, None, "detail")

    def test_target_may_be_new_file_under_root(self, tmp_path):
        assert resolve_target(tmp_path, "docs/plans/01-auth.md", "detail") == "docs/plans/01-auth.md"

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
        code = main(
            [
                "spawn",
                "--root",
                str(tmp_path),
                "--next",
                "detail",
                "--target",
                "docs/plans/01-auth.md",
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
        assert payload["target"] == "docs/plans/01-auth.md"
        assert not (tmp_path / "docs" / "plans" / "01-auth.md").exists()

    def test_pass_increments_from_chain_state(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        state = tmp_path / ".spec" / "autoplan" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"pass": 4, "kind": "detail", "host": "pi"}), encoding="utf-8")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        code = main(
            ["spawn", "--root", str(tmp_path), "--next", "review", "--host", "pi", "--dry-run", "--format", "json"]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["pass"] == 5

    def test_refuses_at_pass_cap(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        state = tmp_path / ".spec" / "autoplan" / "chain.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"pass": 12}), encoding="utf-8")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        code = main(["spawn", "--root", str(tmp_path), "--next", "review", "--dry-run"])
        assert code == 1
        assert "pass cap reached" in capsys.readouterr().err

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
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="tab 1 of window 1", stderr=""),
        )
        code = main(
            [
                "spawn",
                "--root",
                str(tmp_path),
                "--next",
                "detail",
                "--target",
                "docs/plans/01-auth.md",
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
        assert state["target"] == "docs/plans/01-auth.md"
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
        assert "osascript" in capsys.readouterr().err
        assert not (tmp_path / ".spec" / "autoplan" / "chain.json").exists()

    def test_env_host_selected(self, tmp_path, capsys, monkeypatch):
        seed_master(tmp_path)
        monkeypatch.setenv("SPEC_AUTORUN_HOST", "pi")
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
        assert "escapes the project root" in capsys.readouterr().err

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
                {"pass": 3, "kind": "detail", "target": "docs/plans/02-api.md", "host": "pi", "updated_at": "t"}
            ),
            encoding="utf-8",
        )
        code = main(["status", "--root", str(tmp_path), "--format", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["started"] is True
        assert payload["pass"] == 3
        assert payload["kind"] == "detail"
        assert payload["target"] == "docs/plans/02-api.md"


class TestAutorunReuseContract:
    def test_refusal_type_is_autorun_error_subclass(self):
        # The pass chain shares the autorun refusal exception class so the
        # single main() catch handles both scripts' operational failures.
        assert issubclass(AutoplanSpawnError, AutorunError)
