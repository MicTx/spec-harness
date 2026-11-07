"""Runtime instructions and their shipped examples match executable contracts."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from spec_package_support import autonomous_question_contract

from tests.test_autonomous_questions import TASKS, make_spec

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "relative",
    [
        "SKILL.md",
        "references/stages/new.md",
        "references/stages/run.md",
        "references/stages/autoplan.md",
        "references/stages/autorun.md",
        "agents/planner.md",
        "agents/orchestrator.md",
        "agents/reviewer.md",
    ],
)
def test_execution_surfaces_carry_reasoning_and_evidence_inquiry(relative):
    text = (ROOT / relative).read_text(encoding="utf-8").lower()
    assert "mandatory ultrathink" in text
    assert "evidence" in text or "证据" in text
    assert "decision" in text or "[decision]" in text


def test_planning_investigates_before_asking_and_does_not_require_rounds():
    text = (ROOT / "references/stages/autoplan.md").read_text(encoding="utf-8")
    assert "not a questionnaire" in text
    assert "proceed without confirmation" in text
    assert "Ask themed rounds" not in text
    assert "../templates.md#autonomous-inquiry" in text
    templates = (ROOT / "references/templates.md").read_text(encoding="utf-8")
    inquiry = templates.split("### Autonomous inquiry\n", 1)[1].split("## `tasks.md`", 1)[0]
    assert "Classify legacy questions before dispatch." in inquiry
    for reason in ("permission", "irreversibility", "preference", "external-dependency"):
        assert reason in text


def test_documented_question_examples_parse_and_retain_human_authority():
    text = (ROOT / "references/templates.md").read_text(encoding="utf-8")
    section = text.split("### Autonomous inquiry\n", 1)[1].split("## `tasks.md`", 1)[0]
    examples = re.findall(r"```text\n(.*?)\n```", section, re.S)
    assert len(examples) == 2
    tasks = TASKS.replace("task-inspect", "task-format") + TASKS.replace("task-inspect", "task-parser")
    for example in examples:
        closed = "[x]" in example
        current_tasks = tasks.replace("[ ]", "[x]") if closed else tasks
        spec = make_spec("placeholder").replace("- placeholder", example)
        questions, human, errors = autonomous_question_contract(spec, current_tasks)
        assert not errors
        assert {question.kind for question in questions} == {"fact", "reversible"}
        assert len(human) == (0 if closed else 1)


@pytest.mark.parametrize("profile", ["core", "full"])
def test_exported_policy_and_host_audit_remain_honest(tmp_path, profile):
    output = tmp_path / "skill"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(ROOT / "scripts/export_skill_package.py"),
            "--root",
            str(ROOT),
            "--output",
            str(output),
            "--skip-signing",
            "--profile",
            profile,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    policy = output / "scripts/reasoning_policy.py"
    assert policy.exists() is (profile == "full")
    assert (output / "scripts/chain_spawn_support.py").exists() is (profile == "full")
    assert (output / "scripts/worker_decision_guard.py").is_file()
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
            "import spec_verify,route_spec_package,check_spec_package; "
            "from worker_decision_guard import DecisionGuard,decision_guard_errors; "
            "guard=DecisionGuard(Path.cwd()); guard.finish(); "
            "assert not decision_guard_errors(Path.cwd())",
            str(output / "scripts"),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    commands = (output / "references/commands.md").read_text(encoding="utf-8")
    assert "provider_effect=unverified" in commands
    assert "instruction_only" in commands
    if profile == "full":
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                "import json,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                "from chain_spawn_support import build_worker_command; "
                "print(json.dumps(build_worker_command('pi',Path.cwd(),'work')))",
                str(output / "scripts"),
            ],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        argv = json.loads(result.stdout)
        assert argv[argv.index("--thinking") + 1] == "max"


@pytest.mark.parametrize("relative", ["README.md", "README-en.md", "docs/README.md", "docs/README-en.md"])
def test_public_guidance_links_single_contracts(relative):
    text = (ROOT / relative).read_text(encoding="utf-8")
    assert "references/commands.md#mandatory-ultrathink" in text
    assert "references/templates.md#autonomous-inquiry" in text
