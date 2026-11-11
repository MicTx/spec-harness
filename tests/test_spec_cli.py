import json
import subprocess

from tests.v3_support import SLUG, cli, make_package, repo


def test_cli_resolves_root_and_slug_from_nested_directory(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()
    result = cli(nested, "next")
    assert result.returncode == 0
    assert json.loads(result.stdout)["slug"] == SLUG


def test_user_commands_are_only_three():
    from pathlib import Path

    skill = (Path(__file__).parents[1] / "SKILL.md").read_text()
    assert "/spec:new" not in skill
    assert "/spec:push" in skill and "/spec:doctor" in skill


def test_done_reports_archived_result_and_recovery_when_push_fails(tmp_path):
    repo(tmp_path)
    package = make_package(tmp_path)
    spec_path = package / "spec.md"
    spec_path.write_text(
        spec_path.read_text(encoding="utf-8").replace("V4 finish: commit", "V4 finish: push"),
        encoding="utf-8",
    )
    remote = tmp_path.parent / f"{tmp_path.name}-remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=remote, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=tmp_path, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=tmp_path, check=True)
    assert cli(tmp_path, "plan-go").returncode == 0
    assert cli(tmp_path, "verify", "task-value").returncode == 0
    subprocess.run(
        ["git", "remote", "set-url", "origin", str(tmp_path.parent / f"{tmp_path.name}-missing.git")],
        cwd=tmp_path,
        check=True,
    )

    result = cli(tmp_path, "done")

    assert result.returncode == 1
    assert result.stdout, result.stderr
    payload = json.loads(result.stdout)
    assert payload["archive"].endswith(f".spec/specs/archive/{SLUG}")
    assert payload["finish"] == "push"
    assert payload["push"]["error"]
    assert "delivery incomplete" in result.stderr
    assert "spec.py push" in result.stderr

    subprocess.run(["git", "remote", "set-url", "origin", str(remote)], cwd=tmp_path, check=True)
    retry = cli(tmp_path, "push")
    assert retry.returncode == 0, retry.stderr
    assert json.loads(retry.stdout)["pushed"] is True
