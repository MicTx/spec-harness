"""End-to-end CLI and lifecycle-integration tests for spec_handoff."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SPEC_HANDOFF = SCRIPTS / "spec_handoff.py"

SPEC_TEXT = """\
# Handoff CLI - 项目范围

## 1. 问题定义
- **项目目标**：目标
- **目标用户**：用户
- **核心价值**：价值

## 2. 假设与待确认
### 2.1 已确认事实
- 已确认
### 2.2 关键假设
- 假设
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- 不改业务代码
## 4. 最小实现路径
- 复用 checker
- 不复制规则
- 返回退出码
## 5. 技术决策
- Git integration branch：适用外：非 Git 环境测试
"""

TASKS_TEXT = """\
# Handoff CLI - 任务拆解

## 阶段一：实现
- [x] 完成任务一
  - boundary: 只改 A
  - verify: pytest A
- [ ] 进行中任务二
  - boundary: 只改 B
  - verify: pytest B
"""


def _package(root: Path, slug: str = "2026-10-04_cli-handoff") -> Path:
    package = root / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(SPEC_TEXT, encoding="utf-8")
    (package / "tasks.md").write_text(TASKS_TEXT, encoding="utf-8")
    (package / "checklist.md").write_text(
        "# C\n## 验收证据\n- 脚本验证：pytest -q\n**验收结果**：通过\n", encoding="utf-8"
    )
    return package


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SPEC_HANDOFF), *args],
        capture_output=True,
        text=True,
        cwd=root,
    )


# -- CLI subcommands --


def test_update_creates_then_appends():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        package = _package(root)
        first = _run(
            root,
            "update",
            "--root",
            str(root),
            "--slug",
            package.name,
            "--event",
            "pause",
            "--collab",
            "solo",
            "--actor",
            "dev",
            "--note",
            "第一轮",
        )
        assert first.returncode == 0, first.stderr
        assert (package / "handoff.md").is_file()
        second = _run(
            root, "update", "--root", str(root), "--slug", package.name, "--event", "takeover", "--collab", "team"
        )
        assert second.returncode == 0, second.stderr
        content = (package / "handoff.md").read_text(encoding="utf-8")
        assert content.count("### [") == 2
        assert "event: takeover" in content


def test_show_renders_latest_and_json():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        package = _package(root)
        _run(root, "update", "--root", str(root), "--slug", package.name, "--collab", "agent")
        markdown = _run(root, "show", "--root", str(root), "--slug", package.name)
        assert markdown.returncode == 0, markdown.stderr
        assert "新鲜度" in markdown.stdout
        assert "#### 恢复" in markdown.stdout
        payload = _run(root, "show", "--root", str(root), "--slug", package.name, "--format", "json")
        assert payload.returncode == 0, payload.stderr
        data = json.loads(payload.stdout)
        assert data["slug"] == package.name
        assert data["snapshot"]["collab"] == "agent"
        assert data["entry"]["event"] == "pause"
        missing_entry = _run(root, "show", "--root", str(root), "--slug", package.name, "--entry", "9")
        assert missing_entry.returncode == 1


def test_validate_exit_codes():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        package = _package(root)
        absent = _run(root, "validate", "--root", str(root), "--slug", package.name)
        assert absent.returncode == 1
        _run(root, "update", "--root", str(root), "--slug", package.name, "--collab", "solo")
        valid = _run(root, "validate", "--root", str(root), "--slug", package.name)
        assert valid.returncode == 0, valid.stderr
        (package / "handoff.md").write_text("# broken\n", encoding="utf-8")
        invalid = _run(root, "validate", "--root", str(root), "--slug", package.name)
        assert invalid.returncode == 1
        assert invalid.stderr.strip()


def test_update_rejects_unknown_vocabulary():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        package = _package(root)
        result = subprocess.run(
            [
                sys.executable,
                str(SPEC_HANDOFF),
                "update",
                "--root",
                str(root),
                "--slug",
                package.name,
                "--collab",
                "swarm",
            ],
            capture_output=True,
            text=True,
            cwd=root,
        )
        assert result.returncode == 2


def test_update_missing_package_fails_clean():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _package(root)
        result = _run(root, "update", "--root", str(root), "--slug", "2026-10-04_nope-missing")
        assert result.returncode == 1
        assert "error" in result.stderr


# -- lifecycle integration --


def test_route_and_status_surface_handoff():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        package = _package(root)
        route_script = SCRIPTS / "route_spec_package.py"
        before = subprocess.run(
            [sys.executable, str(route_script), "--root", str(root), "--slug", package.name, "--format", "json"],
            capture_output=True,
            text=True,
            cwd=root,
        )
        assert before.returncode == 0, before.stderr
        assert "handoff" not in json.loads(before.stdout)

        _run(root, "update", "--root", str(root), "--slug", package.name, "--collab", "agent")
        after = subprocess.run(
            [sys.executable, str(route_script), "--root", str(root), "--slug", package.name, "--format", "json"],
            capture_output=True,
            text=True,
            cwd=root,
        )
        assert after.returncode == 0, after.stderr
        assert "agent" in json.loads(after.stdout)["handoff"]

        report_script = SCRIPTS / "report_spec_package.py"
        status = subprocess.run(
            [sys.executable, str(report_script), "--root", str(root), "--view", "status"],
            capture_output=True,
            text=True,
            cwd=root,
        )
        assert status.returncode == 0, status.stderr
        assert "handoff agent/" in status.stdout


def test_check_gate_fails_on_invalid_handoff():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        package = _package(root)
        _run(root, "update", "--root", str(root), "--slug", package.name, "--collab", "solo")
        check_script = SCRIPTS / "check_spec_package.py"
        intact = subprocess.run(
            [sys.executable, str(check_script), "--root", str(root), "--slug", package.name],
            capture_output=True,
            text=True,
            cwd=root,
        )
        assert "handoff" not in intact.stderr

        content = (package / "handoff.md").read_text(encoding="utf-8")
        (package / "handoff.md").write_text(content.replace("## 快照", "## 快照坏了", 1), encoding="utf-8")
        broken = subprocess.run(
            [sys.executable, str(check_script), "--root", str(root), "--slug", package.name],
            capture_output=True,
            text=True,
            cwd=root,
        )
        assert broken.returncode == 1
        assert "invalid handoff document" in broken.stderr


def test_archive_closes_handoff_and_moves_it():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        package = _package(root, "2026-10-04_close-handoff")
        tasks_done = TASKS_TEXT.replace("- [ ] 进行中任务二", "- [x] 进行中任务二")
        (package / "tasks.md").write_text(tasks_done, encoding="utf-8")
        _run(root, "update", "--root", str(root), "--slug", package.name, "--collab", "team")

        complete_script = SCRIPTS / "complete_spec_package.py"
        result = subprocess.run(
            [
                sys.executable,
                str(complete_script),
                "--root",
                str(root),
                "--slug",
                package.name,
                "--archive",
                "--verified-assumption",
                "假设成立",
                "--delivered",
                "交付内容",
                "--simplification",
                "最小实现",
                "--out-of-scope",
                "不做扩展",
                "--touched-area",
                "scripts",
                "--untouched-area",
                "docs",
                "--build-evidence",
                "pytest",
                "--test-evidence",
                "pytest -q",
                "--manual-evidence",
                "手工验证通过",
                "--effect-metric",
                "检查项=1",
                "--consistency-evidence",
                "一致",
                "--gate-evidence",
                "gate 通过",
            ],
            capture_output=True,
            text=True,
            cwd=root,
        )
        assert result.returncode == 0, result.stderr
        archive = root / ".spec" / "specs" / "archive" / package.name
        handoff = archive / "handoff.md"
        assert handoff.is_file()
        content = handoff.read_text(encoding="utf-8")
        assert "event: close" in content
        assert "status: done" in content
        assert "entry: 2" in content
        assert "交接记录" in result.stdout


# -- adversarial-review regressions --


def test_note_over_cap_is_rejected():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        package = _package(root)
        result = _run(root, "update", "--root", str(root), "--slug", package.name, "--note", "x" * 4001)
        assert result.returncode == 1
        assert "--note exceeds 4000 characters" in result.stderr
        assert not (package / "handoff.md").exists()


def test_update_refuses_manual_close_event():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        package = _package(root)
        result = _run(root, "update", "--root", str(root), "--slug", package.name, "--event", "close")
        assert result.returncode == 2
        assert not (package / "handoff.md").exists()


def test_show_and_validate_read_archived_package():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        package = _package(root)
        _run(root, "update", "--root", str(root), "--slug", package.name, "--collab", "agent")
        archive = root / ".spec" / "specs" / "archive"
        archive.mkdir(parents=True)
        package.rename(archive / package.name)

        shown = _run(root, "show", "--root", str(root), "--slug", package.name)
        assert shown.returncode == 0, shown.stderr
        assert "event=close" not in shown.stdout  # frozen history, latest stays non-close

        validated = _run(root, "validate", "--root", str(root), "--slug", package.name)
        assert validated.returncode == 0, validated.stderr

        # update never resolves into the archive: a closed log must not grow
        updated = _run(root, "update", "--root", str(root), "--slug", package.name)
        assert updated.returncode == 1
        assert "task package not found" in updated.stderr


def test_oversized_handoff_fails_clean_across_lifecycle():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        package = _package(root)
        (package / "handoff.md").write_text("# handoff\n" + "x" * (3 * 1024 * 1024), encoding="utf-8")

        check_script = SCRIPTS / "check_spec_package.py"
        checked = subprocess.run(
            [sys.executable, str(check_script), "--root", str(root), "--slug", package.name],
            capture_output=True,
            text=True,
            cwd=root,
        )
        assert checked.returncode == 1
        assert "unreadable handoff document" in checked.stderr
        assert "Traceback" not in checked.stderr

        route_script = SCRIPTS / "route_spec_package.py"
        routed = subprocess.run(
            [sys.executable, str(route_script), "--root", str(root), "--slug", package.name, "--format", "json"],
            capture_output=True,
            text=True,
            cwd=root,
        )
        assert routed.returncode == 0, routed.stderr
        assert "不可读" in json.loads(routed.stdout)["handoff"]

        report_script = SCRIPTS / "report_spec_package.py"
        status = subprocess.run(
            [sys.executable, str(report_script), "--root", str(root), "--view", "status"],
            capture_output=True,
            text=True,
            cwd=root,
        )
        assert status.returncode == 0, status.stderr
        assert "不可读" in status.stdout
