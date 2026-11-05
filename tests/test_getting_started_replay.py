"""Replay the published tutorial in an isolated checkout, project, and HOME."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from export_public_repo import build_public_tree  # noqa: E402

GUIDE = ROOT / "docs" / "getting-started.md"
PUBLIC_URL = "https://github.com/MicTx/spec-harness.git"
SLUG = "2026-11-04_fix-add-function"


def guide_block(section: str, language: str, needle: str) -> str:
    lines = GUIDE.read_text(encoding="utf-8").splitlines(keepends=True)
    start = lines.index(f"## {section}\n") + 1
    selected: list[str] = []
    in_fence = False
    for line in lines[start:]:
        if line.startswith("```"):
            in_fence = not in_fence
        if not in_fence and line.startswith("## "):
            break
        selected.append(line)
    blocks = re.findall(rf"(?ms)^```{language}\n(.*?)^```", "".join(selected))
    matches = [block for block in blocks if needle in block]
    assert len(matches) == 1, (section, needle, len(matches))
    return matches[0]


def guide_bash_blocks() -> set[tuple[str, str]]:
    section = ""
    language = ""
    block: list[str] = []
    result: set[tuple[str, str]] = set()
    in_fence = False
    for line in GUIDE.read_text(encoding="utf-8").splitlines(keepends=True):
        if not in_fence and line.startswith("## "):
            section = line[3:].strip()
        if line.startswith("```"):
            if in_fence:
                if language == "bash":
                    result.add((section, "".join(block)))
                in_fence = False
            else:
                language = line[3:].strip()
                block = []
                in_fence = True
        elif in_fence:
            block.append(line)
    return result


def run(args, *, cwd: Path, env: dict[str, str], expect: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == expect, (args, result.stdout, result.stderr)
    return result


def test_getting_started_replays_failure_fix_and_archive(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    source = tmp_path / "public-source"
    checkout = tmp_path / "spec-harness"
    project = tmp_path / "spec-practice"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "TMPDIR": str(tmp_path),
        "BACKUP_ROOT": str(home / "backups"),
        "SPEC_SKIP_SIGNING": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Tutorial Test",
        "GIT_AUTHOR_EMAIL": "tutorial@example.invalid",
        "GIT_COMMITTER_NAME": "Tutorial Test",
        "GIT_COMMITTER_EMAIL": "tutorial@example.invalid",
    }
    build_public_tree(ROOT, source)
    run(["git", "init", "-q", "-b", "main"], cwd=source, env=env)
    run(["git", "add", "-A"], cwd=source, env=env)
    run(["git", "commit", "-qm", "public source fixture"], cwd=source, env=env)
    exercised: set[tuple[str, str]] = set()

    def shell(section: str, needle: str, *, cwd: Path, expect: int = 0):
        block = guide_block(section, "bash", needle)
        exercised.add((section, block))
        block = block.replace(PUBLIC_URL, shlex.quote(str(source)))
        block = block.replace("/tmp/spec-practice", shlex.quote(str(project)))
        block = block.replace("/tmp/spec-harness", shlex.quote(str(checkout)))
        return run(["bash", "-e", "-c", block], cwd=cwd, env=env, expect=expect)

    first = "第一步：准备环境"
    second = "第二步：安装 Spec Harness"
    shell(first, "git --version", cwd=tmp_path)
    shell(first, "mkdir -p /tmp/spec-practice", cwd=tmp_path)
    shell(first, "cat > calc.py", cwd=project)
    baseline = shell(first, "unittest discover", cwd=project, expect=1)
    assert "-1 != 5" in baseline.stderr
    shell(first, "git add calc.py", cwd=project)
    remote = tmp_path / "remote.git"
    run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], cwd=tmp_path, env=env)
    run(["git", "remote", "add", "origin", str(remote)], cwd=project, env=env)
    run(["git", "push", "-u", "origin", "main"], cwd=project, env=env)

    cloned = shell(second, "git clone", cwd=tmp_path)
    assert re.search(r"(?m)^[0-9a-f]{40}$", cloned.stdout)
    shell(second, "INSTALL_PROFILE=full", cwd=checkout)
    smoke = shell(second, "smoke_test_spec_skill.py", cwd=checkout)
    assert "smoke: OK" in smoke.stdout
    shell(second, "doctor_spec_environment.py", cwd=checkout)

    shell("第三步：创建任务包", "cd /tmp/spec-practice", cwd=tmp_path)
    shell("第三步：创建任务包", "init_spec_package.py", cwd=project)
    branch = shell("第三步：创建任务包", "git branch --show-current", cwd=project)
    assert f"spec/{SLUG}" in branch.stdout
    package = project / ".spec" / "specs" / SLUG
    spec_path = package / "spec.md"
    tasks_path = package / "tasks.md"
    spec_path.write_text(guide_block("第四步：填写 `spec.md`", "markdown", "# Fix add function"), encoding="utf-8")
    tasks_path.write_text(guide_block("第五步：填写 `tasks.md`", "markdown", "# Fix add function"), encoding="utf-8")
    assert "- [ ] 修复 add" in spec_path.read_text(encoding="utf-8")
    assert "- [ ] 修复 add" in tasks_path.read_text(encoding="utf-8")

    failed = shell("第六步：运行 verify，生成 `runs.jsonl`", "spec_verify.py run", cwd=project, expect=1)
    assert "FAIL exit=1" in failed.stdout
    recorded = shell("第六步：运行 verify，生成 `runs.jsonl`", "cat .spec/specs", cwd=project)
    assert json.loads(recorded.stdout)["exit"] == 1
    correction = guide_block("第七步：故意失败一次，再修正", "markdown", "new output:").strip()
    spec = spec_path.read_text(encoding="utf-8")
    marker = "### 5.5 修正日志\n- none."
    assert marker in spec
    spec_path.write_text(spec.replace(marker, "### 5.5 修正日志\n" + correction), encoding="utf-8")
    shell("第七步：故意失败一次，再修正", "path = Path('calc.py')", cwd=project)
    passed = shell("第七步：故意失败一次，再修正", "spec_verify.py run", cwd=project)
    assert "OK exit=0" in passed.stdout
    spec = spec_path.read_text(encoding="utf-8")
    assert spec.count("- [ ] 修复 add") == 2
    spec_path.write_text(spec.replace("- [ ] 修复 add", "- [x] 修复 add"), encoding="utf-8")
    tasks = tasks_path.read_text(encoding="utf-8")
    assert tasks.count("- [ ] 修复 add") == 1
    tasks_path.write_text(tasks.replace("- [ ] 修复 add", "- [x] 修复 add"), encoding="utf-8")

    checked = shell("第八步：跑单包门禁", "check_spec_package.py", cwd=project)
    assert "收敛状态：已收敛" in checked.stdout
    shell("第八步：跑单包门禁", "check_all_spec_packages.py", cwd=project)
    shell("第九步：恢复演练（归档前）", "route_spec_package.py", cwd=project)
    recovered = shell("第八步：跑单包门禁", "check_spec_package.py", cwd=project)
    assert "收敛状态：已收敛" in recovered.stdout
    records = (package / "runs.jsonl").read_bytes()
    assert [json.loads(line)["exit"] for line in records.splitlines()] == [1, 0]
    shell("第十步：归档并生成完成总结", "complete_spec_package.py", cwd=project)
    archive = project / ".spec" / "specs" / "archive" / SLUG
    assert (archive / "runs.jsonl").read_bytes() == records
    assert "runs:task-0001" in (archive / "completion-summary.md").read_text(encoding="utf-8")
    shell("第十一步：提交，再决定是否推送", "git status --short", cwd=project)
    shell("第十一步：提交，再决定是否推送", "git add -A", cwd=project)
    shell("第十一步：提交，再决定是否推送", "push_spec_package.py", cwd=project)
    local = run(["git", "rev-parse", "main"], cwd=project, env=env).stdout.strip()
    remote_main = run(["git", "--git-dir", str(remote), "rev-parse", "main"], cwd=project, env=env).stdout.strip()
    assert local == remote_main
    shell("附录 D：项目级长路", "autoplan_gate.py --help", cwd=project)
    # The corrupt-record recovery branch is executed by its own isolated test.
    recovery = ("第八步：跑单包门禁", guide_block("第八步：跑单包门禁", "bash", "runs.jsonl.corrupt"))
    assert exercised | {recovery} == guide_bash_blocks()


def test_tutorial_checkout_guard_rejects_source_without_this_guide(tmp_path):
    source = tmp_path / "old-source"
    source.mkdir()
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Tutorial Test",
        "GIT_AUTHOR_EMAIL": "tutorial@example.invalid",
        "GIT_COMMITTER_NAME": "Tutorial Test",
        "GIT_COMMITTER_EMAIL": "tutorial@example.invalid",
    }
    run(["git", "init", "-q", "-b", "main"], cwd=source, env=env)
    (source / "README.md").write_text("old source\n", encoding="utf-8")
    run(["git", "add", "README.md"], cwd=source, env=env)
    run(["git", "commit", "-qm", "old source"], cwd=source, env=env)
    checkout = tmp_path / "old-checkout"
    block = guide_block("第二步：安装 Spec Harness", "bash", "git clone")
    block = block.replace(PUBLIC_URL, shlex.quote(str(source)))
    block = block.replace("/tmp/spec-harness", shlex.quote(str(checkout)))
    run(["bash", "-e", "-c", block], cwd=tmp_path, env=env, expect=1)


def test_tutorial_corrupt_run_recovery_preserves_existing_backup(tmp_path):
    from tests.test_spec_verify import PASSING_TASKS, init_repo, make_package

    project = tmp_path / "project"
    project.mkdir()
    init_repo(project)
    package = make_package(project, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
    corrupt = b'{"task":\n' + b"x" * (2 * 1024 * 1024)
    (package / "runs.jsonl").write_bytes(corrupt)
    block = guide_block("第八步：跑单包门禁", "bash", "runs.jsonl.corrupt")
    block = block.replace("/tmp/spec-practice", shlex.quote(str(project)))
    block = block.replace("/tmp/spec-harness", shlex.quote(str(ROOT)))
    env = {**os.environ, "HOME": str(tmp_path)}
    run(["bash", "-e", "-c", block], cwd=project, env=env)
    backup = project / ".spec/artifacts" / f"{SLUG}-runs.jsonl.corrupt"
    assert backup.read_bytes() == corrupt
    records = (package / "runs.jsonl").read_bytes()
    assert len(records.splitlines()) == 2
    guarded = run(["bash", "-e", "-c", block], cwd=project, env=env)
    assert "备份已存在" in guarded.stderr
    assert backup.read_bytes() == corrupt
    assert (package / "runs.jsonl").read_bytes() == records


def test_tutorial_recovery_does_not_move_evidence_when_backup_directory_is_unusable(tmp_path):
    from tests.test_spec_verify import PASSING_TASKS, init_repo, make_package

    project = tmp_path / "project"
    project.mkdir()
    init_repo(project)
    package = make_package(project, SLUG, runs_protocol=True, tasks_text=PASSING_TASKS)
    corrupt = b'{"task":\n'
    (package / "runs.jsonl").write_bytes(corrupt)
    (project / ".spec/artifacts").write_text("not a directory", encoding="utf-8")
    block = guide_block("第八步：跑单包门禁", "bash", "runs.jsonl.corrupt")
    block = block.replace("/tmp/spec-practice", shlex.quote(str(project)))
    block = block.replace("/tmp/spec-harness", shlex.quote(str(ROOT)))
    result = subprocess.run(["bash", "-c", block], cwd=project, capture_output=True, text=True)
    assert result.returncode != 0
    assert (package / "runs.jsonl").read_bytes() == corrupt


def test_init_requires_explicit_ui_surface_before_accepting_a_ui_change(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Tutorial Test",
        "GIT_AUTHOR_EMAIL": "tutorial@example.invalid",
        "GIT_COMMITTER_NAME": "Tutorial Test",
        "GIT_COMMITTER_EMAIL": "tutorial@example.invalid",
    }
    run(["git", "init", "-q", "-b", "main"], cwd=project, env=env)
    (project / "index.html").write_text("<button>Before</button>\n", encoding="utf-8")
    (project / "tests").mkdir()
    (project / "tests" / "test_calc.py").write_text(
        "import unittest\nclass TestCalc(unittest.TestCase):\n"
        "    def test_calc(self):\n        self.assertEqual(2 + 3, 5)\n",
        encoding="utf-8",
    )
    run(["git", "add", "-A"], cwd=project, env=env)
    run(["git", "commit", "-qm", "baseline"], cwd=project, env=env)
    scripts = ROOT / "scripts"
    run(
        [
            sys.executable,
            str(scripts / "init_spec_package.py"),
            "--root",
            str(project),
            "--slug",
            SLUG,
            "--title",
            "UI change",
        ],
        cwd=project,
        env=env,
    )
    package = project / ".spec" / "specs" / SLUG
    spec_path = package / "spec.md"
    template = spec_path.read_text(encoding="utf-8")
    initial_surface = next(line for line in template.splitlines() if line.startswith("- UI surface:"))
    spec = guide_block("第四步：填写 `spec.md`", "markdown", "# Fix add function")
    spec = spec.replace("- UI surface: unchanged", initial_surface)
    spec = spec.replace("- [ ] 修复 add", "- [x] 修复 add")
    spec_path.write_text(spec, encoding="utf-8")
    tasks = guide_block("第五步：填写 `tasks.md`", "markdown", "# Fix add function")
    (package / "tasks.md").write_text(tasks.replace("- [ ] 修复 add", "- [x] 修复 add"), encoding="utf-8")
    (project / "index.html").write_text("<button>After</button>\n", encoding="utf-8")
    run(
        [
            sys.executable,
            str(scripts / "spec_verify.py"),
            "run",
            "--root",
            str(project),
            "--slug",
            SLUG,
            "--task",
            "task-0001",
        ],
        cwd=project,
        env=env,
    )
    checked = subprocess.run(
        [sys.executable, str(scripts / "check_spec_package.py"), "--root", str(project), "--slug", SLUG],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if checked.returncode == 0:
        archived = subprocess.run(
            [
                sys.executable,
                str(scripts / "complete_spec_package.py"),
                "--root",
                str(project),
                "--slug",
                SLUG,
                "--archive",
            ],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert archived.returncode != 0, "Unselected UI surface passed both check and archive"
    assert checked.returncode != 0, checked.stdout
    assert "SPEC-UI-001" in checked.stdout + checked.stderr
    spec_path.write_text(spec.replace(initial_surface, "- UI surface: changed"), encoding="utf-8")
    checked = subprocess.run(
        [sys.executable, str(scripts / "check_spec_package.py"), "--root", str(project), "--slug", SLUG],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert checked.returncode != 0
    assert "SPEC-UI-002" in checked.stdout + checked.stderr
