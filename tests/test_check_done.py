import json
import subprocess

import pytest

from tests.v3_support import SLUG, cli, git, make_package, repo, started_package


def _finished_package(tmp_path, finish="commit"):
    repo(tmp_path)
    package = make_package(tmp_path)
    spec_path = package / "spec.md"
    spec_path.write_text(
        spec_path.read_text(encoding="utf-8").replace("V4 finish: commit", f"V4 finish: {finish}"),
        encoding="utf-8",
    )
    assert cli(tmp_path, "plan-go").returncode == 0
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    return package


def test_check_passes_and_runs_done_automatically(tmp_path):
    _finished_package(tmp_path)

    result = cli(tmp_path, "check")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert payload["archive"].endswith(f".spec/specs/archive/{SLUG}")
    assert payload["finish"] == "commit"
    assert (tmp_path / ".spec/specs/archive" / SLUG / "completion-summary.md").is_file()
    assert not (tmp_path / ".spec/specs" / SLUG).exists()
    log = subprocess.run(
        ["git", "log", "-1", "--format=%B"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout
    assert f"Spec: {SLUG}" in log


def test_check_restores_an_archive_interrupted_before_commit(tmp_path, monkeypatch):
    import done
    import gitx

    directory = _finished_package(tmp_path)
    before = gitx.head(tmp_path)
    real = gitx.git

    def interrupted(root, *args, **kwargs):
        if args[0] == "commit":
            raise KeyboardInterrupt
        return real(root, *args, **kwargs)

    monkeypatch.setattr(gitx, "git", interrupted)
    with pytest.raises(KeyboardInterrupt):
        done.finish(tmp_path, SLUG)
    assert not directory.exists() and gitx.head(tmp_path) == before
    card = json.loads(cli(tmp_path, "next").stdout)
    assert card["state"] == "check-failed" and not card["archived"]
    resumed = cli(tmp_path, "check")
    assert resumed.returncode == 0, resumed.stderr
    assert json.loads(cli(tmp_path, "next").stdout)["state"] == "delivered"


def test_check_records_an_interrupted_local_commit_without_committing_again(tmp_path, monkeypatch):
    import done
    import gitx

    _finished_package(tmp_path)
    assert cli(tmp_path, "purge").returncode == 0

    def interrupted(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(done, "append_run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        done.finish(tmp_path, SLUG)
    committed = gitx.head(tmp_path)
    assert json.loads(cli(tmp_path, "next").stdout)["state"] == "check-failed"
    resumed = cli(tmp_path, "check")
    assert resumed.returncode == 0, resumed.stderr
    assert gitx.head(tmp_path) == committed


def test_rejected_delivery_commit_restores_the_preexisting_index(tmp_path):
    import gitx

    directory = _finished_package(tmp_path)
    (tmp_path / "app.py").write_text("VALUE = 3\n")
    git(tmp_path, "add", "app.py")
    before = gitx.stdout(tmp_path, "write-tree")
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    assert cli(tmp_path, "check").returncode == 1
    assert directory.exists() and gitx.stdout(tmp_path, "write-tree") == before
    hook.unlink()
    assert cli(tmp_path, "check").returncode == 0


def test_check_only_reports_gate_without_archiving(tmp_path):
    _finished_package(tmp_path)

    result = cli(tmp_path, "check", "--check-only")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert "archive" not in payload
    assert (tmp_path / ".spec/specs" / SLUG / "spec.md").is_file()
    assert not (tmp_path / ".spec/specs/archive" / SLUG).exists()


def test_changed_tree_can_enter_final_verification_without_repair(tmp_path):
    _finished_package(tmp_path)
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    probe = cli(tmp_path, "check", "--check-only")
    assert probe.returncode == 0
    assert json.loads(probe.stdout)["verification"] == "pending"

    result = cli(tmp_path, "check")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert payload["errors"] == []
    assert (tmp_path / ".spec/specs/archive" / SLUG / "completion-summary.md").is_file()


def test_check_recovers_final_failure_without_switching_commands(tmp_path):
    repo(tmp_path)
    command = "printf x >> .spec/verify-count; grep -q 'VALUE = 0' app.py || { echo broken-app >&2; exit 9; }"
    directory = started_package(tmp_path, verify=command)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    source = tmp_path / "app.py"
    source.write_text("VALUE = 7\n", encoding="utf-8")
    failed = cli(tmp_path, "check")
    assert failed.returncode == 1 and "broken-app" in failed.stderr
    payload = json.loads(failed.stdout)
    assert payload["passed"] is False
    assert any(item["code"] == "verify-failed" and item["subject"] == "task-value" for item in payload["findings"])
    assert not any("criterion not proven" in error for error in payload["errors"])
    assert "spec.py check" in payload["next"]["next"] or payload["next"]["recovery"]["final_failed"]
    count = (tmp_path / ".spec/verify-count").read_bytes()
    assert cli(tmp_path, "check").returncode == 1
    assert cli(tmp_path, "done").returncode == 1
    assert (tmp_path / ".spec/verify-count").read_bytes() == count + b"xx"
    assert directory.is_dir()

    source.write_text("VALUE = 0\n", encoding="utf-8")
    retry = cli(tmp_path, "check")
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert json.loads(retry.stdout)["next"]["state"] == "delivered"
    assert (tmp_path / ".spec/verify-count").read_bytes() == count + b"xxx"


def test_check_only_cannot_execute_changed_evidence(tmp_path):
    repo(tmp_path)
    started_package(tmp_path, verify="printf x >> .spec/verify-count")
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    (tmp_path / "app.py").write_text("VALUE = 7\n", encoding="utf-8")
    counter = tmp_path / ".spec/verify-count"
    before = counter.read_bytes()
    probed = cli(tmp_path, "check", "--check-only")
    assert probed.returncode == 0 and json.loads(probed.stdout)["verification"] == "pending"
    assert counter.read_bytes() == before
    assert not (tmp_path / ".spec/specs/archive" / SLUG).exists()


def test_done_does_not_execute_a_drifted_command(tmp_path):
    directory = _finished_package(tmp_path)
    tasks = directory / "tasks.md"
    tasks.write_text(tasks.read_text().replace("`true`", "`touch .spec/not-authorized`"), encoding="utf-8")
    for entrypoint in (("check",), ("done",), ("verify", "--all")):
        refused = cli(tmp_path, *entrypoint)
        assert refused.returncode == 1
        assert not (tmp_path / ".spec/not-authorized").exists()
        assert directory.is_dir()


def test_check_preflights_a_forged_new_task_before_running_any_command(tmp_path):
    directory = _finished_package(tmp_path)
    tasks = directory / "tasks.md"
    tasks.write_text(
        tasks.read_text() + "\n- [x] forged\n  - id: extra\n  - boundary: app.py\n"
        "  - verify: `touch .spec/unauthorized`\n  - expect: green\n"
    )
    refused = cli(tmp_path, "check")
    assert refused.returncode == 1
    assert any(item["code"] == "plan-integrity" for item in json.loads(refused.stdout)["findings"])
    assert not (tmp_path / ".spec/unauthorized").exists()


def test_check_rejects_extra_verify_text_even_when_the_first_command_is_frozen(tmp_path):
    directory = _finished_package(tmp_path)
    tasks = directory / "tasks.md"
    tasks.write_text(tasks.read_text().replace("`true`", "`true` `touch .spec/unauthorized`"))
    result = cli(tmp_path, "check")
    assert result.returncode == 1
    assert any(item["code"] == "plan-integrity" for item in json.loads(result.stdout)["findings"])
    assert not (tmp_path / ".spec/unauthorized").exists()


def test_checkpoint_never_commits_unrelated_staged_paths(tmp_path):
    repo(tmp_path)
    started_package(tmp_path)
    (tmp_path / "other.txt").write_text("keep staged\n", encoding="utf-8")
    git(tmp_path, "add", "other.txt")
    refused = cli(tmp_path, "verify", "task-value")
    assert refused.returncode == 1
    assert "other.txt" in refused.stderr
    assert (tmp_path / "other.txt").read_text() == "keep staged\n"
    staged = subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=tmp_path, text=True)
    assert staged.splitlines() == ["other.txt"]


def test_check_blocks_a_criterion_retargeted_to_an_unrelated_passing_task(tmp_path):
    repo(tmp_path)
    directory = make_package(tmp_path, verify="test -f app.py")
    spec = directory / "spec.md"
    tasks = directory / "tasks.md"
    spec.write_text(spec.read_text().replace("## Decisions", "- C2 | Supporting evidence | task-other\n## Decisions"))
    tasks.write_text(
        tasks.read_text() + "\n- [ ] Support\n  - id: task-other\n  - boundary: app.py\n"
        "  - verify: `true`\n  - expect: green\n"
    )
    assert cli(tmp_path, "plan-go").returncode == 0
    assert cli(tmp_path, "verify", "--all").returncode == 0
    spec.write_text(spec.read_text().replace("- C1 | Fixture works | task-value", "- C1 | Fixture works | task-other"))
    checked = cli(tmp_path, "check")
    assert checked.returncode == 1
    assert any(item["code"] == "plan-integrity" for item in json.loads(checked.stdout)["findings"])
    assert directory.is_dir()
    spec.write_text(spec.read_text().replace("- C1 | Fixture works | task-other", "- C1 | Fixture works | task-value"))
    spec.write_text(spec.read_text().replace("- C1 | Fixture works | task-value\n", ""))
    assert cli(tmp_path, "check").returncode == 1
    assert directory.is_dir()


def test_gate_rejects_duplicate_criterion_ids(tmp_path):
    directory = _finished_package(tmp_path)
    spec = directory / "spec.md"
    spec.write_text(spec.read_text().replace("## Decisions", "- C1 | conflicting meaning | task-value\n## Decisions"))
    result = cli(tmp_path, "check")
    assert result.returncode == 1
    assert any(item["code"] == "plan-integrity" for item in json.loads(result.stdout)["findings"])


def test_environment_repair_retries_the_same_tree_without_a_flag(tmp_path):
    repo(tmp_path)
    started_package(tmp_path, verify="test ! -f .spec/check-disabled")
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    disabled = tmp_path / ".spec" / "check-disabled"
    disabled.touch()
    assert cli(tmp_path, "check").returncode == 1
    disabled.unlink()
    result = cli(tmp_path, "check")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["passed"]


def test_plan_amend_records_a_new_task_as_an_authorized_command(tmp_path):
    repo(tmp_path)
    directory = started_package(tmp_path)
    spec = directory / "spec.md"
    spec.write_text(
        spec.read_text().replace(
            "- C1 | Fixture works | task-value", "- C1 | Fixture works | task-value\n- C2 | Extended case | task-extra"
        )
    )
    draft = tmp_path / ".spec/amend.md"
    draft.write_text("- [ ] Extra\n  - id: task-extra\n  - boundary: app.py\n  - verify: `true`\n  - expect: green\n")
    amended = cli(tmp_path, "plan-amend", "--file", str(draft))
    assert amended.returncode == 0, amended.stderr
    tasks = directory / "tasks.md"
    original = tasks.read_text()
    tasks.write_text(original.replace("  - origin: emergent", "  - depends-on: task-value\n  - origin: emergent"))
    assert cli(tmp_path, "verify", "--all").returncode == 1
    tasks.write_text(original)
    assert cli(tmp_path, "verify", "--all").returncode == 0
    assert cli(tmp_path, "check").returncode == 0


def test_frozen_v4_and_boundary_changes_are_not_authorized_by_their_new_contents(tmp_path):
    directory = _finished_package(tmp_path)
    spec = directory / "spec.md"
    spec.write_text(spec.read_text().replace("V4 finish: commit", "V4 finish: push"))
    result = cli(tmp_path, "check")
    assert any("charter changed" in item for item in json.loads(result.stdout)["errors"])
    spec.write_text(spec.read_text().replace("V4 finish: push", "V4 finish: commit"))
    tasks = directory / "tasks.md"
    tasks.write_text(tasks.read_text().replace("boundary: app.py", "boundary: other.txt"))
    result = cli(tmp_path, "check")
    assert any("task boundary changed" in item for item in json.loads(result.stdout)["errors"])


def test_final_verify_that_changes_the_tree_never_creates_fresh_proof(tmp_path):
    repo(tmp_path)
    command = "test ! -e .spec/mutate || printf 'VALUE = 9\\n' > app.py"
    started_package(tmp_path, verify=command)
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    (tmp_path / ".spec/mutate").write_text("go\n")
    (tmp_path / "app.py").write_text("VALUE = 1\n")
    result = cli(tmp_path, "check")
    assert result.returncode == 1
    assert any(item["code"] == "tree-changed" for item in json.loads(result.stdout)["findings"])
    assert (tmp_path / "app.py").read_text() == "VALUE = 9\n"
    assert (tmp_path / ".spec/specs" / SLUG).exists()


def test_local_delivery_ignores_only_unstaged_outside_dirt(tmp_path):
    _finished_package(tmp_path)
    log = tmp_path / "keep.txt"
    log.write_text("unrelated working copy\n")
    result = cli(tmp_path, "check")
    assert result.returncode == 0, result.stdout + result.stderr
    findings = json.loads(result.stdout)["findings"]
    assert any(item["code"] == "boundary-residual" and item["severity"] == "warning" for item in findings)
    assert log.read_text() == "unrelated working copy\n"
    assert not subprocess.check_output(["git", "ls-files", "keep.txt"], cwd=tmp_path)


def test_committed_outside_content_stays_blocking(tmp_path):
    _finished_package(tmp_path)
    (tmp_path / "outside.txt").write_text("outside the package\n")
    git(tmp_path, "add", "outside.txt")
    git(tmp_path, "commit", "-qm", "unrelated fixture commit")
    result = cli(tmp_path, "check")
    assert result.returncode == 1
    assert any(item["code"] == "boundary-commit" for item in json.loads(result.stdout)["findings"])


def test_check_failing_gate_does_not_archive(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    assert cli(tmp_path, "plan-go").returncode == 0

    result = cli(tmp_path, "check")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["passed"] is False
    assert payload["errors"]
    assert (tmp_path / ".spec/specs" / SLUG / "spec.md").is_file()
    assert not (tmp_path / ".spec/specs/archive" / SLUG).exists()


def test_check_done_chains_push_delivery(tmp_path):
    _finished_package(tmp_path, finish="push")
    remote = tmp_path.parent / f"{tmp_path.name}-remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=remote, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=tmp_path, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=tmp_path, check=True)

    result = cli(tmp_path, "check")

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    payload = json.loads(result.stdout)
    assert payload["finish"] == "push"
    assert payload["push"]["pushed"] is True
    assert (tmp_path / ".spec/specs/archive" / SLUG / "completion-summary.md").is_file()
