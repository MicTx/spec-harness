"""Evidence-driven Stop: real progress continues without a round quota and replans from the frozen goal,
task criteria and actual evidence; stale attempts, replays, blocked work and idle turns start no turn."""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.v3_support import SCRIPTS, SLUG, cli, make_package, repo, started_package

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))

import spec_hook  # noqa: E402

COUNTER = "n=$(cat .git/n 2>/dev/null || echo 0); echo $((n+1)) > .git/n"
NOISY = (
    "test ! -e fail-marker || { "
    + COUNTER
    + '; echo "FAILED t.py::a - assert 1 == 2 at 0x7f00000$n"; '
    + 'echo "1 failed in 0.0${n}s /tmp/pytest-of-u/pytest-$n/t0 12:00:0$n" >&2; exit 1; }'
)
DISTINCT = "test ! -e fail-marker || { " + COUNTER + '; echo "AssertionError: case $n diverged"; exit 1; }'
# The diagnostic is a pure function of the candidate, so a returning candidate reproduces its failure.
BY_CANDIDATE = 'test ! -e fail-marker || { echo "AssertionError: expected VALUE = 9, saw $(cat app.py)"; exit 1; }'
GOAL = "Change the fixture value"
LEDGER = f".spec/specs/{SLUG}/runs.jsonl"
DISTINCT_APPROACH = re.compile(r"distinct\s+testable\s+approach", re.IGNORECASE)
KEPT_ACCEPTANCE = re.compile(r"preserv\w*[^.;|\n]*\bacceptance\b|\bacceptance\b[^.;|\n]*preserv\w*", re.IGNORECASE)
# A fake Pi whose `exec` runs the installed dispatcher for real. The sitecustomize directory on PYTHONPATH
# freezes the dispatcher's wall clock, so every callback shares one instant on any machine.
PI_SETTLE = r"""
import { spawnSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { delimiter } from 'node:path';

const extension = (await import(process.argv[1])).default;
const [python, spec, root, dispatcher, clock] = process.argv.slice(2);
const handlers = new Map();
const inputs = [];
await extension({
  on(name, callback) { handlers.set(name, callback); },
  async exec(executable, args, options) {
    if (executable !== 'sh' || args[0] !== dispatcher) throw Error('not the installed dispatcher command');
    inputs.push([executable, ...args, options.input].join(' '));
    const env = { ...process.env, PYTHONPATH: clock + delimiter + (process.env.PYTHONPATH ?? '') };
    const run = spawnSync(executable, args, { input: options.input, encoding: 'utf8', env });
    if (run.status !== 0) throw Error('dispatcher failed: ' + run.stderr);
    return { code: run.status, stdout: run.stdout };
  },
});
const settle = handlers.get('agent_before_settle');
if (typeof settle !== 'function') throw Error('Pi has no native continuation handler');
const ctx = { cwd: root, sessionManager: { getSessionId: () => 'fixture' } };
const event = { entries: [], continue: false, outcome: 'completed', context: { canContinue: true } };
const results = [];
const settleOnce = async () => {
  const next = await settle(event, ctx);
  results.push(next?.continue === true);
};
const verify = (candidate) => {
  writeFileSync(`${root}/app.py`, `VALUE = ${candidate}\n`);
  const run = spawnSync(python, [spec, '--json', '--root', root, 'verify', 'task-value'], {
    cwd: root, encoding: 'utf8',
  });
  if (run.status !== 1) throw Error(`verify exited ${run.status}: ${run.stderr}`);
};
await verify(1);
await settleOnce();  // new evidence
await settleOnce();  // replay: nothing has happened since
await verify(2);
await settleOnce();  // new evidence again
console.log(JSON.stringify({ inputs, results }));
"""


def _failing(tmp_path: Path, verify: str) -> Path:
    repo(tmp_path)
    package = started_package(tmp_path, verify=verify)
    (tmp_path / "fail-marker").write_text("fail\n", encoding="utf-8")
    return package


def _two_task_package(root: Path, verify: str, *, flood: bool = False) -> Path:
    """Two open tasks with one criterion each; only the first can fail. `flood` overflows every text limit."""
    repo(root)
    package = make_package(root, verify=verify)
    goal = "G" * 600 if flood else GOAL
    criterion = "T" * 400 if flood else "Fixture works"
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8")
        .replace("- goal: Change the fixture value", "- goal: " + goal)
        .replace(
            "- C1 | Fixture works | task-value",
            f"- C1 | {criterion} | task-value\n- C2 | Independent works | task-next",
        ),
        encoding="utf-8",
    )
    tasks = package / "tasks.md"
    tasks.write_text(
        tasks.read_text(encoding="utf-8")
        + "- [ ] Check the independent result\n"
        + "  - id: task-next\n  - boundary: app.py\n  - verify: `true`\n  - expect: green\n",
        encoding="utf-8",
    )
    assert cli(root, "plan-go").returncode == 0
    (root / "fail-marker").write_text("fail\n", encoding="utf-8")
    return package


def _candidate(root: Path, value: int) -> None:
    """A materially different product input for the failing task; its boundary is app.py."""
    (root / "app.py").write_text(f"VALUE = {value}\n", encoding="utf-8")


def _fail(root: Path) -> None:
    assert cli(root, "verify", "task-value").returncode == 1


def _pass(root: Path, value: int) -> None:
    _candidate(root, value)
    (root / "fail-marker").unlink()
    assert cli(root, "verify", "task-value").returncode == 0


def _evidence(package: Path) -> list[tuple[str, str]]:
    """(verified tree, failure signature) of every failed verify, oldest first, from the evidence ledger."""
    rows = [json.loads(line) for line in (package / "runs.jsonl").read_text(encoding="utf-8").splitlines() if line]
    return [(row["tree_fp"], row["sig"]) for row in rows if row.get("kind", "run") == "run" and row["exit"] != 0]


def _mismatches(actions: list[spec_hook.Action], expected: list[bool]) -> list[tuple[int, bool, str]]:
    """The Stop verdicts that differ from `expected`, with what the hook said, for readable failure messages."""
    return [
        (index, action.block, action.reason or action.context)
        for index, (action, wanted) in enumerate(zip(actions, expected))
        if action.block != wanted
    ]


def _stop(root: Path, **values: object) -> spec_hook.Action:
    return spec_hook.handle("stop", {"cwd": str(root), **values})


def _dispatch(host: str, payload: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> dict:
    """Run the real dispatcher CLI once, as a host pipe would, and return the protocol JSON it printed."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert spec_hook.main(["--host", host, "stop"]) == 0
    return json.loads(capsys.readouterr().out or "{}")


def _assert_replan_reason(reason: str, goal: str = GOAL) -> None:
    """A replan re-states the frozen intent, points at real evidence and fits the brief budget."""
    import route

    assert f"Goal: {goal}" in reason, reason
    assert LEDGER in reason, reason
    assert DISTINCT_APPROACH.search(reason), reason
    assert KEPT_ACCEPTANCE.search(reason), reason
    assert len(reason) <= route.BRIEF_BUDGET == 900, len(reason)


def _assert_delivery(action: spec_hook.Action) -> None:
    assert action.block and "spec.py check" in action.reason, action


@pytest.fixture
def wall_clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """A controllable wall clock: 'rapid' and 'late' must never depend on machine speed."""
    now = [1_700_000_000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    return now


# --- C1: no fixed Stop quota ---


@pytest.mark.parametrize("ending", ["verified", "failure-gate"])
def test_each_new_candidate_and_failure_gets_a_replan_without_a_round_quota(tmp_path, ending):
    package = _failing(tmp_path, DISTINCT)
    replans = []
    for candidate in range(1, 8):  # seven: past the retired ceiling of six, still under the task failure gate
        _candidate(tmp_path, candidate)
        _fail(tmp_path)
        replans.append(_stop(tmp_path))
    assert [action.block for action in replans] == [True] * 7, _mismatches(replans, [True] * 7)
    for action in replans:
        _assert_replan_reason(action.reason)
    evidence = _evidence(package)
    assert len(set(evidence)) == len({tree for tree, _ in evidence}) == len({sig for _, sig in evidence}) == 7
    if ending == "verified":
        _pass(tmp_path, 8)
        _assert_delivery(_stop(tmp_path))
        return
    # The per-task failure gate is unchanged: the eighth failed verify blocks the task and ends replanning...
    _candidate(tmp_path, 8)
    _fail(tmp_path)
    stopped = _stop(tmp_path)
    assert not stopped.block and "spec.py unblock task-value" in stopped.context
    # ...until new evidence unblocks it.
    assert cli(tmp_path, "unblock", "task-value", "--reason", "new evidence").returncode == 0
    _candidate(tmp_path, 9)
    _fail(tmp_path)
    resumed = _stop(tmp_path)
    assert resumed.block, resumed
    _assert_replan_reason(resumed.reason)


# --- C2: replan from the frozen goal, affected acceptance and actual evidence ---


@pytest.mark.parametrize("flood", [False, True], ids=["compact", "avalanche"])
def test_replan_reason_restates_the_failing_task_goal_criteria_and_evidence(tmp_path, flood):
    _two_task_package(tmp_path, DISTINCT, flood=flood)
    _fail(tmp_path)
    action = _stop(tmp_path)
    assert action.block, action
    _assert_replan_reason(action.reason, goal="G" * 40 if flood else GOAL)
    reason = action.reason
    assert "task-value" in reason
    assert ("C1 " + "T" * 40 if flood else "C1 Fixture works") in reason
    assert "C2" not in reason and "Independent works" not in reason  # another task's acceptance is not affected


# --- C2: stale attempts start no turn ---


@pytest.mark.parametrize("verify", [DISTINCT, BY_CANDIDATE], ids=["novel-output", "identical-output"])
def test_output_from_an_unchanged_tree_does_not_keep_starting_turns(tmp_path, verify):
    package = _failing(tmp_path, verify)
    actions = []
    for _ in range(3):
        _fail(tmp_path)  # no candidate edit between verifies
        actions.append(_stop(tmp_path))
    evidence = _evidence(package)
    assert len({tree for tree, _ in evidence}) == 1
    assert len({sig for _, sig in evidence}) == (3 if verify == DISTINCT else 1)
    assert [action.block for action in actions] == [True, False, False], _mismatches(actions, [True, False, False])
    # Releasing is a verdict on the evidence, not a latch: a real fix still earns the delivery turn.
    _pass(tmp_path, 1)
    _assert_delivery(_stop(tmp_path))


@pytest.mark.parametrize("candidates", [(1, 2, 1), (1, 2, 3, 1)], ids=["A-B-A", "A-B-C-A"])
def test_returning_to_a_verified_candidate_and_failure_stops_the_loop(tmp_path, candidates):
    package = _failing(tmp_path, BY_CANDIDATE)
    actions = []
    for candidate in candidates:
        _candidate(tmp_path, candidate)
        _fail(tmp_path)
        actions.append(_stop(tmp_path))
    evidence = _evidence(package)
    assert len(set(evidence[:-1])) == len(candidates) - 1 and evidence[-1] == evidence[0]
    expected = [True] * (len(candidates) - 1) + [False]
    assert [action.block for action in actions] == expected, _mismatches(actions, expected)


# --- C2: replay and idle turns start no turn ---


@pytest.mark.parametrize("gap", [0, 30], ids=["rapid", "late"])
def test_pi_dispatcher_answers_the_evidence_not_the_clock_for_an_identical_payload(
    tmp_path, wall_clock, monkeypatch, capsys, gap
):
    _failing(tmp_path, DISTINCT)
    payload = {"cwd": str(tmp_path), "session_id": "s1"}  # everything the Pi extension sends for Stop
    settled = []

    def settle() -> None:
        settled.append(_dispatch("pi", payload, monkeypatch, capsys).get("decision"))

    _fail(tmp_path)
    settle()  # new evidence
    wall_clock[0] += gap
    settle()  # replay: nothing has happened since
    _candidate(tmp_path, 2)
    _fail(tmp_path)
    settle()  # new evidence again
    assert settled == ["block", None, "block"], settled


def test_installed_pi_extension_resumes_only_settles_with_new_evidence(tmp_path, tmp_path_factory):
    node = shutil.which("node")
    if node is None or shutil.which("sh") is None:
        pytest.skip("Node and sh are needed to exercise the installed Pi extension without a model")
    import install_hooks

    home = tmp_path_factory.mktemp("pi-home")
    extension = install_hooks.install_session_hooks("pi", home=home)
    assert extension is not None
    clock = tmp_path_factory.mktemp("frozen-clock")
    (clock / "sitecustomize.py").write_text("import time\ntime.time = lambda: 1700000000.0\n", encoding="utf-8")
    _failing(tmp_path, DISTINCT)
    result = subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            PI_SETTLE,
            extension.as_uri(),
            sys.executable,
            str(SCRIPTS / "spec.py"),
            str(tmp_path),
            str(HOOKS / "spec-hook.sh"),
            str(clock),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert len(set(report["inputs"])) == 1, report  # the same dispatcher command and stdin payload every time
    assert report["results"] == [True, False, True], report


def test_prompt_replay_changes_nothing_and_a_new_prompt_id_starts_an_idle_turn(tmp_path):
    import memory

    package = _failing(tmp_path, DISTINCT)
    _fail(tmp_path)  # evidence that belongs to no user turn yet
    prompt = {"cwd": str(tmp_path), "session_id": "s", "prompt_id": "first"}
    spec_hook.handle("prompt", prompt)
    assert not _stop(tmp_path, session_id="s").block  # idle: the new turn has executed nothing
    _candidate(tmp_path, 1)
    _fail(tmp_path)
    started = memory.snapshot_read(package)["turn_started"]["s"]
    spec_hook.handle("prompt", prompt)  # a replay after new evidence is not user authority
    assert memory.snapshot_read(package)["turn_started"]["s"] == started
    assert _stop(tmp_path, session_id="s").block  # the evidence inside the real turn still earns its replan
    # A new prompt id starts a turn even when its brief is deduplicated: the earlier evidence is not this turn's.
    spec_hook.handle("prompt", {**prompt, "prompt_id": "second"})
    assert not _stop(tmp_path, session_id="s").block


def test_reentry_is_released_without_new_evidence_and_pause_or_cancel_always_releases(tmp_path):
    _failing(tmp_path, DISTINCT)
    _fail(tmp_path)

    def releases(**flags: bool) -> bool:
        return _stop(tmp_path, **flags) == spec_hook.Action()

    assert releases(continuing=True)  # no prior block handed off, so a reentry has nothing to continue from
    assert releases(aborted=True) and releases(paused=True)  # even with a replan owed
    assert _stop(tmp_path).block  # a genuine Stop still gets the replan
    assert releases(continuing=True)  # the block handed off, but nothing new was verified
    _pass(tmp_path, 1)  # the hand-off ends in an actual new checkpoint
    assert releases(aborted=True) and releases(paused=True)  # even with a delivery action owed
    _assert_delivery(_stop(tmp_path))  # no release consumed it


# --- releases and host protocol ---


def test_stop_with_only_blocked_work_does_not_request_another_turn(tmp_path):
    _failing(tmp_path, NOISY)
    for _ in range(4):
        assert cli(tmp_path, "verify", "task-value").returncode == 1
    action = _stop(tmp_path)
    assert not action.block
    assert "spec.py unblock task-value" in action.context


def test_claude_receives_a_release_not_a_block_when_the_inputs_did_not_change(tmp_path, monkeypatch, capsys):
    _failing(tmp_path, DISTINCT)
    outputs = []
    for nonce in range(2):  # the host payload differs per callback, so only the evidence decides
        _fail(tmp_path)  # the same tree; only the diagnostic is novel
        outputs.append(_dispatch("claude", {"cwd": str(tmp_path), "nonce": nonce}, monkeypatch, capsys))
    follow, release = outputs
    assert follow["decision"] == "block" and follow["reason"]
    assert "decision" not in release and "followup_message" not in release, release
    if release:  # an explanation is allowed, but only through Claude's Stop context shape
        assert release["hookSpecificOutput"]["hookEventName"] == "Stop"


def test_first_delivery_stop_asks_for_check_and_replays_without_new_evidence_do_not_block(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    first = _stop(tmp_path)
    assert first.block and "spec.py check" in first.reason
    for _ in range(2):  # no new checkpoint, gate or check between these Stops
        replay = _stop(tmp_path)
        assert not replay.block, replay


def test_blocked_task_hands_off_to_independent_ready_work(tmp_path: Path) -> None:
    _two_task_package(tmp_path, NOISY)
    _fail(tmp_path)
    assert _stop(tmp_path).block
    for _ in range(3):
        _fail(tmp_path)
    action = _stop(tmp_path)
    assert action.block and "Next: task-next" in action.reason
    assert not _stop(tmp_path).block
    assert cli(tmp_path, "verify", "task-next").returncode == 0
    assert not _stop(tmp_path).block  # completed independent work cannot waive the original blocker


def test_novel_final_verification_diagnostic_replans_but_returning_gate_stalls(tmp_path: Path) -> None:
    repo(tmp_path)
    verify = """grep -qx 'VALUE = 0' app.py || { printf 'AssertionError: unexpected %s\\n' "$(cat app.py)"; exit 1; }"""
    started_package(tmp_path, verify=verify)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    _assert_delivery(_stop(tmp_path))
    for candidate, expected in ((1, True), (2, True), (1, False)):
        _candidate(tmp_path, candidate)
        check = cli(tmp_path, "check")
        assert check.returncode == 1
        action = _stop(tmp_path)
        assert action.block is expected, action
    _candidate(tmp_path, 0)
    assert cli(tmp_path, "check").returncode == 0


def test_cursor_release_sends_no_follow_up():
    from adapters import encode

    assert encode("cursor", "stop", spec_hook.Action(context="stalled: done")) == {}
    assert encode("cursor", "stop", spec_hook.Action(block=True, reason="go")) == {"followup_message": "go"}
