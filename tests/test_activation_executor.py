"""Activation host-executor contract tests (task-host-executor): stub units, zero model spend.

Proves the WIRING, never model behavior:
- stream parsing maps the measured turn onto skill_invoked / package_recorded /
  first implementation tool in order, with the setup turn sliced away;
- package state comes from disk before/after (create / resume / duplicate);
- the two arms install differently and the row records it, including the
  one-time activation opt-in and the identical model-alias rewrite both arms get;
- scenario seeds are honest: acceptance fails on the raw seed and passes on a
  golden solution (the gap is real and satisfiable);
- timeout / host-error rows are honest failures, never silent passes;
- the CLI refuses without an authorization label or a claude binary (zero calls,
  proven by a marker the stub writes only when actually invoked);
- --resume skips units already present and never duplicates them.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(ROOT / "bench" / "activation"))

import activation as act  # noqa: E402
import host_executor as hx  # noqa: E402

STUB_TEMPLATE = ROOT / "tests" / "fixtures" / "activation_stub_claude.py"
SCENARIOS = ROOT / "bench" / "activation" / "scenarios"

P1_SOLUTION = {
    "app/users.py": '''"""User directory: listing and lookup."""


def list_users():
    return [
        {"name": "Alice", "role": "admin", "email": "alice@example.com"},
        {"name": "Bob", "role": "member", "email": "bob@example.com"},
        {"name": "Linda", "role": "member", "email": "linda@example.com"},
    ]


def find_by_email(users, email):
    return next((u for u in users if u["email"] == email), None)


def filter_users(users, query):
    q = query.lower()
    return [u for u in users if q in u["name"].lower()]
''',
    "app/settings.py": '''"""Settings storage for the admin console."""


_DEFAULTS = {"theme": "light", "page_size": 25}

_state = dict(_DEFAULTS)


def get_setting(key):
    return _state.get(key)


def set_setting(key, value):
    _state[key] = value


def set_toggle(key, enabled):
    _state["toggle:" + key] = bool(enabled)


def get_toggle(key):
    return bool(_state.get("toggle:" + key, False))
''',
    "app/export.py": '''"""Export helpers for the admin console."""

import json


def to_json(rows):
    return json.dumps(rows, ensure_ascii=False)


def to_csv(rows):
    keys = list(rows[0].keys())
    lines = [",".join(keys)]
    for row in rows:
        lines.append(",".join(str(row[k]) for k in keys))
    return "\\n".join(lines) + "\\n"
''',
    "tests/test_new_features.py": """from app import export, settings, users


def test_filter():
    people = users.list_users()
    assert [u["name"] for u in users.filter_users(people, "li")] == ["Alice", "Linda"]


def test_toggle():
    settings.set_toggle("csv_export", True)
    assert settings.get_toggle("csv_export") is True


def test_csv():
    assert export.to_csv([{"a": 1}]).startswith("a")
""",
}

P3_SOLUTION = {
    "app/settings.py": P1_SOLUTION["app/settings.py"],
    "app/export.py": P1_SOLUTION["app/export.py"],
    "tests/test_resumed_work.py": """from app import export, settings


def test_toggle_read():
    settings.set_toggle("csv_export", True)
    assert settings.get_toggle("csv_export") is True


def test_csv():
    assert export.to_csv([{"a": 1}]).startswith("a")
""",
}


def write_stub(tmp_path: Path, plan: dict) -> Path:
    stub = tmp_path / "claude-stub"
    shutil.copy2(STUB_TEMPLATE, stub)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    plan_path = tmp_path / "stub-plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    stub_env_note = str(plan_path)
    assert stub_env_note  # plan path is threaded via ACT_STUB_PLAN below
    return stub


def stub_env(plan_path: Path, tmp_path: Path, real_settings: Path, **extra: str) -> dict:
    env = dict(os.environ)
    env["ACT_STUB_PLAN"] = str(plan_path)
    env["BENCH_CLAUDE_SETTINGS_SOURCE"] = str(real_settings)
    env.update(extra)
    return env


def fake_real_settings(tmp_path: Path, with_1m: bool = True) -> Path:
    settings = tmp_path / "real-settings.json"
    suffix = "[1M]" if with_1m else ""
    settings.write_text(
        json.dumps(
            {
                "env": {
                    "ANTHROPIC_AUTH_TOKEN": "stub-token",
                    "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
                    "ANTHROPIC_DEFAULT_SONNET_MODEL": f"claude-sonnet-5{suffix}",
                    "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "glm-5.3",
                    "ANTHROPIC_DEFAULT_OPUS_MODEL": f"claude-opus-5{suffix}",
                    "CLAUDE_CODE_SUBAGENT_MODEL": "glm-5.3",
                },
                "effortLevel": "high",
            }
        ),
        encoding="utf-8",
    )
    return settings


# --- scenario seeds are honest --------------------------------------------------------


@pytest.mark.parametrize(
    "scenario,solution,readme_fix",
    [
        ("P1", P1_SOLUTION, False),
        ("P2", P1_SOLUTION, False),
        ("P3", P3_SOLUTION, False),
    ],
)
def test_positive_acceptance_fails_on_seed_and_passes_on_solution(tmp_path, scenario, solution, readme_fix):
    repo = hx.seed_repo(SCENARIOS / scenario, tmp_path / "seedcheck")
    ok_seed, _ = hx.run_acceptance(SCENARIOS / scenario, repo)
    assert ok_seed is False, "acceptance must prove a real gap on the raw seed"
    for path, content in solution.items():
        (repo / path).write_text(content, encoding="utf-8")
    if readme_fix:
        (repo / "README.md").write_text("# User Administration\n", encoding="utf-8")
    ok_fixed, tail = hx.run_acceptance(SCENARIOS / scenario, repo)
    assert ok_fixed is True, tail


@pytest.mark.parametrize("scenario", ["N1", "N5"])
def test_typo_acceptance_fails_on_seed_and_passes_on_fix(tmp_path, scenario):
    repo = hx.seed_repo(SCENARIOS / scenario, tmp_path / f"seedcheck-{scenario}")
    ok_seed, _ = hx.run_acceptance(SCENARIOS / scenario, repo)
    assert ok_seed is False
    fixed = "# User Administration\n\nDemo notes for the admin console. See app/ for code.\n"
    (repo / "README.md").write_text(fixed, encoding="utf-8")
    ok_fixed, tail = hx.run_acceptance(SCENARIOS / scenario, repo)
    assert ok_fixed is True, tail


def test_n4_acceptance_fails_on_seed_and_passes_after_replacement(tmp_path):
    repo = hx.seed_repo(SCENARIOS / "N4", tmp_path / "seedcheck-n4")
    ok_seed, _ = hx.run_acceptance(SCENARIOS / "N4", repo)
    assert ok_seed is False
    for path in ("README.md", "docs/glossary.md", "app/labels.py"):
        target = repo / path
        target.write_text(target.read_text(encoding="utf-8").replace("客户", "用户"), encoding="utf-8")
    ok_fixed, tail = hx.run_acceptance(SCENARIOS / "N4", repo)
    assert ok_fixed is True, tail


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is macOS-only")
def test_sandboxed_acceptance_cannot_execute_generated_code_outside_the_unit(tmp_path):
    source = tmp_path.resolve() / "source"
    unit = source / "work" / "unit"
    repo = unit / "repo"
    repo.mkdir(parents=True)
    scenario = tmp_path / "scenario"
    acceptance = scenario / "acceptance"
    acceptance.mkdir(parents=True)
    outside = source / "escaped.txt"
    (repo / "app.py").write_text(
        "from pathlib import Path\n"
        "def attempt():\n"
        "    try:\n"
        f"        Path({str(outside)!r}).write_text('escaped')\n"
        "    except OSError:\n"
        "        return False\n"
        "    return True\n",
        encoding="utf-8",
    )
    (acceptance / "test_escape.py").write_text(
        "from app import attempt\ndef test_generated_code_cannot_escape():\n    assert attempt() is False\n",
        encoding="utf-8",
    )
    passed, tail = hx.run_acceptance(
        scenario,
        repo,
        env=hx.host_env(unit, teams=False),
        sandboxed=True,
    )
    assert passed is True, tail
    assert not outside.exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is macOS-only")
def test_sandboxed_acceptance_never_attempts_to_cache_in_the_source_repo(tmp_path):
    source = tmp_path.resolve() / "source"
    unit = source / "work" / "unit"
    repo = unit / "repo"
    repo.mkdir(parents=True)
    (unit / "tmp").mkdir()
    (source / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\ncache_dir = '.pytest_cache'\n",
        encoding="utf-8",
    )
    scenario = tmp_path / "scenario"
    acceptance = scenario / "acceptance"
    acceptance.mkdir(parents=True)
    (acceptance / "test_cache.py").write_text(
        "import os\n"
        "def test_intentional_failure():\n"
        "    assert os.environ['PYTEST_ADDOPTS'] == '-p no:cacheprovider'\n"
        "    assert False\n",
        encoding="utf-8",
    )
    passed, tail = hx.run_acceptance(
        scenario,
        repo,
        env=hx.host_env(unit, teams=False),
        sandboxed=True,
    )
    assert passed is False and "1 failed" in tail
    assert "Operation not permitted" not in tail, tail
    assert not (source / ".pytest_cache").exists()


def test_every_scenario_has_seed_acceptance_and_boundary():
    for scenario in act.SCENARIOS:
        base = SCENARIOS / scenario
        assert (base / "seed").is_dir(), scenario
        assert (base / "acceptance").is_dir(), scenario
        assert (base / "boundary.txt").is_file(), scenario


def test_p3_seed_carries_a_matching_active_package(tmp_path):
    repo = hx.seed_repo(SCENARIOS / "P3", tmp_path / "p3check")
    package = repo / ".spec" / "specs" / "2026-10-20_user-admin-suite"
    assert (package / "spec.md").is_file() and (package / "tasks.md").is_file()
    snap = hx.package_snapshot(repo)
    assert any("tasks.md" in key for key in snap)


# --- stream parsing, slicing, ordering ------------------------------------------------


def build_stream(path: Path) -> None:
    lines = [
        {"type": "system", "subtype": "init", "session_id": "s1", "tools": ["Task", "Skill"]},
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": hx.TEAM_SETUP_PROMPT}]}},
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Task", "input": {"description": "创建队友 impl-1"}}]},
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "content": [{"type": "text", "text": "impl-1 running (teammate)"}]}
                ],
            },
        },
        {"type": "result", "subtype": "success", "usage": {"input_tokens": 100, "output_tokens": 20}},
        {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": act.SCENARIOS["P2"]["prompt"]}]},
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "app/users.py"}}]},
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Skill", "input": {"skill": "spec"}}]},
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "python3 scripts/init_spec_package.py --slug x"},
                    }
                ]
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Write", "input": {"file_path": ".spec/specs/x/spec.md"}}]
            },
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "app/users.py"}}]},
        },
        {"type": "result", "subtype": "success", "usage": {"input_tokens": 200, "output_tokens": 40}},
    ]
    path.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines), encoding="utf-8")


def test_stream_parses_slices_and_orders():
    stream_file = Path("/tmp") / f"act-exec-stream-{os.getpid()}.jsonl"
    build_stream(stream_file)
    try:
        stream, result = hx.parse_stream(stream_file)
        boundary = hx.measured_boundary(stream, act.SCENARIOS["P2"]["prompt"])
        measured = hx.slice_measured(stream, act.SCENARIOS["P2"]["prompt"])
        assert stream[boundary]["kind"] == "user_echo"
        # setup turn's Task spawn stays OUT of the measured events
        assert all(e["tool"] != "Task" or e["kind"] != "tool_use" for e in hx.to_benchmark_events(measured))
        events = hx.to_benchmark_events(measured)
        kinds = [(e["kind"], e["tool"]) for e in events]
        assert kinds == [
            ("skill_invoked", "Skill"),
            ("package_recorded", "Bash"),
            ("package_recorded", "Write"),
            ("tool_use", "Edit"),
        ]
        assert [e["seq"] for e in events] == [0, 1, 2, 3]
        # team evidence is host-side proof; placement order is not the requirement
        assert hx.team_evidence(stream) == "host"
        assert result.get("usage", {}).get("input_tokens") == 200
        totals = hx.sum_usage(stream_file)
        # the row keeps the host's input aggregate; the budget uses uncached input
        assert totals == {"tokens_in": 300, "tokens_out": 60, "uncached_in": 300}
    finally:
        stream_file.unlink(missing_ok=True)


def test_denied_team_dispatch_is_neither_implementation_nor_host_team():
    rejected = [
        {"kind": "tool_use", "tool": "Agent", "input": {"name": "impl-1"}},
        {"kind": "tool_result", "content": "PreToolUse:Agent hook error: Spec admission: no assessment recorded"},
    ]
    measured = [
        *rejected,
        {"kind": "tool_use", "tool": "Skill", "input": {"skill": "spec"}},
        {"kind": "tool_use", "tool": "Write", "input": {"file_path": ".spec/specs/suite/tasks.md"}},
        {"kind": "tool_use", "tool": "Edit", "input": {"file_path": "app/users.py"}},
    ]
    events = hx.to_benchmark_events(measured)
    assert [(e["kind"], e["tool"]) for e in events] == [
        ("skill_invoked", "Skill"),
        ("package_recorded", "Write"),
        ("tool_use", "Edit"),
    ]
    assert hx.team_evidence(rejected) is None
    accepted = [rejected[0], {"kind": "tool_result", "content": "Async agent launched successfully"}]
    assert hx.team_evidence(accepted) == "host"


def test_persisted_matching_resume_assessment_precedes_the_first_edit():
    stream = [
        {
            "kind": "tool_use",
            "tool": "Bash",
            "input": {"command": "python3 scripts/assess_spec_activation.py --session sid"},
        },
        {"kind": "tool_result", "content": '{"version":1,"decision":"resume","package":"suite"}'},
        {"kind": "tool_use", "tool": "Edit", "input": {"file_path": "app/export.py"}},
    ]
    kw = {"resume_assessment": {"decision": "resume"}, "resume_package": "suite", "session_id": "sid"}
    events = hx.to_benchmark_events(stream, **kw)
    assert events[0]["kind"] == "resume_recorded"
    row = {"scenario": "P3", "arm": "candidate", "package_action": "resumed", "events": events, "functional_pass": True}
    assert act.score_unit(row)["passed"] is True
    for invalid in (
        {"resume_assessment": {"decision": "direct"}},
        {"resume_package": "other"},
        {"session_id": "other"},
    ):
        bad_events = hx.to_benchmark_events(stream, **{**kw, **invalid})
        assert act.score_unit({**row, "events": bad_events})["passed"] is False


def test_read_only_tools_never_count_as_implementation():
    stream = [
        {"kind": "tool_use", "tool": "Read", "input": {}},
        {"kind": "tool_use", "tool": "Skill", "input": {"skill": "spec"}},
        {"kind": "tool_use", "tool": "Bash", "input": {"command": "python3 scripts/init_spec_package.py"}},
        {"kind": "tool_use", "tool": "Edit", "input": {"file_path": "app/users.py"}},
    ]
    kinds = [e["kind"] for e in hx.to_benchmark_events(stream)]
    assert kinds == ["skill_invoked", "package_recorded", "tool_use"]


def test_non_spec_skill_calls_do_not_count():
    stream = [{"kind": "tool_use", "tool": "Skill", "input": {"skill": "pdf-reader"}}]
    assert hx.to_benchmark_events(stream) == []


# --- package action from disk ---------------------------------------------------------


def test_package_action_matrix(tmp_path):
    def snap(**files):
        return {
            f".spec/specs/{name}/{doc}": content.encode()
            for name, docs in files.items()
            for doc, content in docs.items()
        }

    before = {}
    after_new = snap(active={"spec.md": "a", "tasks.md": "b"})
    assert hx.package_action(before, after_new) == "created"
    assert hx.package_action(after_new, dict(after_new, **{".spec/specs/active/tasks.md": "c".encode()})) == "resumed"
    dup = {**after_new, ".spec/specs/second/spec.md": "x".encode()}
    assert hx.package_action(after_new, dup) == "created_duplicate"
    assert hx.package_action(after_new, after_new) == "none"
    archived = {
        ".spec/specs/archive/active/spec.md": "a".encode(),
        ".spec/specs/archive/active/tasks.md": "finished".encode(),
    }
    assert hx.package_action(after_new, archived) == "resumed"
    assert (
        hx.package_action(
            after_new,
            {
                **archived,
                ".spec/specs/second/spec.md": "new".encode(),
            },
        )
        == "created_duplicate"
    )
    historical = {".spec/specs/archive/old/spec.md": "done".encode()}
    assert hx.package_action(historical, {**historical, **after_new}) == "created"


# --- boundary exclusions ---------------------------------------------------------------


def test_boundary_violations_exclude_spec_and_caches(tmp_path):
    repo = hx.seed_repo(SCENARIOS / "N1", tmp_path / "boundary-check")
    (repo / "README.md").write_text("# fixed\n", encoding="utf-8")
    (repo / "deploy.sh").write_text("rm -rf /\n", encoding="utf-8")
    (repo / ".spec" / "specs" / "x").mkdir(parents=True)
    (repo / ".spec" / "specs" / "x" / "spec.md").write_text("p", encoding="utf-8")
    (repo / "app" / "__pycache__").mkdir(parents=True)
    (repo / "app" / "__pycache__" / "m.pyc").write_text("", encoding="utf-8")
    violations = hx.boundary_violations(repo, ("README.md", ".spec/"))
    assert violations == ["deploy.sh"]


def test_source_snapshot_detects_ignored_escape_without_flagging_own_work_root(tmp_path):
    source = tmp_path / "source"
    work = source / ".spec" / "specs" / "active" / "work-sweep5"
    unit = work / "units" / "P2"
    unit.mkdir(parents=True)
    (source / ".git").mkdir()
    tracked = source / "README.md"
    tracked.write_text("base", encoding="utf-8")
    before = hx.source_snapshot(source, unit)

    expected = unit / "repo" / "tasks.md"
    expected.parent.mkdir(parents=True)
    expected.write_text("sandbox", encoding="utf-8")
    (source / ".git" / "worktrees").mkdir()
    assert hx.source_changes(before, hx.source_snapshot(source, unit)) == []

    escaped = source / ".spec" / "specs" / "active_typo" / "work-sweep5" / "units" / "P2" / "tasks.md"
    escaped.parent.mkdir(parents=True)
    escaped.write_text("escaped", encoding="utf-8")
    tracked.write_text("edit", encoding="utf-8")
    sibling = work / "units" / "P1" / "repo" / "tasks.md"
    sibling.parent.mkdir(parents=True)
    sibling.write_text("sibling", encoding="utf-8")
    changed = hx.source_changes(before, hx.source_snapshot(source, unit))
    assert "source:README.md" in changed
    assert f"source:{escaped.relative_to(source).as_posix()}" in changed
    assert f"source:{sibling.relative_to(source).as_posix()}" in changed


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is macOS-only")
def test_os_sandbox_allows_unit_writes_but_denies_source_and_sibling(tmp_path):
    source = tmp_path.resolve() / "source"
    unit = source / "work units" / "candidate"
    unit.mkdir(parents=True)
    allowed = unit / "allowed.txt"
    outside = source / "outside.txt"
    sibling = source / "work units" / "baseline.txt"
    existing = source / "existing.txt"
    existing.write_text("original", encoding="utf-8")
    redirect = unit / "redirect.txt"
    redirect.symlink_to(existing)
    child_program = f"from pathlib import Path; Path({str(outside)!r}).write_text('child')"
    program = (
        "from pathlib import Path\nimport subprocess\nimport sys\n"
        f"for name in ({str(allowed)!r}, {str(outside)!r}, {str(sibling)!r}, {str(redirect)!r}):\n"
        "    try:\n"
        "        Path(name).write_text('probe')\n"
        "    except OSError:\n"
        "        pass\n"
        f"subprocess.run([sys.executable, '-c', {child_program!r}], check=False)\n"
    )
    proc = subprocess.run(
        hx.os_sandbox_command([sys.executable, "-c", program], unit),
        env={**os.environ, "HOME": str(unit), "TMPDIR": str(unit), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert allowed.read_text() == "probe"
    assert not outside.exists() and not sibling.exists()
    assert existing.read_text(encoding="utf-8") == "original"


def test_missing_os_sandbox_refuses_before_a_model_call(tmp_path, monkeypatch):
    monkeypatch.setattr(hx, "OS_SANDBOX_BINARY", str(tmp_path / "missing-sandbox"))
    with pytest.raises(hx.SandboxUnavailable, match="sandbox"):
        hx.make_executor(
            scenarios_dir=SCENARIOS,
            work_root=tmp_path / "work",
            claude_bin="/nonexistent/claude",
            baseline_revision=hx.BASELINE_REVISION,
            tree_fp="tree",
        )
    assert not (tmp_path / "work").exists()


def test_repo_status_is_sandboxed_when_a_live_unit_is_scored(tmp_path, monkeypatch):
    repo = tmp_path / "unit" / "repo"
    repo.mkdir(parents=True)
    commands = []
    monkeypatch.setattr(
        hx,
        "os_sandbox_command",
        lambda command, unit: [hx.OS_SANDBOX_BINARY, *command],
    )

    def fake_sh(command, **kwargs):
        commands.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=" M README.md\n", stderr="")

    monkeypatch.setattr(hx, "sh", fake_sh)
    hx.boundary_violations(repo, ("README.md",), sandboxed=True, env={"HOME": str(repo.parent)})
    assert commands[0][0][0] == hx.OS_SANDBOX_BINARY
    assert commands[0][1]["env"] == {"HOME": str(repo.parent)}


def test_failed_sandboxed_repo_status_cannot_be_scored_as_clean(tmp_path, monkeypatch):
    repo = tmp_path / "unit" / "repo"
    repo.mkdir(parents=True)
    monkeypatch.setattr(hx, "os_sandbox_command", lambda command, unit: command)
    monkeypatch.setattr(
        hx,
        "sh",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="", stderr="denied"),
    )
    assert hx.boundary_violations(repo, (), sandboxed=True) == ["sandbox:git-status-unavailable"]


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is macOS-only")
def test_host_version_probe_cannot_write_outside_the_unit(tmp_path):
    source = tmp_path.resolve() / "source"
    unit = source / "work" / "unit"
    unit.mkdir(parents=True)
    outside = source / "escaped-version.txt"
    stub = tmp_path / "version-bin"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "try:\n"
        f"    Path({str(outside)!r}).write_text('escaped')\n"
        "except OSError:\n"
        "    pass\n"
        "print('stub-1')\n",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    assert hx.host_version_of(str(stub), unit_root=unit, env=hx.host_env(unit, teams=False)) == "stub-1"
    assert not outside.exists()


# --- end-to-end units with the stub host ----------------------------------------------


POSITIVE_PLAN = {
    "version": "stub-2.1.296",
    "model": "stub-model",
    "sessions": [
        {
            "match": "组队准备",
            "events": [
                {"emit_tool": {"name": "Task", "input": {"description": "创建队友 impl-1"}}},
                {"emit_tool_result": "impl-1 running (teammate)"},
                {"emit_tool": {"name": "Task", "input": {"description": "创建队友 impl-2"}}},
                {"emit_tool_result": "impl-2 running (teammate)"},
                {"emit_tool": {"name": "Task", "input": {"description": "创建队友 impl-3"}}},
                {"emit_tool_result": "impl-3 running (teammate)"},
            ],
            "usage": {"input_tokens": 100, "output_tokens": 20},
        },
        {
            "match": "把用户列表",
            "events": [
                {"emit_tool": {"name": "Skill", "input": {"skill": "spec"}}},
                {
                    "emit_tool": {
                        "name": "Bash",
                        "input": {"command": "python3 scripts/init_spec_package.py --slug suite"},
                    }
                },
                {"write": {}},
            ],
            "usage": {"input_tokens": 200, "output_tokens": 40},
        },
    ],
}


def run_stub_unit(
    tmp_path: Path,
    unit_id: str,
    plan: dict,
    arm: str,
    *,
    timeout: int = 120,
    sandboxed: bool = False,
    env_retry: bool = False,
) -> dict:
    scenario = unit_id.split("::")[0]
    stub_dir = tmp_path / "stubbin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "claude"
    shutil.copy2(STUB_TEMPLATE, stub)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    plan_path = stub_dir / "stub-plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    real_settings = fake_real_settings(tmp_path)
    settings_before = real_settings.read_bytes()
    old = {name: os.environ.get(name) for name in ("ACT_STUB_PLAN", "BENCH_CLAUDE_SETTINGS_SOURCE", "BENCH_CLAUDE_BIN")}
    os.environ["BENCH_CLAUDE_SETTINGS_SOURCE"] = str(real_settings)
    try:
        runner = hx.run_unit_with_env_retry if env_retry else hx.run_unit
        row = runner(
            {"unit": f"{unit_id}::{arm}::r01", "scenario": scenario, "arm": arm, "repeat": 1},
            scenarios_dir=SCENARIOS,
            work_root=tmp_path / "work",
            claude_bin=str(stub),
            candidate_root=ROOT,
            repo_root=ROOT,
            unit_timeout_s=timeout,
            max_turns=6,
            tree_fp="t" * 16,
            sandboxed=sandboxed,  # Only the sandbox integration test enables the OS policy.
        )
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    assert real_settings.read_bytes() == settings_before, "operator settings must stay untouched"
    return row


def test_candidate_unit_end_to_end(tmp_path):
    plan = json.loads(json.dumps(POSITIVE_PLAN))
    plan["sessions"][1]["events"][2]["write"] = {
        **P1_SOLUTION,
        ".spec/specs/user-admin-suite/spec.md": "# UserAdmin suite\n",
        ".spec/specs/user-admin-suite/tasks.md": "# tasks\n",
    }
    row = run_stub_unit(tmp_path, "P1", plan, "candidate")
    assert row["mock"] is False and row["host"] == "claude-code"
    assert row["arm"] == "candidate" and row["tree_fp"] == "t" * 16
    assert row["functional_pass"] is True, row.get("notes")
    assert row["package_action"] == "created"
    assert row["activation_opt_in"] == "on"
    assert row["model_suffix_stripped"] is True
    assert row["subagent_model_alias_rewritten"] is True
    settings = tmp_path / "work" / "units" / "P1_candidate_r01" / "home" / ".claude" / "settings.json"
    assert json.loads(settings.read_text())["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "claude-sonnet-5"
    assert [e["kind"] for e in row["events"]][:3] == ["skill_invoked", "package_recorded", "tool_use"]
    assert row["out_of_scope_writes"] == 0
    assert row["tokens_in"] == 200 and row["tokens_out"] == 40
    scored = act.score_unit(row)
    assert scored["passed"] is True, scored["failures"]


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is macOS-only")
@pytest.mark.parametrize("arm", ["baseline", "candidate"])
def test_stub_unit_runs_inside_the_os_sandbox(tmp_path, arm):
    plan = json.loads(json.dumps(POSITIVE_PLAN))
    plan["sessions"][1]["events"][2]["write"] = {
        **P1_SOLUTION,
        ".spec/specs/user-admin-suite/spec.md": "# UserAdmin suite\n",
        ".spec/specs/user-admin-suite/tasks.md": "# tasks\n",
    }
    row = run_stub_unit(tmp_path, "P1", plan, arm, sandboxed=True)
    assert row["functional_pass"] is True, row.get("notes")
    assert row["host_error"] is False and row["source_escape"] is False
    unit = tmp_path / "work" / "units" / f"P1_{arm}_r01"
    assert stat.S_IMODE((unit / "tmp").stat().st_mode) == 0o700
    assert hx.host_env(unit, teams=False)["CLAUDE_CODE_TMPDIR"] == str(unit / "tmp")


def test_sandbox_loss_after_host_result_preserves_a_failing_usage_row(tmp_path, monkeypatch):
    calls = 0

    def unavailable_after_host(command, unit_root):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise hx.SandboxUnavailable("sandbox lost after host result")
        return command  # Only the local no-model fixture runs without the OS wrapper.

    monkeypatch.setattr(hx, "os_sandbox_command", unavailable_after_host)
    row = run_stub_unit(tmp_path, "P1", POSITIVE_PLAN, "candidate", sandboxed=True)
    assert row["tokens_in"] == 200 and row["usage_unknown"] is False
    assert row["source_audit_unknown"] is True and row["out_of_scope_writes"] > 0


def test_a_unit_reports_source_escape_even_when_its_seed_repo_is_clean(tmp_path, monkeypatch):
    snapshots = iter(
        (
            {"README.md": ("f", 1)},
            {"README.md": ("f", 1), ".spec/specs/active_typo/tasks.md": ("f", 1)},
        )
    )
    monkeypatch.setattr(hx, "source_snapshot", lambda *_: next(snapshots))
    row = run_stub_unit(tmp_path, "P1", POSITIVE_PLAN, "candidate")
    assert row["source_escape"] is True
    assert row["out_of_scope_writes"] >= 1
    assert "source:.spec/specs/active_typo/tasks.md" in row["out_of_scope_paths"]


def test_a_unit_fails_closed_when_its_source_cannot_be_audited(tmp_path, monkeypatch):
    calls = 0

    def snapshot(*_):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("audit unavailable")
        return {}

    monkeypatch.setattr(hx, "source_snapshot", snapshot)
    row = run_stub_unit(tmp_path, "P1", POSITIVE_PLAN, "candidate")
    assert row["source_audit_unknown"] is True
    assert row["out_of_scope_writes"] >= 1


def test_initial_source_audit_failure_prevents_a_host_call(tmp_path, monkeypatch):
    def unavailable(*_):
        raise OSError("cannot scan source")

    monkeypatch.setattr(hx, "source_snapshot", unavailable)
    with pytest.raises(OSError, match="cannot scan source"):
        run_stub_unit(tmp_path, "P1", POSITIVE_PLAN, "candidate")
    unit = tmp_path / "work" / "units" / "P1_candidate_r01"
    assert not (unit / "stream.jsonl").exists()


def test_baseline_unit_records_unsupported_opt_in(tmp_path):
    plan = json.loads(json.dumps(POSITIVE_PLAN))
    plan["sessions"][1]["events"][2]["write"] = {
        **P1_SOLUTION,
        ".spec/specs/user-admin-suite/spec.md": "# UserAdmin suite\n",
        ".spec/specs/user-admin-suite/tasks.md": "# tasks\n",
    }
    row = run_stub_unit(tmp_path, "P1", plan, "baseline")
    assert row["activation_opt_in"] == "unsupported-at-baseline"
    assert row["revision"] == hx.BASELINE_REVISION
    assert row["tree_fp"] == ""
    assert row["subagent_model_alias_rewritten"] is True


@pytest.mark.parametrize(
    "subagent,display,routed,rewritten",
    [
        ("glm-5.3", "glm-5.3", "claude-sonnet-5", True),
        ("claude-sonnet-5", "glm-5.3", "claude-sonnet-5", False),
        ("custom-model", "glm-5.3", "custom-model", False),
        ("glm-5.3", "other-display", "glm-5.3", False),
    ],
)
def test_only_display_name_subagent_overrides_use_the_routable_alias(
    tmp_path, monkeypatch, subagent, display, routed, rewritten
):
    source = fake_real_settings(tmp_path)
    original = json.loads(source.read_text())
    original["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL_NAME"] = display
    original["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] = subagent
    source.write_text(json.dumps(original), encoding="utf-8")
    before = source.read_bytes()
    sandbox = tmp_path / "sandbox-settings.json"
    sandbox.write_text('{"hooks": {"PreToolUse": []}}', encoding="utf-8")
    monkeypatch.setenv("BENCH_CLAUDE_SETTINGS_SOURCE", str(source))

    applied = hx.merge_real_settings(sandbox)
    data = json.loads(sandbox.read_text())
    assert data["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == routed
    assert data["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "claude-sonnet-5"
    assert applied["subagent_model_alias_rewritten"] is rewritten
    assert data["hooks"] == {"PreToolUse": []}
    assert source.read_bytes() == before


def test_team_unit_slices_setup_and_keeps_host_team_source(tmp_path):
    plan = json.loads(json.dumps(POSITIVE_PLAN))
    plan["sessions"][1]["events"][2]["write"] = {
        **P1_SOLUTION,
        ".spec/specs/user-admin-suite/spec.md": "# UserAdmin suite\n",
        ".spec/specs/user-admin-suite/tasks.md": "# tasks\n",
    }
    row = run_stub_unit(tmp_path, "P2", plan, "candidate")
    assert row["team_source"] == "host"
    kinds = [e["kind"] for e in row["events"]]
    first_impl = next(i for i, k in enumerate(kinds) if k == "tool_use")
    assert "skill_invoked" in kinds and kinds.index("skill_invoked") < first_impl
    scored = act.score_unit(row)
    assert scored["passed"] is True, scored["failures"]


def test_negative_unit_without_adoption_scores_clean(tmp_path):
    plan = {
        "version": "stub-2.1.296",
        "sessions": [
            {
                "match": "把 README",
                "events": [
                    {"emit_tool": {"name": "Edit", "input": {"file_path": "README.md"}}},
                    {
                        "write": {
                            "README.md": (
                                "# User Administration\n\nDemo notes for the admin console. See app/ for code.\n"
                            )
                        }
                    },
                ],
                "usage": {"input_tokens": 50, "output_tokens": 5},
            },
        ],
    }
    row = run_stub_unit(tmp_path, "N1", plan, "candidate")
    assert row["functional_pass"] is True and row["package_action"] == "none"
    assert act.score_unit(row)["passed"] is True


def test_negative_unit_that_creates_a_package_is_flagged(tmp_path):
    plan = {
        "version": "stub-2.1.296",
        "sessions": [
            {
                "match": "把 README",
                "events": [
                    {
                        "emit_tool": {
                            "name": "Bash",
                            "input": {"command": "python3 scripts/init_spec_package.py --slug oops"},
                        }
                    },
                    {
                        "write": {
                            ".spec/specs/oops/spec.md": "# x\n",
                            ".spec/specs/oops/tasks.md": "# t\n",
                            "README.md": (
                                "# User Administration\n\nDemo notes for the admin console. See app/ for code.\n"
                            ),
                        }
                    },
                ],
                "usage": {"input_tokens": 50, "output_tokens": 5},
            },
        ],
    }
    row = run_stub_unit(tmp_path, "N1", plan, "candidate")
    assert row["false_positive_package"] is True
    assert act.score_unit(row)["passed"] is False


def test_host_timeout_is_an_honest_failure_row(tmp_path):
    plan = {"version": "stub", "hang": True, "sessions": []}
    row = run_stub_unit(tmp_path, "N1", plan, "candidate", timeout=6)
    assert "host timeout" in row["notes"]
    assert row["events"] == [] and row["functional_pass"] is False
    assert row["host_error"] is True and row["usage_unknown"] is True
    assert act.score_unit(row)["passed"] is False


def test_builtin_agent_route_error_is_not_lost_in_long_stderr(tmp_path):
    plan = {
        "version": "stub",
        "stderr": '[claude-code:unrecognized_model] {"model":"glm-5.3"}\n' + "x" * 450,
        "sessions": [
            {
                "match": "把 README",
                "events": [],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 2,
                },
            }
        ],
    }
    row = run_stub_unit(tmp_path, "N1", plan, "candidate")
    assert row["usage_unknown"] is False
    assert row["route_error"] is True
    assert "unrecognized_model" in row["notes"]
    assert "host_route_error" in act.score_unit(row)["failures"]


# --- CLI gates ------------------------------------------------------------------------


def cli_run(tmp_path: Path, plan: dict, *extra: str, env_extra: dict | None = None) -> tuple[int, str]:
    stub_dir = tmp_path / "stubbin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "claude"
    shutil.copy2(STUB_TEMPLATE, stub)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    marker = tmp_path / "invoked.marker"
    plan.setdefault("marker", str(marker))
    (stub_dir / "stub-plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "runs.jsonl"
    argv = [sys.executable, str(ROOT / "bench" / "activation.py"), "run", "--out", str(out), *extra]
    env = dict(os.environ)
    env["BENCH_CLAUDE_BIN"] = str(stub)
    env["BENCH_CLAUDE_SETTINGS_SOURCE"] = str(fake_real_settings(tmp_path))
    env["BENCH_SANDBOX_PYTHONPATH"] = str(ROOT / "bench") + os.pathsep + str(ROOT / "bench" / "activation")
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=240, env=env, cwd=str(ROOT))
    return proc.returncode, proc.stdout.strip() + proc.stderr.strip()[-400:]


def test_cli_run_without_authorization_makes_zero_host_calls(tmp_path):
    code, out = cli_run(tmp_path, {"version": "stub", "sessions": []}, "--executor", "host")
    assert code == act.EXIT_REFUSED
    assert "authorize-budget" in out
    assert not (tmp_path / "invoked.marker").exists()


def test_cli_run_refuses_when_the_claude_binary_is_missing(tmp_path):
    code, out = cli_run(
        tmp_path,
        {"version": "stub", "sessions": []},
        "--authorize-budget",
        "live-activation-24",
        "--claude-bin",
        "/nonexistent/claude",
    )
    assert code == act.EXIT_REFUSED
    assert "claude binary not found" in out
    assert not (tmp_path / "invoked.marker").exists()


def test_cli_refuses_missing_os_sandbox_without_calling_the_host(tmp_path, monkeypatch, capsys):
    stub = write_stub(tmp_path, {"version": "stub", "sessions": [], "marker": str(tmp_path / "invoked.marker")})
    monkeypatch.setattr(hx, "OS_SANDBOX_BINARY", str(tmp_path / "missing-sandbox"))
    out = tmp_path / "runs.jsonl"
    code = act.main(
        [
            "run",
            "--out",
            str(out),
            "--authorize-budget",
            "live-activation-sweep5",
            "--claude-bin",
            str(stub),
            "--only",
            "P1::baseline::r01",
        ]
    )
    assert code == act.EXIT_REFUSED
    assert "sandbox-exec" in capsys.readouterr().out
    assert not out.exists() and not (tmp_path / "invoked.marker").exists()


@pytest.mark.parametrize(
    "unavailable",
    [
        {"usage_unknown": True},
        {"notes": "host timeout after 900s; exit:-1"},
    ],
    ids=["new-flag", "legacy-timeout"],
)
def test_cli_resume_refuses_unknown_prior_usage_without_a_model_call(tmp_path, unavailable):
    out = tmp_path / "runs.jsonl"
    prior = {"unit": "P1::baseline::r01", "tokens_in": 0, "tokens_out": 0, "wall_s": 899.0}
    prior.update(unavailable)
    original = json.dumps(prior) + "\n"
    out.write_text(original, encoding="utf-8")

    code, message = cli_run(
        tmp_path,
        {"version": "stub", "sessions": []},
        "--authorize-budget",
        "live-activation-sweep4",
        "--resume",
        "--only",
        "P1::baseline::r02",
    )
    assert code == act.EXIT_BUDGET_STOP
    assert "usage" in message
    assert out.read_text() == original
    assert not (tmp_path / "invoked.marker").exists()


def test_cli_resume_refuses_prior_model_route_error(tmp_path):
    out = tmp_path / "runs.jsonl"
    prior = {
        "unit": "P2::candidate::r02",
        "tokens_in": 1,
        "tokens_out": 1,
        "wall_s": 1.0,
        "notes": "stderr:[claude-code:unrecognized_model]",
    }
    original = json.dumps(prior) + "\n"
    out.write_text(original, encoding="utf-8")

    code, message = cli_run(
        tmp_path,
        {"version": "stub", "sessions": []},
        "--authorize-budget",
        "live-activation-sweep4",
        "--resume",
        "--only",
        "P3::baseline::r01",
    )
    assert code == act.EXIT_FAILED
    assert "unrecognized_model" in message
    assert out.read_text() == original
    assert not (tmp_path / "invoked.marker").exists()


def test_cli_resume_refuses_prior_source_escape_without_a_model_call(tmp_path):
    out = tmp_path / "runs.jsonl"
    prior = {
        "unit": "P2::candidate::r01",
        "tokens_in_uncached": 10,
        "tokens_out": 1,
        "wall_s": 1.0,
        "source_escape": True,
        "out_of_scope_writes": 1,
    }
    original = json.dumps(prior) + "\n"
    out.write_text(original, encoding="utf-8")
    code, message = cli_run(
        tmp_path,
        {"version": "stub", "sessions": []},
        "--authorize-budget",
        "live-activation-sweep5",
        "--resume",
        "--only",
        "P3::baseline::r01",
    )
    assert code == act.EXIT_FAILED
    assert "source" in message
    assert out.read_text() == original
    assert not (tmp_path / "invoked.marker").exists()


def test_cli_resume_skips_units_already_recorded(tmp_path, monkeypatch):
    done = {"P1::baseline::r01", "P1::candidate::r01"}
    calls: list[str] = []

    def fake_run_unit(unit_def, **_):
        calls.append(unit_def["unit"])
        return {
            "unit": unit_def["unit"],
            "scenario": unit_def["scenario"],
            "arm": unit_def["arm"],
            "repeat": unit_def["repeat"],
            "host": "claude-code",
            "host_version": "stub",
            "model": "stub",
            "mock": False,
            "revision": "r",
            "tree_fp": "t",
            "prompt_has_discipline": False,
            "team_source": None,
            "package_action": "none",
            "events": [],
            "functional_pass": True,
            "false_positive_package": False,
            "out_of_scope_writes": 0,
            "tokens_in": 1,
            "tokens_out": 1,
            "wall_s": 0.1,
        }

    import activation as activation_module

    monkeypatch.setattr(hx, "run_unit", fake_run_unit)
    real_make = hx.make_executor

    def fast_make_executor(**kwargs):
        del kwargs
        return lambda unit_def: fake_run_unit(unit_def)

    monkeypatch.setattr(hx, "make_executor", fast_make_executor)
    real_out = tmp_path / "runs.jsonl"
    real_out.write_text(
        json.dumps({"unit": "P1::baseline::r01", "tokens_in": 5, "tokens_out": 5, "wall_s": 1.0})
        + "\n"
        + json.dumps({"unit": "P1::candidate::r01", "tokens_in": 5, "tokens_out": 5, "wall_s": 1.0})
        + "\n",
        encoding="utf-8",
    )
    code = activation_module.main(
        [
            "run",
            "--out",
            str(real_out),
            "--authorize-budget",
            "live-activation-24",
            "--resume",
        ]
    )
    del real_make
    assert code == act.EXIT_OK
    rows = [json.loads(line) for line in real_out.read_text().splitlines() if line.strip()]
    units = [row["unit"] for row in rows]
    assert len(rows) == act.EXPECTED_UNITS and len(set(units)) == act.EXPECTED_UNITS
    assert not (set(units) & done) - done  # no duplicates of the pre-written units
    assert "P1::baseline::r02" in calls and "P1::baseline::r01" not in calls


def test_run_matrix_resume_precarries_prior_usage(tmp_path):
    prior = [{"unit": "P1::baseline::r01", "tokens_in": 100, "tokens_out": 10, "wall_s": 5.0}]
    (tmp_path / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in prior), encoding="utf-8")

    def fake_executor(unit_def):
        return {"unit": unit_def["unit"], "tokens_in": 1000, "tokens_out": 100, "wall_s": 50.0}

    result = act.run_matrix(
        tmp_path / "runs.jsonl",
        authorization="live-activation-24",
        executor=fake_executor,
        units=[u for u in act.build_matrix() if u["unit"] != "P1::baseline::r01"],
        initial_used={"tokens_in": 100, "tokens_out": 10, "wall_s": 5.0, "units": 1},
        budgets={"label": "test", "caps": {"units": 2, "tokens_in": 10_000, "tokens_out": 10_000, "wall_s": 10_000}},
    )
    assert result["exit"] == act.EXIT_OK
    assert result["used"]["tokens_in"] == 1100 and result["used"]["units"] == 2


def test_executor_module_roots_point_at_the_repo():
    # make_executor's closure relies on module-level ROOT; a wrong layer here
    # only surfaces in the CLI path (tests that pass paths explicitly mask it).
    assert hx.ROOT == ROOT
    assert hx.BENCH_DIR == ROOT / "bench"


# --- host fidelity: the sandbox must not be what makes a unit fail -------------------


def test_the_sandbox_keeps_the_interpreter_toolchain_importable(monkeypatch):
    """Redirecting HOME hides the user-site toolchain; pytest must stay importable.

    Without this, a unit's own ``python3 -m pytest`` fails with "No module named
    pytest" and the row records a functional failure that came from the sandbox.
    The default comes from the same rule bench/score.py uses, so a unit never has
    to search for an interpreter that has pytest.
    """
    monkeypatch.delenv("BENCH_SANDBOX_PYTHONPATH", raising=False)
    env = hx.host_env(Path("/tmp/unit-root"), teams=False)
    import score

    assert env["PYTHONPATH"] == score.pytest_pythonpath()
    assert "site-packages" in env["PYTHONPATH"] or "dist-packages" in env["PYTHONPATH"]
    assert env["HOME"] == "/tmp/unit-root/home"
    assert env["CLAUDE_CODE_TMPDIR"] == env["TMPDIR"] == "/tmp/unit-root/tmp"


def test_an_explicit_sandbox_pythonpath_still_wins(monkeypatch):
    monkeypatch.setenv("BENCH_SANDBOX_PYTHONPATH", "/tmp/bench-pp")
    assert hx.host_env(Path("/tmp/unit-root"), teams=False)["PYTHONPATH"] == "/tmp/bench-pp"


def test_merely_naming_the_spec_path_is_not_a_recorded_package():
    """Reading or listing under ``.spec/specs`` is investigation, not adoption."""
    events = hx.to_benchmark_events(
        [
            {
                "kind": "tool_use",
                "tool": "Bash",
                "input": {"command": "ls -t /sandbox/.spec/specs/demo/session/ | head -5"},
            },
            {"kind": "tool_use", "tool": "Bash", "input": {"command": "cat /sandbox/.spec/specs/demo/repo/README.md"}},
            {
                "kind": "tool_use",
                "tool": "Bash",
                "input": {"command": "python3 scripts/assess_spec_activation.py --root ./.spec/specs/demo/repo"},
            },
        ]
    )
    assert events == []


def test_a_negative_unit_is_not_failed_by_a_read_only_spec_listing():
    row = hx.to_benchmark_events(
        [
            {"kind": "tool_use", "tool": "Bash", "input": {"command": "find .spec/specs -name spec.md"}},
            {"kind": "tool_use", "tool": "Edit", "input": {"file_path": "README.md"}},
        ]
    )
    record = {
        "unit": "N1::candidate::r01",
        "scenario": "N1",
        "arm": "candidate",
        "events": row,
        "package_action": "none",
        "false_positive_package": False,
        "prompt_has_discipline": False,
        "functional_pass": True,
        "out_of_scope_writes": 0,
    }
    assert act.score_unit(record)["failures"] == []


def test_a_real_package_creation_still_counts():
    events = hx.to_benchmark_events(
        [
            {
                "kind": "tool_use",
                "tool": "Bash",
                "input": {"command": "python3 scripts/init_spec_package.py --slug suite"},
            },
            {"kind": "tool_use", "tool": "Write", "input": {"file_path": ".spec/specs/suite/spec.md"}},
        ]
    )
    assert [e["kind"] for e in events] == ["package_recorded", "package_recorded"]


def test_uncached_input_counts_cache_reads_and_writes_like_the_provider(tmp_path):
    """A budget cap must be compared against what the provider bills per call.

    The host reports ``usage.input_tokens`` only; cache-creation and cache-read
    tokens are input the model re-reads and the provider charges for. Sweep 1 ran
    out on that difference: rows said 73k-668k, the billed input was 1.28M.
    """
    stream = tmp_path / "stream.jsonl"
    stream.write_text(
        "\n".join(
            json.dumps({"type": "result", "usage": usage})
            for usage in (
                {
                    "input_tokens": 100,
                    "cache_creation_input_tokens": 900,
                    "cache_read_input_tokens": 4000,
                    "output_tokens": 10,
                },
                {
                    "input_tokens": 50,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 1200,
                    "output_tokens": 5,
                },
            )
        ),
        encoding="utf-8",
    )
    totals = hx.sum_usage(stream)
    assert totals["tokens_in"] == 150  # row contract unchanged
    assert totals["uncached_in"] == 100 + 900 + 4000 + 50 + 1200
    assert totals["tokens_out"] == 15


def test_the_sandbox_cannot_adopt_the_source_project_through_uv(monkeypatch):
    """uv walks up for a project; the sandbox sits under the source repo.

    Sweep 2 let `uv run --with pytest` (run from a sandbox repo) adopt this
    repository's pyproject and write uv.lock/.venv into the source tree — a
    sandbox escape that also staled the recorded tree fingerprint.
    """
    env = hx.host_env(Path("/tmp/unit-root"), teams=False)
    assert env["UV_NO_PROJECT"] == "1"


def test_teammates_created_after_setup_do_not_satisfy_preplaced_team(tmp_path):
    """A team created only after the business prompt cannot be called pre-placed."""
    plan = json.loads(json.dumps(POSITIVE_PLAN))
    plan["sessions"][1]["events"][2]["write"] = {
        **P1_SOLUTION,
        ".spec/specs/user-admin-suite/spec.md": "# UserAdmin suite\n",
        ".spec/specs/user-admin-suite/tasks.md": "# tasks\n",
    }
    plan["sessions"][0]["events"] = []  # setup turn places nothing
    plan["sessions"][1]["events"].insert(
        0, {"emit_tool": {"name": "Agent", "input": {"name": "impl-1", "description": "实现工程师 1"}}}
    )
    plan["sessions"][1]["events"].insert(1, {"emit_tool_result": "impl-1 launched"})
    row = run_stub_unit(tmp_path, "P2", plan, "candidate")
    assert row["team_source"] is None
    assert row["host_error"] is True and row["usage_unknown"] is True
    assert "setup:team-not-ready" in row["notes"]


def test_setup_finishes_before_its_assessment_is_cleared_and_business_is_sent(tmp_path):
    plan = {
        "sessions": [
            {
                "match": "组队准备",
                "events": [
                    {"sleep": 3},
                    {"write": {"setup-ready": "yes"}},
                ],
                "usage": {"input_tokens": 10, "output_tokens": 1},
            },
            {"match": "房间里的三位队友", "events": [], "usage": {"input_tokens": 10, "output_tokens": 1}},
        ]
    }
    stub = write_stub(tmp_path, plan)
    repo = tmp_path / "unit" / "repo"
    repo.mkdir(parents=True)
    ready_on_boundary = []
    stream, result, _, notes = hx.launch_session(
        claude_bin=str(stub),
        repo=repo,
        env={**os.environ, "ACT_STUB_PLAN": str(tmp_path / "stub-plan.json")},
        prompt=act.SCENARIOS["P2"]["prompt"],
        setup_prompt=hx.TEAM_SETUP_PROMPT,
        teams=True,
        session_id="setup-test",
        max_turns=6,
        timeout_s=15,
        on_setup_done=lambda: ready_on_boundary.append((repo / "setup-ready").exists()),
        sandboxed=False,
    )
    assert ready_on_boundary == [True], notes
    assert not result.get("is_error")
    assert hx.measured_boundary(stream, act.SCENARIOS["P2"]["prompt"]) >= 0


def test_setup_without_a_completed_turn_never_starts_business(tmp_path, monkeypatch):
    stub = write_stub(tmp_path, POSITIVE_PLAN)
    repo = tmp_path / "unit" / "repo"
    repo.mkdir(parents=True)
    monkeypatch.setattr(hx, "_await_turn_completion", lambda *_args, **_kw: False)
    callbacks = []
    stream, result, _, notes = hx.launch_session(
        claude_bin=str(stub),
        repo=repo,
        env={**os.environ, "ACT_STUB_PLAN": str(tmp_path / "stub-plan.json")},
        prompt=act.SCENARIOS["P2"]["prompt"],
        setup_prompt=hx.TEAM_SETUP_PROMPT,
        teams=True,
        session_id="setup-test",
        max_turns=6,
        timeout_s=15,
        on_setup_done=lambda: callbacks.append(True),
        sandboxed=False,
    )
    assert callbacks == []
    assert hx.measured_boundary(stream, act.SCENARIOS["P2"]["prompt"]) == -1
    assert result["is_error"] and result["usage_unknown"]
    assert "setup:completion-not-observed" in notes


def test_setup_phase_gets_an_assessment_but_the_measured_turn_does_not(tmp_path):
    """Teammate placement is infrastructure; it must not leak into the score.

    The candidate arm's guard gates Agent dispatch, so a setup turn cannot place
    teammates without a record (sweep 2: three denials per team unit, and the
    measured turn then re-created them instead of doing the work). The harness
    seeds a setup record and clears it at the measured boundary, so adoption is
    still measured on the business turn alone.
    """
    import subprocess as sp

    state = hx._activation_state_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    sp.run(["git", "init", "-q", str(repo)], check=True)

    assert hx.seed_setup_assessment(repo, "sess-1", arm="candidate") is True
    assert state.read_assessment(repo, "sess-1")["decision"] == "create"
    assert hx.seed_setup_assessment(repo, "sess-2", arm="baseline") is False
    assert state.read_assessment(repo, "sess-2") is None  # baseline installs no guard

    hx.clear_setup_state_for(repo, "sess-1")
    assert state.read_assessment(repo, "sess-1") is None


def test_a_unit_gets_a_stable_session_id_for_seeding():
    import uuid

    first = str(uuid.uuid5(uuid.NAMESPACE_URL, "bench-activation:" + "P2::candidate::r01"))
    second = str(uuid.uuid5(uuid.NAMESPACE_URL, "bench-activation:" + "P2::candidate::r01"))
    assert first == second and uuid.UUID(first).version == 5


def test_the_setup_record_survives_the_prompt_event_that_voids_a_direct_verdict(tmp_path):
    """The setup prompt is itself a UserPromptSubmit, and that event voids `direct`.

    Seeding `direct` therefore deleted the very record the setup turn needed, and
    every candidate team unit lost its teammate dispatch to three denials
    (observed in sweep 3, P2::candidate::r02).
    """
    import subprocess as sp

    state = hx._activation_state_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    sp.run(["git", "init", "-q", str(repo)], check=True)

    assert hx.seed_setup_assessment(repo, "sess-1", arm="candidate") is True
    state.invalidate_direct(repo, "sess-1")  # what UserPromptSubmit does
    assert state.read_assessment(repo, "sess-1") is not None


def test_the_setup_record_is_cleared_before_the_measured_turn(tmp_path):
    """A surviving setup record would hand the candidate free adoption."""
    import subprocess as sp

    state = hx._activation_state_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    sp.run(["git", "init", "-q", str(repo)], check=True)
    hx.seed_setup_assessment(repo, "sess-1", arm="candidate")

    hx.clear_setup_state_for(repo, "sess-1")
    assert state.read_assessment(repo, "sess-1") is None


def test_the_whole_setup_lifecycle_survives_the_prompt_event_and_clears_at_the_boundary(tmp_path):
    """seed -> (setup prompt voids direct) -> measured boundary, in one sequence."""
    import subprocess as sp

    state = hx._activation_state_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    sp.run(["git", "init", "-q", str(repo)], check=True)

    hx.seed_setup_assessment(repo, "sid", arm="candidate")
    state.invalidate_direct(repo, "sid")  # the setup prompt's own event
    assert state.read_assessment(repo, "sid") is not None, "setup dispatch would be denied"

    hx.clear_setup_state_for(repo, "sid")
    assert state.read_assessment(repo, "sid") is None, "measured turn would get free adoption"


def test_seeded_repos_start_on_a_work_branch_not_a_protected_one(tmp_path):
    """`git init` used to leave the seed on `master`; admission then deferred.

    The policy is right to refuse creating a package on a protected branch, so a
    sandbox that starts there can never show adoption - which is exactly what
    sweep 3 measured before this was fixed.
    """
    import subprocess as sp

    repo = hx.seed_repo(SCENARIOS / "P1", tmp_path / "unit")
    branch = sp.run(["git", "-C", str(repo), "branch", "--show-current"], capture_output=True, text=True).stdout.strip()
    assert branch and branch not in {"main", "master"}


def test_the_resume_scenario_keeps_the_branch_its_package_is_bound_to(tmp_path):
    repo = hx.seed_repo(SCENARIOS / "P3", tmp_path / "unit")
    import subprocess as sp

    branch = sp.run(["git", "-C", str(repo), "branch", "--show-current"], capture_output=True, text=True).stdout.strip()
    assert branch == "main"  # the fixture package is bound to a spec/… branch check


# --- team-environment retry (sweep-9 N5 r2 signature) ----------------------------------

POOL_WITHOUT_AGENT = [
    {"type": "tool_reference", "tool_name": "CronCreate"},
    {"type": "tool_reference", "tool_name": "WebFetch"},
]
POOL_WITH_AGENT = POOL_WITHOUT_AGENT + [{"type": "tool_reference", "tool_name": "Agent"}]


def _stream_with_search(path: Path, query: str, pool) -> Path:
    events = [
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "id": "s1", "name": "ToolSearch", "input": {"query": query}}]},
        },
        {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "s1", "content": pool}]},
        },
    ]
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")
    return path


def test_team_pool_absent_detected_from_full_enumeration(tmp_path):
    stream = _stream_with_search(tmp_path / "s.jsonl", ".", POOL_WITHOUT_AGENT)
    assert hx.team_pool_absent(stream) is True


def test_team_pool_present_is_not_absent(tmp_path):
    stream = _stream_with_search(tmp_path / "s.jsonl", ".", POOL_WITH_AGENT)
    assert hx.team_pool_absent(stream) is False


def test_regex_search_misses_do_not_prove_absence(tmp_path):
    stream = _stream_with_search(tmp_path / "s.jsonl", "teammate|team|agent", "No matching deferred tools found")
    assert hx.team_pool_absent(stream) is False


def test_text_embedded_pool_json_still_parses(tmp_path):
    stream = _stream_with_search(tmp_path / "s.jsonl", ".", json.dumps(POOL_WITHOUT_AGENT))
    assert hx.team_pool_absent(stream) is True


def test_team_env_fault_needs_marker_team_and_proof(tmp_path):
    unit = {"unit": "N5::baseline::r02", "scenario": "N5", "arm": "baseline", "repeat": 2}
    work = tmp_path / "work"
    stream = work / "units" / "N5_baseline_r02" / "stream.jsonl"
    stream.parent.mkdir(parents=True)
    _stream_with_search(stream, ".", POOL_WITHOUT_AGENT)
    faulted = {"notes": "setup:team-not-ready; exit:-1", "team_source": None}
    assert hx._team_env_fault(faulted, unit, work) is True
    assert hx._team_env_fault({"notes": "host timeout after 5s", "team_source": None}, unit, work) is False
    assert hx._team_env_fault({"notes": faulted["notes"], "team_source": "host"}, unit, work) is False
    clean_pool = _stream_with_search(stream, ".", POOL_WITH_AGENT)
    assert clean_pool
    assert hx._team_env_fault(faulted, unit, work) is False


ENV_FAULT_SETUP_EVENTS = [
    {"emit_tool": {"name": "ToolSearch", "input": {"query": "."}}},
    {"emit_tool_result_json": POOL_WITHOUT_AGENT},
]
HAPPY_SETUP_EVENTS = [
    {"emit_tool": {"name": "Task", "input": {"description": "创建队友 impl-1"}}},
    {"emit_tool_result": "impl-1 running (teammate)"},
    {"emit_tool": {"name": "Task", "input": {"description": "创建队友 impl-2"}}},
    {"emit_tool_result": "impl-2 running (teammate)"},
    {"emit_tool": {"name": "Task", "input": {"description": "创建队友 impl-3"}}},
    {"emit_tool_result": "impl-3 running (teammate)"},
]
BUSINESS_EVENTS = [{"emit_tool": {"name": "Bash", "input": {"command": "echo typo-fixed"}}}]


def test_env_retry_recovers_team_session_and_charges_both_attempts(tmp_path):
    plan = {
        "version": "stub-2.1.296",
        "model": "stub-model",
        "sessions": [
            {
                "spawn": 0,
                "match": "组队准备",
                "events": ENV_FAULT_SETUP_EVENTS,
                "usage": {"input_tokens": 100, "output_tokens": 10},
            },
            {
                "spawn": 1,
                "match": "组队准备",
                "events": HAPPY_SETUP_EVENTS,
                "usage": {"input_tokens": 500, "output_tokens": 50},
            },
            {"match": "typo", "events": BUSINESS_EVENTS, "usage": {"input_tokens": 200, "output_tokens": 20}},
        ],
    }
    row = run_stub_unit(tmp_path, "N5", plan, "baseline", env_retry=True)
    assert row["team_source"] == "host"
    assert row["team_env_retried"] == 1
    assert "team-env-retry:1" in row["notes"]
    assert "setup:team-not-ready" not in row["notes"]
    # both attempts' usage folded into the recorded row
    assert row["tokens_in"] >= 700
    assert row["tokens_out"] >= 80
    # attempt 1 preserved verbatim with its proving enumeration
    preserved = tmp_path / "work" / "units-env-retries" / "N5_baseline_r01.attempt1" / "stream.jsonl"
    assert preserved.is_file()
    assert '"query": "."' in preserved.read_text(encoding="utf-8")
    assert "No matching deferred tools found" not in preserved.read_text(encoding="utf-8")
    # the live unit dir holds only attempt 2's stream
    live = tmp_path / "work" / "units" / "N5_baseline_r01" / "stream.jsonl"
    assert live.read_text(encoding="utf-8").count('"type": "result"') >= 2


def test_env_retry_bounded_once_when_second_attempt_also_lacks_pool(tmp_path):
    plan = {
        "version": "stub-2.1.296",
        "model": "stub-model",
        "sessions": [
            {
                "spawn": 0,
                "match": "组队准备",
                "events": ENV_FAULT_SETUP_EVENTS,
                "usage": {"input_tokens": 100, "output_tokens": 10},
            },
            {
                "spawn": 1,
                "match": "组队准备",
                "events": ENV_FAULT_SETUP_EVENTS,
                "usage": {"input_tokens": 100, "output_tokens": 10},
            },
        ],
    }
    row = run_stub_unit(tmp_path, "N5", plan, "baseline", env_retry=True)
    assert "setup:team-not-ready" in row["notes"]
    assert row["team_env_retried"] == 1
    assert row["usage_unknown"] is True
    spawns = (tmp_path / "stubbin" / "stub-plan.json.spawns").read_text(encoding="utf-8")
    assert spawns.strip() == "2"


def test_no_retry_when_team_failure_lacks_enumeration_proof(tmp_path):
    plan = {
        "version": "stub-2.1.296",
        "model": "stub-model",
        "sessions": [
            {
                "spawn": 0,
                "match": "组队准备",
                "events": [
                    {"emit_tool": {"name": "ToolSearch", "input": {"query": "teammate|team"}}},
                    {"emit_tool_result": "No matching deferred tools found"},
                ],
                "usage": {"input_tokens": 50, "output_tokens": 5},
            },
        ],
    }
    row = run_stub_unit(tmp_path, "N5", plan, "baseline", env_retry=True)
    assert "setup:team-not-ready" in row["notes"]
    assert "team_env_retried" not in row
    spawns = (tmp_path / "stubbin" / "stub-plan.json.spawns").read_text(encoding="utf-8")
    assert spawns.strip() == "1"
    assert not (tmp_path / "work" / "units-env-retries").exists()


def test_non_team_unit_is_never_env_retried(tmp_path):
    plan = {
        "version": "stub-2.1.296",
        "model": "stub-model",
        "hang": True,
        "sessions": [],
    }
    row = run_stub_unit(tmp_path, "N4", plan, "baseline", timeout=6, env_retry=True)
    assert "host timeout" in row["notes"]
    assert "team_env_retried" not in row
    spawns = (tmp_path / "stubbin" / "stub-plan.json.spawns").read_text(encoding="utf-8")
    assert spawns.strip() == "1"


# --- self-terminated sessions certify their own usage (sweep-10 P1 r2 signature) --------


def test_max_turns_self_exit_with_usage_is_not_usage_unknown(tmp_path):
    plan = {
        "version": "stub-2.1.296",
        "model": "stub-model",
        "exit_code": 1,
        "sessions": [
            {
                "match": "把用户列表",
                "result_subtype": "error_max_turns",
                "result_is_error": True,
                "events": [{"emit_tool": {"name": "Skill", "input": {"skill": "spec"}}}],
                "usage": {"input_tokens": 61118, "output_tokens": 22425},
            },
        ],
    }
    row = run_stub_unit(tmp_path, "P1", plan, "candidate")
    assert row["usage_unknown"] is False
    assert row["host_error"] is True  # the result's own is_error is preserved honestly
    assert row["tokens_in"] == 61118
    assert row["tokens_out"] == 22425


def test_timeout_kill_with_partial_setup_result_stays_usage_unknown(tmp_path):
    plan = {
        "version": "stub-2.1.296",
        "model": "stub-model",
        "sessions": [
            {
                "match": "组队准备",
                "events": [
                    {"emit_tool": {"name": "Task", "input": {"description": "创建队友 impl-1"}}},
                    {"emit_tool_result": "impl-1 running (teammate)"},
                    {"emit_tool": {"name": "Task", "input": {"description": "创建队友 impl-2"}}},
                    {"emit_tool_result": "impl-2 running (teammate)"},
                    {"emit_tool": {"name": "Task", "input": {"description": "创建队友 impl-3"}}},
                    {"emit_tool_result": "impl-3 running (teammate)"},
                ],
                "usage": {"input_tokens": 900, "output_tokens": 90},
            },
            {"match": "typo", "events": [{"sleep": 600}], "usage": {"input_tokens": 1, "output_tokens": 1}},
        ],
    }
    row = run_stub_unit(tmp_path, "N5", plan, "baseline", timeout=20)
    assert "host timeout after" in row["notes"]
    # the setup turn's result (with usage) must NOT certify the killed unit
    assert row["usage_unknown"] is True


def test_setup_prompt_makes_team_discovery_deterministic():
    """Setup tries name-addressed select: first (index-independent), enumerates
    only as final adjudication, and reports absence with the fixed phrase."""
    prompt = hx.TEAM_SETUP_PROMPT
    assert "select:Agent" in prompt and "select:Task" in prompt
    assert "query '.'" in prompt
    assert "本会话无 teammate 工具" in prompt
    # the setup prompt stays harness scaffolding: no discipline vocabulary leaks
    assert not hx.DISCIPLINE_RE.search(prompt)


def test_parallel_teammate_launches_pair_by_id_not_position():
    """sweep-10 N5 candidate r2: use,use,result,result broke positional pairing
    and a healthy parallel setup was killed as team-not-ready."""
    stream = [
        {"kind": "tool_use", "tool": "Agent", "input": {"name": "impl-1"}, "id": "u1"},
        {"kind": "tool_use", "tool": "Agent", "input": {"name": "impl-2"}, "id": "u2"},
        {
            "kind": "tool_result",
            "content": [{"type": "text", "text": "Async agent launched successfully."}],
            "tool_use_id": "u1",
        },
        {
            "kind": "tool_result",
            "content": [{"type": "text", "text": "Async agent launched successfully."}],
            "tool_use_id": "u2",
        },
        {"kind": "tool_use", "tool": "Agent", "input": {"name": "impl-3"}, "id": "u3"},
        {"kind": "tool_result", "content": [{"type": "text", "text": "launched"}], "tool_use_id": "u3"},
    ]
    assert hx.accepted_team_names(stream) == {"impl-1", "impl-2", "impl-3"}


def test_id_less_streams_keep_positional_fallback():
    stream = [
        {"kind": "tool_use", "tool": "Agent", "input": {"description": "创建队友 impl-1"}},
        {"kind": "tool_result", "content": [{"type": "text", "text": "impl-1 running (teammate)"}]},
    ]
    assert hx.accepted_team_names(stream) == {"impl-1"}
