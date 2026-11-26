"""Spec arm install contract for the bench harness (task-spec-arm).

Stub host only — zero model spend. A stub ``claude`` binary writes a JSON
probe of what a measured session can see (skills dir, settings hooks, HOME,
PYTHONPATH) and emits a valid headless result payload, so the adapter
completes without ever calling a model.

Covered:

- the ``spec`` arm installs the current tree into the unit HOME via
  install.sh; the skill, stage commands and session hooks land inside the
  sandbox and every rendered hook command points at the sandbox install;
- no arm inherits the operator's session hooks, even when the operator
  settings source declares them;
- the spec prompt differs from native only by the arm block;
- an install failure stops the adapter before the stub host ever runs;
- the ``real-value-baseline`` authorization label covers the 12-unit
  reduced matrix and stays inside the default class batch caps;
- install-record extras pass from ``.bench-usage.json`` into runs.jsonl.

install.sh takes real seconds, so the spec-arm install is shared through a
module-scoped fixture and the number of adapter runs is kept minimal.
"""

from __future__ import annotations

import importlib
import json
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = REPO_ROOT / "bench"
SCORE_SOURCE = BENCH_DIR / "score.py"

pytestmark = pytest.mark.skipif(
    not SCORE_SOURCE.is_file(),
    reason="benchmark development tooling is absent in the public snapshot",
)


@pytest.fixture(scope="module")
def load_benchmark_modules():
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.syspath_prepend(str(BENCH_DIR))
        monkeypatch.syspath_prepend(str(BENCH_DIR / "hosts"))
        yield importlib.import_module("claude_host"), importlib.import_module("harness")


SCENARIOS_DIR = BENCH_DIR / "scenarios"
PROMPT_ONLY_ARMS = ("native", "todo", "core", "cprime", "o")

#: The probe payload the stub claude writes next to the result JSON: what a
#: measured session sees without ever calling a model.
STUB_CLAUDE_BODY = """
import json
import os
import sys

if "--version" in sys.argv:
    print("stub-claude 1.0 (test)")
    raise SystemExit(0)

home = os.environ["HOME"]
config_dir = os.environ["CLAUDE_CONFIG_DIR"]
settings_path = os.path.join(config_dir, "settings.json")
settings = {}
if os.path.isfile(settings_path):
    settings = json.load(open(settings_path, encoding="utf-8"))
hook_commands = []
for entries in (settings.get("hooks") or {}).values():
    for entry in entries:
        for hook in entry.get("hooks", [entry]):
            if isinstance(hook, dict) and hook.get("command"):
                hook_commands.append(hook["command"])
skills_root = os.path.join(home, ".claude", "skills")
probe = {
    "home": home,
    "cwd": os.getcwd(),
    "config_dir": config_dir,
    "pythonpath": os.environ.get("PYTHONPATH", ""),
    "skills": sorted(os.listdir(skills_root)) if os.path.isdir(skills_root) else [],
    "settings_has_hooks": bool(settings.get("hooks")),
    "hook_commands": hook_commands,
    "commands_dir": sorted(os.listdir(os.path.join(home, ".claude", "commands", "spec")))
    if os.path.isdir(os.path.join(home, ".claude", "commands", "spec"))
    else [],
}
with open(os.path.join(os.environ["TMPDIR"], "probe.json"), "w", encoding="utf-8") as handle:
    json.dump(probe, handle)

print(json.dumps({
    "type": "result",
    "is_error": False,
    "subtype": "success",
    "session_id": "stub-session",
    "num_turns": 2,
    "usage": {
        "input_tokens": 11,
        "output_tokens": 7,
        "cache_read_input_tokens": 3,
        "cache_creation_input_tokens": 5,
    },
    "result": "stub done",
}))
"""


def _write_stub(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stub = directory / "stub-claude"
    stub.write_text("#!/usr/bin/env python3\n" + STUB_CLAUDE_BODY, encoding="utf-8")
    stub.chmod(0o755)
    return stub


def _operator_settings(directory: Path) -> Path:
    """Operator settings fixture carrying hooks, so stripping is observable."""
    source = directory / "operator-settings.json"
    source.write_text(
        json.dumps(
            {
                "env": {"ANTHROPIC_BASE_URL": "https://stub.invalid", "ANTHROPIC_MODEL": "stub"},
                "hooks": {
                    "SessionStart": [{"matcher": "*", "hooks": [{"type": "command", "command": "operator-hook"}]}]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return source


def _run_arm(claude_host, sandbox, arm: str, *, monkeypatch, stub: Path, settings: Path) -> dict:
    monkeypatch.setenv("BENCH_CLAUDE_BIN", str(stub))
    monkeypatch.setenv("BENCH_CLAUDE_CONFIG_SOURCE", str(settings))
    exit_code = claude_host.main(
        ["--sandbox", str(sandbox.repo), "--scenario", "s1-simple-fix", "--arm", arm, "--timeout", "60"]
    )
    assert exit_code == 0
    return json.loads((sandbox.repo / ".bench-usage.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def stub_bin(tmp_path_factory):
    return _write_stub(tmp_path_factory.mktemp("stub-bin"))


@pytest.fixture(scope="module")
def spec_unit(tmp_path_factory, load_benchmark_modules):
    """One spec-arm unit with a real install.sh into the sandbox HOME.

    The install takes real seconds; every spec-arm assertion shares this
    single run so the suite pays it once.
    """
    started = time.monotonic()
    work_root = tmp_path_factory.mktemp("spec-unit")
    claude_host, bench_harness = load_benchmark_modules
    scenario = bench_harness.discover_scenario(["s1-simple-fix"], SCENARIOS_DIR)[0]
    sandbox = bench_harness.build_sandbox(scenario, work_root, bench_harness.Unit("s1-simple-fix", "spec", 1))
    stub = _write_stub(work_root / "bin")
    settings = _operator_settings(work_root)
    monkey = pytest.MonkeyPatch()
    monkey.setenv("BENCH_CLAUDE_BIN", str(stub))
    monkey.setenv("BENCH_CLAUDE_CONFIG_SOURCE", str(settings))
    try:
        exit_code = claude_host.main(
            ["--sandbox", str(sandbox.repo), "--scenario", "s1-simple-fix", "--arm", "spec", "--timeout", "60"]
        )
    finally:
        monkey.undo()
    assert exit_code == 0
    probe = json.loads((sandbox.tmp / "probe.json").read_text(encoding="utf-8"))
    usage = json.loads((sandbox.repo / ".bench-usage.json").read_text(encoding="utf-8"))
    return {
        "sandbox": sandbox,
        "probe": probe,
        "usage": usage,
        "install_seconds": time.monotonic() - started,
    }


def test_spec_arm_installs_skill_hooks_and_commands_inside_unit(spec_unit, load_benchmark_modules):
    claude_host, _ = load_benchmark_modules
    sandbox, probe = spec_unit["sandbox"], spec_unit["probe"]
    unit_root = str(sandbox.root.resolve())
    home = sandbox.home.resolve()

    # Skill + stage commands live under the unit HOME, not the operator's.
    assert "spec" in probe["skills"]
    assert (home / ".claude" / "skills" / "spec" / "SKILL.md").is_file()
    assert {"plan.md", "run.md", "check.md"} <= set(probe["commands_dir"])
    assert probe["home"] == str(sandbox.home)
    # CLAUDE_CONFIG_DIR is the install target so the host loads the hooks.
    assert probe["config_dir"] == str(home / ".claude")

    # Session hooks are installed and every rendered command stays inside the
    # unit — never the operator home or the primary checkout.
    assert probe["settings_has_hooks"] is True
    commands = probe["hook_commands"]
    assert commands and "spec-hook.sh" in " ".join(commands)
    for command in commands:
        assert command.startswith("sh " + str(home / ".claude" / "skills" / "spec")), command
    operator_home = str(Path(claude_host.real_settings_source()).parent)
    for command in commands:
        assert str(REPO_ROOT) not in command, command
        if operator_home != unit_root:
            assert operator_home not in command, command

    # Prompt carries the spec discipline block and usage records the install.
    task = (SCENARIOS_DIR / "s1-simple-fix" / "task.md").read_text(encoding="utf-8")
    expected_prompt = claude_host.build_prompt("spec", task)
    usage = spec_unit["usage"]
    assert usage["prompt_sha256"] == __import__("hashlib").sha256(expected_prompt.encode()).hexdigest()
    assert usage["installed"] is True
    assert usage["hooks_installed"] is True
    assert usage["install_source_head"] or usage["install_tree_fp"]
    assert usage["usage_unknown"] is False
    print(f"\nspec-arm install.sh duration: {spec_unit['install_seconds']:.1f}s")


def test_spec_prompt_differs_from_native_only_by_arm_block(load_benchmark_modules):
    claude_host, _ = load_benchmark_modules
    task = (SCENARIOS_DIR / "s1-simple-fix" / "task.md").read_text(encoding="utf-8")
    native = claude_host.build_prompt("native", task)
    spec = claude_host.build_prompt("spec", task)
    block = claude_host.arm_block("spec")
    assert block and "spec" in block.lower()
    # Identical bytes except the inserted discipline block.
    assert spec == native.replace(
        claude_host.PROMPT_WRAPPER + "\n\n",
        claude_host.PROMPT_WRAPPER + "\n\n" + block + "\n\n",
        1,
    )
    assert "spec" in claude_host.REAL_ARMS


@pytest.mark.parametrize("arm", PROMPT_ONLY_ARMS)
def test_prompt_only_arms_inherit_no_hooks_and_no_spec_skill(
    tmp_path, monkeypatch, stub_bin, arm, load_benchmark_modules
):
    claude_host, bench_harness = load_benchmark_modules
    settings = _operator_settings(tmp_path)
    scenario = bench_harness.discover_scenario(["s1-simple-fix"], SCENARIOS_DIR)[0]
    sandbox = bench_harness.build_sandbox(scenario, tmp_path, bench_harness.Unit("s1-simple-fix", arm, 1))
    _run_arm(claude_host, sandbox, arm, monkeypatch=monkeypatch, stub=stub_bin, settings=settings)
    probe = json.loads((sandbox.tmp / "probe.json").read_text(encoding="utf-8"))

    # The operator settings source declares hooks; the arm's copy must not.
    assert json.loads(settings.read_text(encoding="utf-8"))["hooks"]
    assert probe["settings_has_hooks"] is False
    assert probe["hook_commands"] == []
    assert "spec" not in probe["skills"]
    assert probe["commands_dir"] == []
    assert not (sandbox.home / ".claude" / "skills").exists()


def test_sandbox_pythonpath_cannot_import_primary_checkout(spec_unit):
    entries = [entry for entry in spec_unit["probe"]["pythonpath"].split(":") if entry]
    for entry in entries:
        resolved = Path(entry).resolve()
        assert resolved != REPO_ROOT and REPO_ROOT not in resolved.parents, entry


def test_install_failure_exits_before_host_and_claims_no_spend(tmp_path, monkeypatch, stub_bin, load_benchmark_modules):
    claude_host, bench_harness = load_benchmark_modules
    monkeypatch.setenv("BENCH_CLAUDE_BIN", str(stub_bin))
    monkeypatch.setenv("BENCH_CLAUDE_CONFIG_SOURCE", str(_operator_settings(tmp_path)))
    # Documented test-only override: point the install source at a tree
    # without install.sh so the spec-arm install fails deterministically.
    empty_source = tmp_path / "not-a-spec-tree"
    empty_source.mkdir()
    monkeypatch.setenv("BENCH_SPEC_INSTALL_SOURCE", str(empty_source))
    scenario = bench_harness.discover_scenario(["s1-simple-fix"], SCENARIOS_DIR)[0]
    sandbox = bench_harness.build_sandbox(scenario, tmp_path, bench_harness.Unit("s1-simple-fix", "spec", 1))

    exit_code = claude_host.main(
        ["--sandbox", str(sandbox.repo), "--scenario", "s1-simple-fix", "--arm", "spec", "--timeout", "60"]
    )

    assert exit_code != 0
    assert not (sandbox.tmp / "probe.json").exists()  # stub host never launched
    usage_path = sandbox.repo / ".bench-usage.json"
    if usage_path.exists():
        usage = json.loads(usage_path.read_text(encoding="utf-8"))
        assert usage.get("usage_unknown") is not False or not any(
            key in usage for key in ("tokens_in", "tokens_out", "tokens_cache", "tokens_cache_creation")
        )
        assert usage.get("installed") is not True
        assert "install" in usage.get("host_stderr_tail", "").lower() or usage.get("installed") is False


def test_real_value_baseline_label_covers_reduced_matrix(load_benchmark_modules):
    _, bench_harness = load_benchmark_modules
    budgets = bench_harness.load_budgets(BENCH_DIR / "budgets.json")
    label = "real-value-baseline"
    assert bench_harness.authorize_budget_label(budgets, label) == label
    entry = budgets["authorization_labels"][label]
    assert entry["authorized"] is True
    assert "user 2026-11-25" in entry["authorized_by"]

    scenarios = bench_harness.discover_scenario(
        ["s1-simple-fix", "s4-interrupt-recovery", "s7-backend-unavailable"], SCENARIOS_DIR
    )
    units = bench_harness.build_matrix(scenarios, ["native", "spec"], 2)
    assert len(units) == 12
    bench_harness.validate_authorized_units(budgets, label, units)

    for bad in (
        bench_harness.Unit("s1-simple-fix", "cprime", 1),
        bench_harness.Unit("s2-multi-file-feature", "spec", 1),
        bench_harness.Unit("s1-simple-fix", "spec", 3),
    ):
        with pytest.raises(SystemExit):
            bench_harness.validate_authorized_units(budgets, label, [bad])

    caps = entry["estimate_upper_bound"]
    batch = budgets["classes"]["default"]["batch"]
    for field in bench_harness.BATCH_CAP_FIELDS:
        assert caps[field] <= batch[field], field


def test_install_extras_pass_through_to_runs_record(tmp_path, load_benchmark_modules):
    _, bench_harness = load_benchmark_modules
    """harness.run_worker forwards adapter-declared scalar extras to the row."""
    stub = tmp_path / "recording_host.py"
    usage_payload = {
        "tokens_in": 10,
        "tokens_out": 10,
        "tokens_cache": 0,
        "tokens_cache_creation": 0,
        "tool_calls": 1,
        "human_interventions": 0,
        "usage_unknown": False,
        "installed": True,
        "hooks_installed": True,
        "install_source_head": "a" * 40,
        "install_tree_fp": "b" * 64,
    }
    stub.write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "sandbox = Path(sys.argv[sys.argv.index('--sandbox') + 1])\n"
        f"(sandbox / '.bench-usage.json').write_text(json.dumps({usage_payload!r}))\n",
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    sandbox = bench_harness.Sandbox(repo=repo, home=tmp_path, tmp=tmp_path, remote=tmp_path, root=tmp_path)
    outcome = bench_harness.run_worker(
        bench_harness.Unit("s1-simple-fix", "spec", 1),
        sandbox,
        tmp_path,
        60,
        {},
        host_script=stub,
    )
    assert outcome["installed"] is True
    assert outcome["hooks_installed"] is True
    assert outcome["install_source_head"] == "a" * 40
    assert outcome["install_tree_fp"] == "b" * 64
