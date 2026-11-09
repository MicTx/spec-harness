import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SCRIPT = SCRIPTS / "complete_spec_package.py"

_COMPLETE_SPEC_EN = """\
# Billing System - 项目范围

## 1. 问题定义
- **项目目标**：goal
- **目标用户**：user
- **核心价值**：value

## 2. 假设与待确认
### 2.1 已确认事实
- fact
### 2.2 关键假设
- assumption
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- nothing
## 4. 最小实现路径
- path1
- path2
- path3
### 6.1 行为成效指标
- 无关改动数 <= 0 -> verify: diff
- 澄清前置率 >= 100% -> verify: spec
- 返工次数 <= 0 -> verify: notes
"""

_COMPLETE_SPEC_ZH = _COMPLETE_SPEC_EN.replace("# Billing System - 项目范围", "# 计费系统 - 项目范围")

_COMPLETE_CHECKLIST = """\
# C
- [x] Item

## 跨载体一致性
- [x] Consistent

## 行为成效
- [x] Metrics filled

## 验收证据
- 脚本验证：pytest passed

**验收结果**：通过
"""

_COMPLETE_TASKS = "- [x] Task A\n  - boundary: scripts\n  - verify: pytest\n"
SLUG = "2026-11-09_fix-completion"


def write_package(root: Path, slug: str = SLUG, *, specs_dir: str = ".spec") -> Path:
    package = root / specs_dir / "specs" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(_COMPLETE_SPEC_ZH, encoding="utf-8")
    (package / "tasks.md").write_text(_COMPLETE_TASKS, encoding="utf-8")
    (package / "checklist.md").write_text(_COMPLETE_CHECKLIST, encoding="utf-8")
    return package


def complete(root: Path, *args: str, slug: str = SLUG) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--slug", slug, *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def init_repo(root: Path, slug: str = SLUG) -> Path:
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    (root / "README").write_text("fixture\n", encoding="utf-8")
    git(root, "add", "README")
    git(root, "commit", "-qm", "initial")
    git(root, "checkout", "-qb", f"spec/{slug}")
    package = write_package(root, slug)
    with (package / "spec.md").open("a", encoding="utf-8") as output:
        output.write(f"\n- Git integration branch：`spec/{slug}`\n")
    return package


def archive_path(root: Path, slug: str = SLUG, *, specs_dir: str = ".spec") -> Path:
    return root / specs_dir / "specs" / "archive" / slug


def test_help_has_at_most_eight_options_and_no_removed_flags():
    result = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, check=True)
    options = set(re.findall(r"--[a-z][a-z-]+", result.stdout))
    assert options >= {"--root", "--slug", "--specs-dir", "--archive", "--agents-proposal", "--issue-disposition"}
    assert "--help" in options  # argparse's built-in help is not a business parameter
    assert len(options - {"--help"}) <= 8
    assert "--main-branch" in options
    assert options.isdisjoint(
        {"--follow-up", "--open-risk", "--not-delivered", "--deviation", "--git-record-language", "--allow-incomplete"}
    )


@pytest.mark.parametrize(
    "flag",
    ["--follow-up", "--open-risk", "--not-delivered", "--deviation", "--git-record-language", "--allow-incomplete"],
)
def test_removed_options_are_rejected(tmp_path: Path, flag: str):
    package = write_package(tmp_path)
    result = complete(tmp_path, "--archive", flag, "value")
    assert result.returncode != 0
    assert "unrecognized arguments" in result.stderr
    assert package.is_dir()
    assert not archive_path(tmp_path).exists()


def test_archive_non_git_generates_summary_and_moves_all_files(tmp_path: Path):
    package = write_package(tmp_path)
    (package / "evidence.txt").write_text("proof\n", encoding="utf-8")
    docs = tmp_path / ".spec" / "docs"
    docs.mkdir()
    result = complete(
        tmp_path, "--archive", "--agents-proposal", "+ Reusable project fact", "--agents-proposal", "+ Second fact"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    archived = archive_path(tmp_path)
    summary = (archived / "completion-summary.md").read_text(encoding="utf-8")
    assert not package.exists()
    assert (archived / "evidence.txt").read_text(encoding="utf-8") == "proof\n"
    assert "## Delivery Outcome" in summary
    assert "Task A" in summary
    assert "## Project Fact Proposal\n```diff\n+ Reusable project fact\n+ Second fact\n```" in summary
    assert '"version": 1' in summary
    assert "## 失误方向统计" not in summary
    assert list(docs.iterdir()) == []


def test_manual_only_package_does_not_claim_missing_runs_file(tmp_path: Path):
    package = write_package(tmp_path)
    with (package / "spec.md").open("a", encoding="utf-8") as output:
        output.write("\n本包启用 evidence: runs 协议。\n")
    (package / "tasks.md").write_text(
        "- [x] Review\n  - boundary: analysis\n  - verify: manual: confirmed by maintainer\n", encoding="utf-8"
    )
    result = complete(tmp_path, "--archive")
    assert result.returncode == 0, result.stdout + result.stderr
    archived = archive_path(tmp_path)
    assert not (archived / "runs.jsonl").exists()
    summary = (archived / "completion-summary.md").read_text(encoding="utf-8")
    assert "运行证据见 runs.jsonl" not in summary
    assert "manual" in summary


def test_archive_closes_existing_handoff(tmp_path: Path):
    package = write_package(tmp_path)
    update = subprocess.run(
        [sys.executable, str(SCRIPTS / "spec_handoff.py"), "update", "--root", str(tmp_path), "--slug", SLUG],
        capture_output=True,
        text=True,
    )
    assert update.returncode == 0, update.stdout + update.stderr
    result = complete(tmp_path, "--archive")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not package.exists()
    handoff = (archive_path(tmp_path) / "handoff.md").read_text(encoding="utf-8")
    assert "event: close" in handoff
    assert "status: done" in handoff
    assert "Handoff record" in result.stdout


def test_git_archive_moves_tracked_and_untracked_files(tmp_path: Path):
    package = init_repo(tmp_path)
    git(tmp_path, "add", str(package.relative_to(tmp_path)))
    git(tmp_path, "commit", "-qm", "track package")
    (package / "extra.txt").write_text("untracked\n", encoding="utf-8")
    result = complete(tmp_path, "--archive")
    assert result.returncode == 0, result.stdout + result.stderr
    archived = archive_path(tmp_path)
    assert not package.exists()
    assert (archived / "completion-summary.md").is_file()
    assert (archived / "extra.txt").read_text(encoding="utf-8") == "untracked\n"
    assert git(tmp_path, "diff", "--cached", "--name-status").count("R100") >= 3
    summary = (archived / "completion-summary.md").read_text(encoding="utf-8")
    assert "## Git Record" in summary
    assert re.search(r"^- Commit interval: [0-9a-f]{40}\.\.[0-9a-f]{40}$", summary, re.MULTILINE)


def test_archive_records_configured_mainline_when_multiple_branches_exist(tmp_path: Path):
    package = init_repo(tmp_path)
    git(tmp_path, "branch", "develop")
    (tmp_path / "README").write_text("integration branch change\n", encoding="utf-8")
    git(tmp_path, "add", "README")
    git(tmp_path, "commit", "-qm", "work on integration")
    integration_head = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "checkout", "main")
    (tmp_path / "README").write_text("unrelated main change\n", encoding="utf-8")
    git(tmp_path, "add", "README")
    git(tmp_path, "commit", "-qm", "advance main independently")
    git(tmp_path, "checkout", f"spec/{SLUG}")
    result = complete(tmp_path, "--archive", "--main-branch", "develop")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not package.exists()
    summary = (archive_path(tmp_path) / "completion-summary.md").read_text(encoding="utf-8")
    base = git(tmp_path, "merge-base", "develop", integration_head)
    assert f"- Commit interval: {base}..{integration_head}" in summary


def test_missing_mainline_records_explicit_no_interval(tmp_path: Path):
    init_repo(tmp_path)
    result = complete(tmp_path, "--archive", "--main-branch", "missing-main")
    assert result.returncode == 0, result.stdout + result.stderr
    summary = (archive_path(tmp_path) / "completion-summary.md").read_text(encoding="utf-8")
    assert "- Commit interval: not applicable (cannot resolve merge-base for refs/heads/missing-main)" in summary


def test_generated_interval_warns_when_archive_footer_is_reused(tmp_path: Path):
    package = init_repo(tmp_path)
    first = complete(tmp_path, "--archive")
    assert first.returncode == 0, first.stdout + first.stderr
    git(tmp_path, "add", "--", str(archive_path(tmp_path).relative_to(tmp_path)))
    git(tmp_path, "commit", "-qm", "archive first package")
    git(tmp_path, "checkout", "main")
    git(tmp_path, "merge", "--ff-only", f"spec/{SLUG}")
    old_main = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "checkout", "-qb", "spec/2026-11-10_new-work")
    (tmp_path / "README").write_text("new work\n", encoding="utf-8")
    git(tmp_path, "add", "README")
    git(tmp_path, "commit", "-qm", "new work")
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "check_all_spec_packages.py"),
            "--root",
            str(tmp_path),
            "--revision",
            "HEAD",
            "--baseline",
            old_main,
            "--footer-window-base",
            old_main,
            "--slug",
            package.name,
        ],
        capture_output=True,
        text=True,
    )
    assert "footer attribution cites an archive delivered before this push window" in result.stderr
    assert package.name in result.stderr


def test_git_archive_moves_wholly_untracked_package_without_staging(tmp_path: Path):
    package = init_repo(tmp_path)
    result = complete(tmp_path, "--archive")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not package.exists()
    assert (archive_path(tmp_path) / "completion-summary.md").is_file()
    assert git(tmp_path, "diff", "--cached", "--name-only") == ""


def test_existing_destination_is_never_overwritten(tmp_path: Path):
    package = write_package(tmp_path)
    destination = archive_path(tmp_path)
    destination.mkdir(parents=True)
    sentinel = destination / "keep.txt"
    sentinel.write_text("keep\n", encoding="utf-8")
    result = complete(tmp_path, "--archive", "--force")
    assert result.returncode != 0
    assert "archive destination already exists" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep\n"
    assert not (package / "completion-summary.md").exists()


def test_existing_summary_requires_force_and_can_be_archived(tmp_path: Path):
    package = write_package(tmp_path)
    summary = package / "completion-summary.md"
    summary.write_text("original\n", encoding="utf-8")
    rejected = complete(tmp_path, "--archive")
    assert rejected.returncode != 0
    assert summary.read_text(encoding="utf-8") == "original\n"
    accepted = complete(tmp_path, "--archive", "--force")
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "## Delivery Outcome" in (archive_path(tmp_path) / "completion-summary.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("archive", [False, True])
def test_force_summary_replaces_current_entry_without_changing_hardlink(tmp_path: Path, archive: bool):
    package = write_package(tmp_path)
    old = archive_path(tmp_path, "2026-11-08_old")
    old.mkdir(parents=True)
    sentinel = old / "completion-summary.md"
    sentinel.write_text("frozen summary\n", encoding="utf-8")
    summary = package / "completion-summary.md"
    os.link(sentinel, summary)
    result = complete(tmp_path, "--force", *(["--archive"] if archive else []))
    assert result.returncode == 0, result.stdout + result.stderr
    generated = (archive_path(tmp_path) if archive else package) / "completion-summary.md"
    assert "## Delivery Outcome" in generated.read_text(encoding="utf-8")
    assert sentinel.read_text(encoding="utf-8") == "frozen summary\n"
    assert generated.stat().st_ino != sentinel.stat().st_ino
    assert generated.stat().st_nlink == 1


def test_summary_without_archive_remains_active(tmp_path: Path):
    package = write_package(tmp_path)
    result = complete(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (package / "completion-summary.md").is_file()
    assert not archive_path(tmp_path).exists()


@pytest.mark.parametrize("state", ["pending", "blocked", "decision", "invalid-scope"])
def test_failed_gate_prevents_summary_and_archive(tmp_path: Path, state: str):
    package = write_package(tmp_path)
    spec = package / "spec.md"
    tasks = package / "tasks.md"
    if state == "pending":
        tasks.write_text(_COMPLETE_TASKS.replace("[x]", "[ ]"), encoding="utf-8")
    elif state == "blocked":
        tasks.write_text(_COMPLETE_TASKS + "\n阻塞：等待外部系统\n", encoding="utf-8")
    elif state == "decision":
        spec.write_text(
            _COMPLETE_SPEC_ZH.replace("### 2.3 待确认问题\n- 无", "### 2.3 待确认问题\n- [ ] [decision] Choose tier"),
            encoding="utf-8",
        )
    else:
        with spec.open("a", encoding="utf-8") as output:
            output.write("\n### 5.1 验证策略\n- 范围级别：unknown\n- 快速检查：pytest\n")
    result = complete(tmp_path, "--archive")
    assert result.returncode != 0
    assert "check gates" in result.stderr
    assert package.is_dir()
    assert not (package / "completion-summary.md").exists()
    assert not archive_path(tmp_path).exists()


def test_archive_records_scoped_legacy_evidence(tmp_path: Path):
    package = write_package(tmp_path)
    with (package / "spec.md").open("a", encoding="utf-8") as output:
        output.write("\n### 5.1 验证策略\n- 范围级别：package\n- 快速检查：pytest tests/example.py\n")
    checklist = package / "checklist.md"
    checklist.write_text(
        _COMPLETE_CHECKLIST.replace("- 脚本验证：pytest passed", "- 脚本验证：pytest tests/example.py passed"),
        encoding="utf-8",
    )
    result = complete(tmp_path, "--archive")
    assert result.returncode == 0, result.stdout + result.stderr
    summary = (archive_path(tmp_path) / "completion-summary.md").read_text(encoding="utf-8")
    assert "- Level: package" in summary
    assert "See task package for acceptance evidence" in summary


def test_validated_decision_allows_archive(tmp_path: Path):
    package = write_package(tmp_path)
    spec = package / "spec.md"
    spec.write_text(
        _COMPLETE_SPEC_ZH.replace(
            "### 2.3 待确认问题\n- 无",
            "### 2.3 待确认问题\n- [x] [decision] Choose tier | answer: paid | authority: user:approval",
        ),
        encoding="utf-8",
    )
    result = complete(tmp_path, "--archive")
    assert result.returncode == 0, result.stdout + result.stderr


def test_full_package_check_rejects_changed_archive_with_unfinished_decision(tmp_path: Path):
    package = init_repo(tmp_path)
    spec = package / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "### 2.3 待确认问题\n- 无", "### 2.3 待确认问题\n- [ ] [decision] Choose tier"
        ),
        encoding="utf-8",
    )
    destination = archive_path(tmp_path)
    destination.parent.mkdir(parents=True)
    package.rename(destination)
    (destination / "completion-summary.md").write_text("manually archived\n", encoding="utf-8")
    git(tmp_path, "add", "--", str(destination.relative_to(tmp_path)))
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "check_all_spec_packages.py"), "--root", str(tmp_path), "--index"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "check gates not passed" in result.stdout + result.stderr


def test_typed_issue_disposition_is_validated_and_recorded(tmp_path: Path):
    write_package(tmp_path)
    issue = {
        "id": "risk-vendor",
        "summary": "vendor API compatibility",
        "actionable": True,
        "disposition": "accepted_risk",
        "owner": "maintainer",
        "rationale": "legacy clients",
        "reviewTrigger": "next major release",
    }
    result = complete(tmp_path, "--archive", "--issue-disposition", json.dumps(issue))
    assert result.returncode == 0, result.stdout + result.stderr
    summary = (archive_path(tmp_path) / "completion-summary.md").read_text(encoding="utf-8")
    assert '"disposition": "accepted_risk"' in summary
    assert '"risk-vendor"' in summary


@pytest.mark.parametrize("issue", ["{", '{"id":"risk-vendor"}', "[]"])
def test_invalid_issue_disposition_blocks_archive_without_writes(tmp_path: Path, issue: str):
    package = write_package(tmp_path)
    result = complete(tmp_path, "--archive", "--issue-disposition", issue)
    assert result.returncode != 0
    assert package.is_dir()
    assert not (package / "completion-summary.md").exists()
    assert not archive_path(tmp_path).exists()


def test_issue_disposition_can_read_json_file(tmp_path: Path):
    write_package(tmp_path)
    path = tmp_path / "issue.json"
    path.write_text(
        json.dumps(
            {
                "id": "noise",
                "summary": "not a defect",
                "actionable": False,
                "disposition": "non_actionable",
                "rationale": "working as designed",
            }
        ),
        encoding="utf-8",
    )
    result = complete(tmp_path, "--archive", "--issue-disposition", f"@{path}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"id": "noise"' in (archive_path(tmp_path) / "completion-summary.md").read_text(encoding="utf-8")


def test_followup_issue_requires_a_valid_archived_target(tmp_path: Path):
    package = write_package(tmp_path)
    target_slug = "2026-11-08_fix-followup"
    issue = json.dumps(
        {
            "id": "fixed",
            "summary": "resolved in follow-up",
            "actionable": True,
            "disposition": "resolved_followup",
            "followUpSpecSlug": target_slug,
            "evidence": "tests",
        }
    )
    rejected = complete(tmp_path, "--archive", "--issue-disposition", issue)
    assert rejected.returncode != 0
    assert "not archived" in rejected.stderr
    assert not (package / "completion-summary.md").exists()
    write_package(tmp_path, target_slug)
    target_result = complete(tmp_path, "--archive", slug=target_slug)
    assert target_result.returncode == 0, target_result.stdout + target_result.stderr
    accepted = complete(tmp_path, "--archive", "--issue-disposition", issue)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    summary = (archive_path(tmp_path) / "completion-summary.md").read_text(encoding="utf-8")
    assert target_slug in summary


def test_custom_specs_directory(tmp_path: Path):
    package = write_package(tmp_path, specs_dir="project-state")
    result = complete(tmp_path, "--specs-dir", "project-state", "--archive")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not package.exists()
    assert (archive_path(tmp_path, specs_dir="project-state") / "completion-summary.md").is_file()


def test_tracked_git_package_with_pathspec_metacharacters_still_uses_git_mv(tmp_path: Path):
    package = init_repo(tmp_path)
    (tmp_path / ".spec").rename(tmp_path / "state[0]")
    package = tmp_path / "state[0]" / "specs" / package.name
    git(tmp_path, "add", "--", f":(literal){package.relative_to(tmp_path).as_posix()}")
    git(tmp_path, "commit", "-qm", "track package")
    result = complete(tmp_path, "--specs-dir", "state[0]", "--archive")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not package.exists()
    assert (archive_path(tmp_path, specs_dir="state[0]") / "completion-summary.md").is_file()
    assert git(tmp_path, "diff", "--cached", "--name-status").count("R100") >= 3
    assert git(tmp_path, "ls-files", "--deleted") == ""


def test_ancestor_symlink_in_specs_path_is_rejected(tmp_path: Path):
    # done must not follow a symlinked .spec or specs into frozen history;
    # without the canonical-path guard --force would rewrite a sealed summary
    # and --archive could relocate a frozen package.
    history = tmp_path / ".history" / "specs"
    package = history / SLUG
    package.mkdir(parents=True)
    (package / "spec.md").write_text(_COMPLETE_SPEC_ZH, encoding="utf-8")
    (package / "tasks.md").write_text(_COMPLETE_TASKS, encoding="utf-8")
    (package / "checklist.md").write_text(_COMPLETE_CHECKLIST, encoding="utf-8")
    (tmp_path / ".spec").symlink_to(tmp_path / ".history")
    result = complete(tmp_path, "--force")
    assert result.returncode != 0
    assert (package / "completion-summary.md").exists() is False


def test_invalid_development_record_slug_rejected(tmp_path: Path):
    write_package(tmp_path, "invalid")
    result = complete(tmp_path, "--archive", slug="invalid")
    assert result.returncode != 0
    assert "YYYY-MM-DD_slug" in result.stderr


def test_failed_git_move_keeps_active_package_and_summary(tmp_path: Path):
    package = init_repo(tmp_path)
    git(tmp_path, "add", str(package.relative_to(tmp_path)))
    git(tmp_path, "commit", "-qm", "track package")
    (tmp_path / ".git" / "index.lock").write_text("locked\n", encoding="utf-8")
    result = complete(tmp_path, "--archive")
    assert result.returncode != 0
    assert "archive failed" in result.stderr
    assert "index.lock" in result.stderr
    assert package.is_dir()
    assert (package / "completion-summary.md").is_file()
    assert not archive_path(tmp_path).exists()


def test_git_archive_can_retry_after_index_lock_with_force(tmp_path: Path):
    package = init_repo(tmp_path)
    git(tmp_path, "add", str(package.relative_to(tmp_path)))
    git(tmp_path, "commit", "-qm", "track package")
    lock = tmp_path / ".git" / "index.lock"
    lock.write_text("locked\n", encoding="utf-8")
    first = complete(tmp_path, "--archive")
    assert first.returncode != 0
    assert "retry with --force" in first.stderr
    assert (package / "completion-summary.md").is_file()
    lock.unlink()
    retry = complete(tmp_path, "--archive", "--force")
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert (archive_path(tmp_path) / "completion-summary.md").is_file()


def test_runs_gate_is_rechecked_before_archive(tmp_path: Path):
    package = init_repo(tmp_path)
    with (package / "spec.md").open("a", encoding="utf-8") as output:
        output.write("\n本包启用 evidence: runs 协议。\n")
    (package / "tasks.md").write_text(
        '- [x] Machine\n  - boundary: tests\n  - verify: `python3 -c "print(1)"`\n', encoding="utf-8"
    )
    record = subprocess.run(
        [sys.executable, str(SCRIPTS / "spec_verify.py"), "run", "--root", str(tmp_path), "--slug", SLUG, "--all"],
        capture_output=True,
        text=True,
    )
    assert record.returncode == 0, record.stdout + record.stderr
    (tmp_path / "README").write_text("changed after run\n", encoding="utf-8")
    result = complete(tmp_path, "--archive")
    assert result.returncode != 0
    assert "check gates" in result.stderr
    assert not archive_path(tmp_path).exists()


def test_archive_carries_fresh_machine_evidence(tmp_path: Path):
    package = init_repo(tmp_path)
    with (package / "spec.md").open("a", encoding="utf-8") as output:
        output.write("\n本包启用 evidence: runs 协议。\n")
    (package / "tasks.md").write_text(
        '- [x] Machine\n  - boundary: tests\n  - verify: `python3 -c "print(1)"`\n'
        "- [x] Review\n  - boundary: review\n  - verify: manual: reviewed\n",
        encoding="utf-8",
    )
    record = subprocess.run(
        [sys.executable, str(SCRIPTS / "spec_verify.py"), "run", "--root", str(tmp_path), "--slug", SLUG, "--all"],
        capture_output=True,
        text=True,
    )
    assert record.returncode == 0, record.stdout + record.stderr
    result = complete(tmp_path, "--archive")
    assert result.returncode == 0, result.stdout + result.stderr
    summary = (archive_path(tmp_path) / "completion-summary.md").read_text(encoding="utf-8")
    assert "- Machine verification rate: 1/1; 1 manual task(s) excluded from the denominator" in summary
    assert '- Script verification: `python3 -c "print(1)"` -> exit 0 (runs:task_0001)' in summary
    assert (archive_path(tmp_path) / "runs.jsonl").is_file()
