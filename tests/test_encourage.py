"""Pin the Spark encouragement channel across the full spec lifecycle.

Every lifecycle surface that prompts the driving agent — next cards, session
briefs, Stop-hook messages, verify directives, contract docs, and the host
listing — must carry an encouraging line that invites divergent thinking,
while every pinned prefix and substring the suite relies on stays intact.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from tests.v3_support import SLUG, cli, make_package, repo, started_package

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
HOOKS = ROOT / "hooks"
for directory in (SCRIPTS, HOOKS):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import evidence  # noqa: E402
import route  # noqa: E402
from spec_hook import handle  # noqa: E402


def _card(root: Path) -> dict:
    result = cli(root, "next")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _runs(package: Path) -> list[dict]:
    return [
        json.loads(line) for line in (package / "runs.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()
    ]


# --- route: every lifecycle state carries a Spark line ---


def test_route_plan_card_encourages_divergence(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)

    card = _card(tmp_path)

    assert card["state"] == "plan"
    assert "plan-lint" in card["next"]
    assert route.SPARK_MARKER in card["next"]


def test_route_run_card_encourages_creativity_and_keeps_task_first(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)

    card = _card(tmp_path)

    assert card["state"] == "run"
    assert card["next"].startswith("task-value:")
    assert route.SPARK_MARKER in card["next"]


def test_route_deliver_card_celebrates(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0

    card = _card(tmp_path)

    assert card["state"] == "deliver"
    assert "spec.py check" in card["next"]
    assert route.SPARK_MARKER in card["next"]


def test_route_check_failed_card_pairs_repair_with_spark(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    (tmp_path / "other.txt").write_text("outside the task boundary\n", encoding="utf-8")
    assert cli(tmp_path, "check", "--check-only").returncode == 1

    card = _card(tmp_path)

    assert card["state"] == "check-failed"
    assert "Move the change inside the owning task boundary" in card["next"]
    assert route.SPARK_MARKER in card["next"]


def test_route_stalled_card_invites_a_new_angle(tmp_path):
    repo(tmp_path)
    package = started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    runs = _runs(package)
    latest = next(record for record in reversed(runs) if record.get("kind") == "run")
    latest["exit"] = 1
    (package / "runs.jsonl").write_text(
        "\n".join(json.dumps(record, sort_keys=True) for record in runs) + "\n", encoding="utf-8"
    )

    card = _card(tmp_path)

    assert card["state"] == "stalled"
    assert "blocked without new evidence" in card["next"]
    assert route.SPARK_MARKER in card["next"]


def test_route_parked_only_card_keeps_seeds(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    tasks = package / "tasks.md"
    tasks.write_text(tasks.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    assert cli(tmp_path, "plan-go").returncode == 0
    assert cli(tmp_path, "decide", "Publish?", "--park", "--by", "user pending").returncode == 0

    card = _card(tmp_path)

    assert card["state"] == "parked-only"
    assert route.SPARK_MARKER in card["next"]


def test_route_archived_push_card_encourages(tmp_path):
    repo(tmp_path)
    archive = tmp_path / ".spec" / "specs" / "archive" / SLUG
    archive.mkdir(parents=True)
    (archive / "spec.md").write_text(
        "# Fixture\n<!-- spec-format: 3 -->\n## Charter\n- V4 finish: push | charter.md\n"
        f"## Binding\n- branch: `spec/{SLUG}`\n",
        encoding="utf-8",
    )
    (archive / "tasks.md").write_text("", encoding="utf-8")

    card = route.archived_card(tmp_path)

    assert card and card["state"] == "push"
    assert "spec.py push" in card["next"]
    assert route.SPARK_MARKER in card["next"]


def test_route_completion_cards_carry_spark():
    pending = route.completion_card(SLUG, "push", push_failed=True)
    delivered = route.completion_card(SLUG, "commit")

    assert pending["state"] == "push" and route.SPARK_MARKER in pending["next"]
    assert "Delivery complete" in delivered["next"]
    assert route.SPARK_MARKER in delivered["next"]


def test_route_brief_keeps_spark_within_budget_for_long_goals(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "- goal: Change the fixture value", "- goal: " + "Divergent design thinking " * 40
        ),
        encoding="utf-8",
    )
    assert cli(tmp_path, "plan-go").returncode == 0

    brief = route.brief(tmp_path)

    assert len(brief) <= 1450
    assert route.SPARK_MARKER in brief


# --- hook: stop messages and directives encourage divergence ---


def test_hook_stop_reasons_stay_pinned_and_inviting(tmp_path):
    repo(tmp_path)
    started_package(tmp_path, verify="test ! -e fail-marker")
    (tmp_path / "fail-marker").write_text("fail\n", encoding="utf-8")
    assert cli(tmp_path, "verify", "task-value").returncode == 1

    first = handle("stop", {"cwd": str(tmp_path)})
    second = handle("stop", {"cwd": str(tmp_path)})
    third = handle("stop", {"cwd": str(tmp_path)})

    assert first.block and first.reason.startswith("task-value:")
    assert route.SPARK_MARKER in first.reason
    assert second.block
    assert "No progress" in second.reason
    assert "change strategy using the latest failure signature" in second.reason
    assert "great moment to diverge" in second.reason
    assert not third.block
    assert third.context.startswith("stalled:")
    assert "a fresh angle" in third.context


def test_hook_deliver_stop_celebrates(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0

    action = handle("stop", {"cwd": str(tmp_path)})

    assert action.block
    assert action.reason.startswith("All work is verified")
    assert "earned the gate" in action.reason


def test_hook_ladder_directives_stay_kind_and_discriminating(tmp_path):
    repo(tmp_path)
    package = started_package(tmp_path, verify="test ! -e fail-marker")
    (tmp_path / "fail-marker").write_text("fail\n", encoding="utf-8")
    assert cli(tmp_path, "verify", "task-value").returncode == 1
    failure = next(record for record in _runs(package) if record.get("kind") == "run")
    failure["attempt"] = 0

    def matching(count: int) -> None:
        (package / "runs.jsonl").write_text(
            "\n".join(json.dumps({**failure, "attempt": index}, sort_keys=True) for index in range(count)) + "\n",
            encoding="utf-8",
        )

    matching(0)
    first = evidence.ladder_directive(package, "task-value", failure["sig"])
    assert "smallest fix" in first
    assert "Fresh evidence" in first

    matching(1)
    second = evidence.ladder_directive(package, "task-value", failure["sig"])
    assert "minimal reproduction" in second
    assert "diverge" in second

    matching(2)
    third = evidence.ladder_directive(package, "task-value", failure["sig"])
    assert "change implementation path" in third
    assert "bold" in third

    matching(3)
    fourth = evidence.ladder_directive(package, "task-value", failure["sig"])
    assert "another ready task" in fourth
    assert "creative energy" in fourth


def test_hook_learn_directive_celebrates_the_hard_won_green(tmp_path):
    repo(tmp_path)
    started_package(tmp_path, verify="test ! -e fail-marker")
    (tmp_path / "fail-marker").write_text("fail\n", encoding="utf-8")
    assert cli(tmp_path, "verify", "task-value").returncode == 1
    (tmp_path / "fail-marker").unlink()
    result = cli(tmp_path, "verify", "task-value")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    directive = payload["results"][0]["directive"]
    assert "spec.py learn" in directive
    assert "Hard-won green" in directive
    assert "reusable lesson" in directive


def test_hook_check_only_pass_message_encourages(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0

    result = cli(tmp_path, "check", "--check-only")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "Delivery gate passed" in payload["next"]["next"]
    assert "run spec.py check to archive and deliver" in payload["next"]["next"]
    assert route.SPARK_MARKER in payload["next"]["next"]


# --- docs: the contract layer declares the stance ---


def test_docs_skill_declares_the_innovation_stance():
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "## Innovation Stance" in skill
    assert route.SPARK_MARKER in skill
    assert "never overrides" in skill


def test_docs_plan_contract_invites_divergence():
    plan = (ROOT / "references/plan.md").read_text(encoding="utf-8")

    assert "rival designs" in plan
    assert "reason backward from failure" in plan
    assert "never reopen planning" in plan
    assert "product files stay untouched" in plan


def test_docs_exec_ladder_is_an_invitation():
    contract = (ROOT / "references/exec.md").read_text(encoding="utf-8")

    assert "invitation to diverge" in contract
    assert "smallest fix" in contract
    assert "minimal reproduction" in contract


def test_docs_philosophy_records_the_rule():
    philosophy = (ROOT / "references/philosophy.md").read_text(encoding="utf-8")

    assert "encouraged divergence" in philosophy


# --- surface: listing and user-facing pages carry the channel ---


def test_surface_agent_starter_prompt_is_encouraging():
    text = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
    match = re.search(r'default_prompt:\s*"(.+)"', text)
    assert match, "default_prompt must stay a one-line starter"
    prompt = match.group(1)

    assert "$spec" in prompt
    assert "explore boldly" in prompt


def test_surface_user_pages_and_changelog_mention_the_channel():
    for name in ("README.md", "README-en.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert route.SPARK_MARKER in text, name
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert route.SPARK_MARKER in changelog.split("## [1.0.8]")[0]
