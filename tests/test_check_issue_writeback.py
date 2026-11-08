import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_issue_writeback.py"
CHECK_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_spec_package.py"
SLUG = "2026-01-01_fix-fixture"

SPEC = """# fixture - 项目范围

<!-- spec:correction-log -->
### 5.5 修正日志
- none.
"""

TASKS = """# fixture - 任务拆解

## 阶段一
- [ ] 现有任务
  - boundary: fixture
  - verify: `true`
"""


def _make_package(root: Path, spec: str = SPEC, tasks: str = TASKS) -> Path:
    package = root / ".spec" / "specs" / SLUG
    package.mkdir(parents=True)
    (package / "spec.md").write_text(spec, encoding="utf-8")
    (package / "tasks.md").write_text(tasks, encoding="utf-8")
    return package


def _write_gaps(root: Path, gaps: list[str]) -> Path:
    path = root / "check.json"
    path.write_text(json.dumps({"converged": False, "gaps": gaps}), encoding="utf-8")
    return path


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), args[0], "--root", str(root), *args[1:]],
        capture_output=True,
        text=True,
    )


def _plan(root: Path, gaps_path: Path) -> subprocess.CompletedProcess[str]:
    return _run(root, "plan", "--slug", SLUG, "--gaps", str(gaps_path))


def _apply(root: Path, plan_path: Path) -> subprocess.CompletedProcess[str]:
    return _run(root, "apply", "--slug", SLUG, "--plan", str(plan_path))


def _snapshot(package: Path) -> dict[str, bytes]:
    return {name: (package / name).read_bytes() for name in ("spec.md", "tasks.md")}


def _seed_plan(root: Path, package: Path, gaps: list[str]) -> tuple[Path, dict]:
    gaps_path = _write_gaps(root, gaps)
    plan_path = root / "plan.json"
    result = _run(root, "plan", "--slug", SLUG, "--gaps", str(gaps_path), "--out", str(plan_path))
    assert result.returncode == 0, result.stderr
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    for task in plan["tasks"]:
        task["verify"] = "manual: reviewed by main session"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return plan_path, plan


def test_weak_id_is_stable_across_volatile_counts() -> None:
    sys.path.insert(0, str(SCRIPT.parent))
    from check_issue_writeback import weak_task_id

    assert weak_task_id("未勾任务 2 项") == weak_task_id("未勾任务 5 项")
    assert weak_task_id("未勾任务 2 项").startswith("task-checkfix-t-")
    assert weak_task_id("task-3 缺 verify") != weak_task_id("task-7 缺 verify")
    assert weak_task_id("task_0001 缺 boundary") != weak_task_id("task_0002 缺 boundary")


def test_plan_reports_tasks_without_writing(tmp_path: Path) -> None:
    package = _make_package(tmp_path)
    before = _snapshot(package)
    _, payload = _seed_plan(tmp_path, package, ["未勾任务 2 项", "验收未通过"])
    assert payload["next"] == "apply"
    assert len(payload["tasks"]) == 2
    assert all(task["id"].startswith("task-checkfix-t-") for task in payload["tasks"])
    assert all(task["disposition"] == "resolved_current" for task in payload["tasks"])
    assert payload["correction_log_row"] and "new output:" in payload["correction_log_row"]
    assert _snapshot(package) == before


def test_apply_appends_and_gate_stays_unconverged(tmp_path: Path) -> None:
    package = _make_package(tmp_path)
    plan_path, _ = _seed_plan(tmp_path, package, ["未勾任务 1 项"])
    before_tasks = (package / "tasks.md").read_text(encoding="utf-8")
    result = _apply(tmp_path, plan_path)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["next"] == "run" and len(payload["tasks"]) == 1
    after_tasks = (package / "tasks.md").read_text(encoding="utf-8")
    assert after_tasks.startswith(before_tasks)
    assert "## check 回写修正" in after_tasks
    assert "未通过项:" in after_tasks and "处置枚举: resolved_current" in after_tasks
    spec_after = (package / "spec.md").read_text(encoding="utf-8")
    assert "| 1 |" in spec_after and ROW_MARKER_TOKEN in spec_after
    sys.path.insert(0, str(SCRIPT.parent))
    from spec_package_support import correction_log_errors, parse_task_records

    assert correction_log_errors(spec_after) == []
    ids = {record.id for record in parse_task_records(after_tasks)}
    assert set(payload["tasks"]) <= ids
    check = subprocess.run(
        [sys.executable, str(CHECK_SCRIPT), "--root", str(tmp_path), "--slug", SLUG, "--format", "json"],
        capture_output=True,
        text=True,
    )
    gate = json.loads(check.stdout)
    assert gate["converged"] is False


def test_reapplying_old_snapshot_is_refused_without_writes(tmp_path: Path) -> None:
    package = _make_package(tmp_path)
    plan_path, _ = _seed_plan(tmp_path, package, ["未勾任务 1 项"])
    assert _apply(tmp_path, plan_path).returncode == 0
    before = _snapshot(package)
    result = _apply(tmp_path, plan_path)
    assert result.returncode == 2
    assert "stale" in result.stderr
    assert _snapshot(package) == before


def test_refreshed_plan_with_existing_unchecked_id_is_skipped(tmp_path: Path) -> None:
    package = _make_package(tmp_path)
    plan_path, first = _seed_plan(tmp_path, package, ["未勾任务 1 项"])
    assert _apply(tmp_path, plan_path).returncode == 0
    refreshed_path, refreshed = _seed_plan(tmp_path, package, ["未勾任务 1 项"])
    assert refreshed["next"] == "run" and refreshed["tasks"] == []
    refreshed["tasks"] = [first["tasks"][0]]
    refreshed_path.write_text(json.dumps(refreshed), encoding="utf-8")
    before = _snapshot(package)
    result = _apply(tmp_path, refreshed_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["skipped"] == [{"id": first["tasks"][0]["id"], "gap": "未勾任务 1 项"}]
    assert _snapshot(package) == before


def test_plan_reports_merged_gaps(tmp_path: Path) -> None:
    package = _make_package(tmp_path)
    _, payload = _seed_plan(tmp_path, package, [])
    assert payload["merged_gaps"] == []


def test_cap_counts_only_checkfix_rows(tmp_path: Path) -> None:
    spec = (
        "# fixture - 项目范围\n\n<!-- spec:correction-log -->\n### 5.5 修正日志\n"
        "| round | approach | result | new information |\n| --- | --- | --- | --- |\n"
        "| 1 | 人工修正 | FAIL | new output: 手工测试输出 @ 2026-01-01 |\n"
        "| 2 | 人工修正 | FAIL | new output: 手工测试输出 @ 2026-01-02 |\n"
        "| 3 | check 回写钩子 plan/apply | FAIL: 1 gaps 转为 checkfix 任务 | new output: check.json (2026-01-03) |\n"
    )
    _make_package(tmp_path, spec=spec)
    gaps_path = _write_gaps(tmp_path, ["未勾任务 1 项"])
    result = _plan(tmp_path, gaps_path)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["next"] == "apply"
    assert payload["correction_log_round"] == 4
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    for task in payload["tasks"]:
        task["verify"] = "manual: reviewed by main session"
    plan_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    apply_result = _apply(tmp_path, plan_path)
    assert apply_result.returncode == 0, apply_result.stderr
    spec_after = (tmp_path / ".spec" / "specs" / SLUG / "spec.md").read_text(encoding="utf-8")
    assert "| 4 | check 回写钩子 plan/apply |" in spec_after
    sys.path.insert(0, str(SCRIPT.parent))
    from spec_package_support import correction_log_errors

    assert correction_log_errors(spec_after) == []


def test_checked_task_reappearance_gets_round_suffix(tmp_path: Path) -> None:
    package = _make_package(tmp_path)
    plan_path, first = _seed_plan(tmp_path, package, ["未勾任务 1 项"])
    assert _apply(tmp_path, plan_path).returncode == 0
    tasks_path = package / "tasks.md"
    tasks_text = tasks_path.read_text(encoding="utf-8")
    first_id = first["tasks"][0]["id"]
    assert first_id in tasks_text
    tasks_text = tasks_text.replace("- [ ] check 回写修正", "- [x] check 回写修正", 1)
    tasks_path.write_text(tasks_text, encoding="utf-8")
    gaps_path = _write_gaps(tmp_path, ["未勾任务 1 项"])
    second = _plan(tmp_path, gaps_path)
    assert second.returncode == 0, second.stderr
    payload = json.loads(second.stdout)
    assert payload["tasks"] and payload["tasks"][0]["id"] == f"{first_id}-r2"


def test_round_cap_escalates(tmp_path: Path) -> None:
    rows = "\n".join(
        f"| {round_number} | check 回写钩子 plan/apply | FAIL: 1 gaps 转为 checkfix 任务 "
        f"| new output: check.json ({2026 + round_number}-01-01) |"
        for round_number in range(1, 4)
    )
    spec = (
        "# fixture - 项目范围\n\n<!-- spec:correction-log -->\n### 5.5 修正日志\n"
        "| round | approach | result | new information |\n| --- | --- | --- | --- |\n"
        f"{rows}\n"
    )
    package = _make_package(tmp_path, spec=spec)
    before = _snapshot(package)
    gaps_path = _write_gaps(tmp_path, ["未勾任务 1 项"])
    plan_result = _plan(tmp_path, gaps_path)
    assert plan_result.returncode == 3
    assert json.loads(plan_result.stdout)["next"] == "escalate"
    plan_path = tmp_path / "plan.json"
    capped_plan = json.loads(plan_result.stdout)
    for task in capped_plan["tasks"]:
        task["verify"] = "manual: fixture"
    plan_path.write_text(json.dumps(capped_plan), encoding="utf-8")
    apply_result = _apply(tmp_path, plan_path)
    assert apply_result.returncode == 3
    assert json.loads(apply_result.stdout)["next"] == "escalate"
    assert _snapshot(package) == before


def test_apply_rejects_invalid_disposition_without_writes(tmp_path: Path) -> None:
    package = _make_package(tmp_path)
    plan_path, payload = _seed_plan(tmp_path, package, ["未勾任务 1 项"])
    payload["tasks"][0]["disposition"] = "fixed"
    plan_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = _snapshot(package)
    result = _apply(tmp_path, plan_path)
    assert result.returncode == 2
    assert _snapshot(package) == before


def test_apply_rejects_untouched_placeholder_verify(tmp_path: Path) -> None:
    package = _make_package(tmp_path)
    gaps_path = _write_gaps(tmp_path, ["未勾任务 1 项"])
    plan_path = tmp_path / "plan.json"
    plan_result = _run(tmp_path, "plan", "--slug", SLUG, "--gaps", str(gaps_path), "--out", str(plan_path))
    assert plan_result.returncode == 0, plan_result.stderr
    before = _snapshot(package)
    result = _apply(tmp_path, plan_path)
    assert result.returncode == 2
    assert "placeholder" in result.stderr
    assert _snapshot(package) == before


def test_apply_restores_files_when_postwrite_gate_converges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = _make_package(tmp_path)
    plan_path, _ = _seed_plan(tmp_path, package, ["未勾任务 1 项"])
    before = _snapshot(package)
    sys.path.insert(0, str(SCRIPT.parent))
    import check_issue_writeback as module

    monkeypatch.setattr(module, "_gate_converged", lambda root, slug, specs_dir=None: True)
    exit_code = module.main(["apply", "--slug", SLUG, "--root", str(tmp_path), "--plan", str(plan_path)])
    assert exit_code == 2
    assert _snapshot(package) == before


def test_apply_restores_files_when_gate_crashes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = _make_package(tmp_path)
    plan_path, _ = _seed_plan(tmp_path, package, ["未勾任务 1 项"])
    before = _snapshot(package)
    sys.path.insert(0, str(SCRIPT.parent))
    import check_issue_writeback as module

    def broken_gate(root: Path, slug: str, specs_dir=None) -> bool:
        raise module.WritebackError("gate crashed")

    monkeypatch.setattr(module, "_gate_converged", broken_gate)
    exit_code = module.main(["apply", "--slug", SLUG, "--root", str(tmp_path), "--plan", str(plan_path)])
    assert exit_code == 2
    assert _snapshot(package) == before


def test_merged_task_keeps_all_gap_texts(tmp_path: Path) -> None:
    package = _make_package(tmp_path)
    plan_path, payload = _seed_plan(
        tmp_path,
        package,
        ["spec.md:42 缺验证锚点", "spec.md:50 缺验证锚点"],
    )
    assert len(payload["tasks"]) == 1
    assert payload["merged_gaps"] == [
        {"base": payload["tasks"][0]["id"].split("-r")[0], "gaps": ["spec.md:50 缺验证锚点"]}
    ]
    assert _apply(tmp_path, plan_path).returncode == 0
    tasks_text = (package / "tasks.md").read_text(encoding="utf-8")
    assert "未通过项: spec.md:42 缺验证锚点" in tasks_text
    assert "未通过项: spec.md:50 缺验证锚点" in tasks_text


def test_fixed_object_with_unticked_checkfix_stays_unconverged(tmp_path: Path) -> None:
    package = _make_package(tmp_path)
    plan_path, _ = _seed_plan(tmp_path, package, ["未勾任务 1 项"])
    assert _apply(tmp_path, plan_path).returncode == 0
    tasks_path = package / "tasks.md"
    tasks_text = tasks_path.read_text(encoding="utf-8")
    tasks_text = tasks_text.replace("- [ ] 现有任务", "- [x] 现有任务", 1)
    tasks_path.write_text(tasks_text, encoding="utf-8")
    check = subprocess.run(
        [sys.executable, str(CHECK_SCRIPT), "--root", str(tmp_path), "--slug", SLUG, "--format", "json"],
        capture_output=True,
        text=True,
    )
    gate = json.loads(check.stdout)
    assert gate["converged"] is False
    assert any("未勾任务" in gap or "check" in gap for gap in gate["gaps"])


def test_empty_gaps_plan_is_check_green_and_apply_rejected(tmp_path: Path) -> None:
    package = _make_package(tmp_path)
    plan_path, payload = _seed_plan(tmp_path, package, [])
    assert payload["next"] == "check-green"
    assert payload["tasks"] == []
    before = _snapshot(package)
    result = _apply(tmp_path, plan_path)
    assert result.returncode == 2
    assert _snapshot(package) == before


def test_snapshot_matched_dirty_git_tree_allows_apply(tmp_path: Path) -> None:
    package = _make_package(tmp_path, spec=SPEC + f"\n- Git integration branch：spec/{SLUG}\n")

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            capture_output=True,
            text=True,
            check=True,
        )

    git("init", "-q", "-b", f"spec/{SLUG}")
    git("add", ".")
    git("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "init")
    (package / "tasks.md").write_text(TASKS + "\n<!-- dirty -->\n", encoding="utf-8")
    plan_path, _ = _seed_plan(tmp_path, package, ["未勾任务 1 项"])
    before = _snapshot(package)
    result = _apply(tmp_path, plan_path)
    assert result.returncode == 0, result.stderr
    assert (package / "tasks.md").read_bytes().startswith(before["tasks.md"])
    assert "<!-- dirty -->" in (package / "tasks.md").read_text()


def test_end_to_end_with_real_check_output(tmp_path: Path) -> None:
    _make_package(tmp_path)
    check = subprocess.run(
        [sys.executable, str(CHECK_SCRIPT), "--root", str(tmp_path), "--slug", SLUG, "--format", "json"],
        capture_output=True,
        text=True,
    )
    assert check.returncode != 0
    gate = json.loads(check.stdout)
    assert gate["converged"] is False and gate["gaps"]
    check_json = tmp_path / "check.json"
    check_json.write_text(check.stdout, encoding="utf-8")
    plan_path = tmp_path / "plan.json"
    plan_result = _run(tmp_path, "plan", "--slug", SLUG, "--gaps", str(check_json), "--out", str(plan_path))
    assert plan_result.returncode == 0, plan_result.stderr
    plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan_payload["next"] == "apply" and plan_payload["tasks"]
    for task in plan_payload["tasks"]:
        task["verify"] = "manual: reviewed by main session"
    plan_path.write_text(json.dumps(plan_payload, ensure_ascii=False), encoding="utf-8")
    apply_result = _apply(tmp_path, plan_path)
    assert apply_result.returncode == 0, apply_result.stderr
    assert json.loads(apply_result.stdout)["tasks"]
    rerun = subprocess.run(
        [sys.executable, str(CHECK_SCRIPT), "--root", str(tmp_path), "--slug", SLUG, "--format", "json"],
        capture_output=True,
        text=True,
    )
    assert json.loads(rerun.stdout)["converged"] is False


ROW_MARKER_TOKEN = "check 回写钩子"


def test_missing_correction_section_errors(tmp_path: Path) -> None:
    package = _make_package(tmp_path, spec="# fixture - 项目范围\n")
    before = _snapshot(package)
    result = _plan(tmp_path, _write_gaps(tmp_path, ["missing correction log"]))
    assert result.returncode == 2
    assert "correction log" in result.stderr
    assert _snapshot(package) == before


@pytest.mark.parametrize(
    ("gaps", "expected_next"),
    [
        (["未勾任务 1 项"], "apply"),
        ([], "check-green"),
    ],
)
def test_plan_next_action(tmp_path: Path, gaps: list[str], expected_next: str) -> None:
    _make_package(tmp_path)
    _, payload = _seed_plan(tmp_path, tmp_path, gaps)
    assert payload["next"] == expected_next
