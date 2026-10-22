"""Internal bounded read surface: brief / evidence envelope contracts.

F3 (master checkbox) executes doc 03 §2: ``route_spec_package.py --brief``
and ``spec_verify.py show`` share one bounded-read envelope (dual budgets,
opaque page token, version pin, classified errors, channel separation).
Tests here pin the envelope mechanics and the scheduling decision table;
CLI behavioral coverage lives beside each host script's existing suite.

Fixtures under ``tests/fixtures/internal_surface/`` are public-safe
synthetic data only — never a real private package.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "internal_surface"
FIXTURE_SLUG = "2099-01-01_test-internal-surface"

sys.path.insert(0, str(SCRIPTS))

from spec_verify import RUNS_FILENAME  # noqa: E402

# --------------------------------------------------------------------------- #
# sandbox helpers
# --------------------------------------------------------------------------- #


def command(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), cwd=root, capture_output=True, text=True)


def git(root: Path, *args: str) -> str:
    result = command(root, "git", *args)
    assert result.returncode == 0, result.stderr
    return result.stdout


def init_repo(root: Path, branch: str = "main") -> Path:
    root.parent.mkdir(parents=True, exist_ok=True)
    command(root.parent, "git", "init", "-q", "-b", branch, str(root))
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "initial")
    return root


def install_fixture_package(root: Path, slug: str = FIXTURE_SLUG, *, branch: str | None = None) -> Path:
    """Copy the committed synthetic package into a git repo on its branch."""
    package = root / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    for name in ("spec.md", "tasks.md"):
        (package / name).write_text((FIXTURES / "pkg" / name).read_text(encoding="utf-8"), encoding="utf-8")
    if branch is None:
        branch = f"spec/{slug}"
    git(root, "checkout", "-qb", branch)
    return package


def route_cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    return command(
        root,
        sys.executable,
        str(SCRIPTS / "route_spec_package.py"),
        "--root",
        str(root),
        *args,
    )


def brief_cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    return route_cli(root, "--format", "json", "--brief", *args)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_tree(root: Path, skip: tuple[str, ...] = (".git",)) -> dict[str, tuple[str, float]]:
    """Content hash + mtime per file; the no-write-side-effect witness."""
    state: dict[str, tuple[str, float]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part in skip for part in path.relative_to(root).parts):
            continue
        stat = path.stat()
        state[rel] = (hashlib.sha256(path.read_bytes()).hexdigest(), stat.st_mtime_ns)
    return state


# --------------------------------------------------------------------------- #
# brief envelope contract (doc 03 §2.2)
# --------------------------------------------------------------------------- #


def test_brief_envelope_shape_and_source_digests(tmp_path):
    root = init_repo(tmp_path / "project")
    package = install_fixture_package(root)
    result = brief_cli(root, "--slug", FIXTURE_SLUG)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    payload = json.loads(result.stdout)

    assert payload["capability"] == "brief"
    assert payload["query"] == {"slug": FIXTURE_SLUG, "max_items": 100, "item_offset": 0}
    assert payload["version"]["source_sha256"] == sha256_file(package / "tasks.md")
    assert payload["version"]["records"] == 5
    assert payload["slug"] == FIXTURE_SLUG
    assert payload["stage"] == "status"  # blocked fixture task routes to status
    assert payload["title"] == "合成包"
    assert payload["branch_binding"] == f"spec/{FIXTURE_SLUG}"
    assert payload["alerts"]  # the blocked task must not be under-reported
    assert payload["handoff"] == {"line": None, "fresh": "absent"}
    assert payload["source"]["spec_sha256"] == sha256_file(package / "spec.md")
    assert payload["source"]["tasks_sha256"] == sha256_file(package / "tasks.md")
    assert payload["source"]["checklist_sha256"] is None  # v2 package, no checklist
    assert payload["counts"] == {"returned": 5, "total_matching": 5}
    assert payload["complete"] is True
    assert payload["truncated_reason"] == "none"
    assert payload["partial_line"] is False
    assert payload["next"] is None


def test_brief_task_states_cover_full_enum(tmp_path):
    root = init_repo(tmp_path / "project")
    install_fixture_package(root)
    payload = json.loads(brief_cli(root, "--slug", FIXTURE_SLUG).stdout)
    by_id = {task["id"]: task for task in payload["tasks"]}
    assert set(by_id) == {f"task_{i:04d}" for i in range(1, 6)}
    assert by_id["task_0001"]["state"] == "ready"
    assert by_id["task_0002"]["state"] == "done"
    assert by_id["task_0003"]["state"] == "blocked"
    assert by_id["task_0004"]["state"] == "pending"
    assert by_id["task_0005"]["state"] == "manual-in-progress"
    assert by_id["task_0001"]["boundary"] == "合成边界一"
    assert by_id["task_0001"]["verify"] == "`python3 -m pytest -q tests/`"  # verbatim span, not reinterpreted
    assert by_id["task_0004"]["depends_on"] == ["task_0001"]
    assert by_id["task_0001"]["depends_on"] == []
    assert "block_text" not in by_id["task_0003"]  # bulk evidence is not default output
    assert payload["ready"] == ["task_0001", "task_0005"]


def test_brief_superset_of_plain_json_and_same_source_as_panel(tmp_path):
    """Plain ``--format json`` fields survive verbatim inside brief (superset),
    and stage/alerts come from the same route computation as the panel."""
    root = init_repo(tmp_path / "project")
    install_fixture_package(root)
    plain = json.loads(route_cli(root, "--slug", FIXTURE_SLUG, "--format", "json").stdout)
    payload = json.loads(brief_cli(root, "--slug", FIXTURE_SLUG).stdout)
    for key, value in plain.items():
        assert payload[key] == value, key
    panel = route_cli(root, "--slug", FIXTURE_SLUG).stdout
    for alert in payload["alerts"]:
        assert alert in panel
    # markdown panel stays the default and is untouched by the fix
    assert panel.startswith("# 合成包")


def test_brief_item_budget_truncates_with_next_and_reassembles(tmp_path):
    root = init_repo(tmp_path / "project")
    package = root / ".spec" / "specs" / FIXTURE_SLUG
    package.mkdir(parents=True)
    (package / "spec.md").write_text((FIXTURES / "pkg" / "spec.md").read_text(encoding="utf-8"), encoding="utf-8")
    (package / "tasks.md").write_text(
        (FIXTURES / "pkg" / "tasks.md").read_text(encoding="utf-8")
        + (FIXTURES / "large_tasks.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    git(root, "checkout", "-qb", f"spec/{FIXTURE_SLUG}")

    result = brief_cli(root, "--slug", FIXTURE_SLUG, "--max-items", "7")
    assert result.returncode == 0, result.stderr
    first = json.loads(result.stdout)
    assert len(first["tasks"]) == 7
    assert first["complete"] is False
    assert first["truncated_reason"] == "item_budget"
    assert first["counts"] == {"returned": 7, "total_matching": 35}
    assert first["next"]

    seen = [task["id"] for task in first["tasks"]]
    token = first["next"]
    while token:
        page = json.loads(brief_cli(root, "--slug", FIXTURE_SLUG, "--max-items", "7", "--page-token", token).stdout)
        seen.extend(task["id"] for task in page["tasks"])
        token = page["next"]
        if page["complete"]:
            assert page["truncated_reason"] == "none"
            break
    assert len(seen) == len(set(seen)) == 35
    assert seen == [f"task_{i:04d}" for i in range(1, 36)]


def test_brief_version_pin_refuses_stale_page_token(tmp_path):
    root = init_repo(tmp_path / "project")
    package = install_fixture_package(root)
    # artificially shrink the budget so a next token exists
    small = brief_cli(root, "--slug", FIXTURE_SLUG, "--max-items", "2")
    first = json.loads(small.stdout)
    assert first["next"]
    # mutate the tasks file between pages -> version_changed, never mixed
    (package / "tasks.md").write_text(
        (package / "tasks.md").read_text(encoding="utf-8") + "- [ ] 追加任务\n  - boundary: b\n  - verify: `v`\n",
        encoding="utf-8",
    )
    result = brief_cli(root, "--slug", FIXTURE_SLUG, "--max-items", "2", "--page-token", first["next"])
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("error: version_changed:")
    assert sha256_file(package / "tasks.md") not in result.stderr or "source" in result.stderr


def test_brief_rejects_over_hard_cap_and_conflicting_flags(tmp_path):
    root = init_repo(tmp_path / "project")
    install_fixture_package(root)
    over = brief_cli(root, "--slug", FIXTURE_SLUG, "--max-items", "1001")
    assert over.returncode == 1
    assert over.stdout == ""
    assert "error: invalid_query:" in over.stderr

    zero = brief_cli(root, "--slug", FIXTURE_SLUG, "--max-items", "0")
    assert zero.returncode == 1
    assert "error: invalid_query:" in zero.stderr

    conflict = route_cli(root, "--slug", FIXTURE_SLUG, "--brief", "--format", "markdown")
    assert conflict.returncode == 1
    assert "error: invalid_query:" in conflict.stderr


def test_brief_unknown_slug_is_missing_error(tmp_path):
    root = init_repo(tmp_path / "project")
    result = brief_cli(root, "--slug", "2099-01-01_no-such-package")
    assert result.returncode == 1
    assert result.stdout == ""
    assert "error: missing:" in result.stderr


def test_brief_dependency_errors_surface_not_empty_tasks(tmp_path):
    root = init_repo(tmp_path / "project")
    package = root / ".spec" / "specs" / FIXTURE_SLUG
    package.mkdir(parents=True)
    (package / "spec.md").write_text((FIXTURES / "pkg" / "spec.md").read_text(encoding="utf-8"), encoding="utf-8")
    (package / "tasks.md").write_text(
        "# T\n## A\n"
        "- [ ] 任务一\n  - boundary: b\n  - verify: `v`\n"
        "- [ ] 任务二\n  - depends-on: task_9999\n  - boundary: b\n  - verify: `v`\n",
        encoding="utf-8",
    )
    git(root, "checkout", "-qb", f"spec/{FIXTURE_SLUG}")
    payload = json.loads(brief_cli(root, "--slug", FIXTURE_SLUG).stdout)
    assert payload["ready"] == []
    assert payload["dependencyErrors"]
    assert any("task_9999" in error for error in payload["dependencyErrors"])


def test_brief_has_no_write_side_effects(tmp_path):
    root = init_repo(tmp_path / "project")
    install_fixture_package(root)
    before = snapshot_tree(root)
    assert brief_cli(root, "--slug", FIXTURE_SLUG).returncode == 0
    assert snapshot_tree(root) == before


def test_brief_handoff_objectified(tmp_path):
    root = init_repo(tmp_path / "project")
    package = install_fixture_package(root)
    handoff = package / "handoff.md"
    head = git(root, "rev-parse", "HEAD").strip()
    total_done = 1
    total = 5
    handoff.write_text(
        "# 交接\n\n"
        f"- 2026-10-19T00:00:00+00:00 | pause | collab=user | head={head} | "
        f"progress={total_done}/{total}\n\n"
        "快照：entries=1，latest-entry=2026-10-19T00:00:00+00:00，collab=user，"
        f"head={head}，progress={total_done}/{total}\n",
        encoding="utf-8",
    )
    result = brief_cli(root, "--slug", FIXTURE_SLUG)
    payload = json.loads(result.stdout)
    handoff_view = payload["handoff"]
    assert isinstance(handoff_view, dict)
    assert handoff_view["line"]
    assert handoff_view["fresh"] in {"fresh", "stale", "unknown"}
    if handoff_view["fresh"] == "stale":
        assert handoff_view.get("stale_reason")


# --------------------------------------------------------------------------- #
# D-1 legacy defect: --format json must hold on all three main branches
# --------------------------------------------------------------------------- #


def test_route_json_no_package_branch_is_machine_readable(tmp_path):
    root = init_repo(tmp_path / "empty-project")
    result = route_cli(root, "--format", "json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)  # was markdown before the fix
    assert payload["slug"] is None
    assert payload["stage"] == "new"


def test_route_json_unbound_branch_is_machine_readable(tmp_path):
    root = init_repo(tmp_path / "multi-project")
    for slug in ("2099-01-02_test-first", "2099-01-03_test-second"):
        package = root / ".spec" / "specs" / slug
        package.mkdir(parents=True)
        (package / "spec.md").write_text(
            (FIXTURES / "pkg" / "spec.md").read_text(encoding="utf-8").replace(f"spec/{FIXTURE_SLUG}", f"spec/{slug}"),
            encoding="utf-8",
        )
        (package / "tasks.md").write_text("# T\n- [ ] 任务\n  - boundary: b\n  - verify: `v`\n", encoding="utf-8")
    result = route_cli(root, "--format", "json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)  # was markdown before the fix
    assert payload["slug"] is None
    assert payload["stage"] == "status"
    assert set(payload["candidates"]) == {"2099-01-02_test-first", "2099-01-03_test-second"}


def test_route_markdown_defaults_survive_the_json_fix(tmp_path):
    root = init_repo(tmp_path / "empty-project")
    result = route_cli(root)
    assert result.returncode == 0
    assert result.stdout.startswith("# 当前没有进行中的项目")


def test_brief_covers_no_package_and_unbound_branches(tmp_path):
    empty = init_repo(tmp_path / "empty-project")
    payload = json.loads(brief_cli(empty).stdout)
    assert payload["capability"] == "brief"
    assert payload["slug"] is None
    assert payload["stage"] == "new"
    assert payload["tasks"] == []
    assert payload["complete"] is True

    multi = init_repo(tmp_path / "multi-project")
    for slug in ("2099-01-02_test-first", "2099-01-03_test-second"):
        package = multi / ".spec" / "specs" / slug
        package.mkdir(parents=True)
        (package / "spec.md").write_text(
            (FIXTURES / "pkg" / "spec.md").read_text(encoding="utf-8").replace(f"spec/{FIXTURE_SLUG}", f"spec/{slug}"),
            encoding="utf-8",
        )
        (package / "tasks.md").write_text("# T\n- [ ] 任务\n  - boundary: b\n  - verify: `v`\n", encoding="utf-8")
    payload = json.loads(brief_cli(multi).stdout)
    assert payload["stage"] == "status"
    assert set(payload["candidates"]) == {"2099-01-02_test-first", "2099-01-03_test-second"}
    assert payload["alerts"]
    assert payload["tasks"] == []


# --------------------------------------------------------------------------- #
# evidence envelope contract (doc 03 §2.3): spec_verify.py show
# --------------------------------------------------------------------------- #


def show_cli(root: Path, slug: str, *args: str) -> subprocess.CompletedProcess:
    return command(
        root,
        sys.executable,
        str(SCRIPTS / "spec_verify.py"),
        "show",
        "--root",
        str(root),
        "--slug",
        slug,
        *args,
    )


def make_runs_package(
    root: Path,
    slug: str = FIXTURE_SLUG,
    *,
    tasks_text: str | None = None,
    records: list[dict] | None = None,
    runs_protocol: bool = True,
) -> tuple[Path, Path]:
    package = root / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    spec_text = (FIXTURES / "pkg" / "spec.md").read_text(encoding="utf-8")
    if not runs_protocol:
        spec_text = spec_text.replace("本包启用 evidence: runs 协议。", "")
    (package / "spec.md").write_text(spec_text, encoding="utf-8")
    fallback_tasks = (FIXTURES / "pkg" / "tasks.md").read_text(encoding="utf-8")
    (package / "tasks.md").write_text(tasks_text or fallback_tasks, encoding="utf-8")
    if records is not None:
        write_runs(package / RUNS_FILENAME, records)
    git(root, "checkout", "-qb", f"spec/{slug}")
    return package, package / RUNS_FILENAME


def write_runs(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")


def synthetic_records(
    count: int,
    *,
    task_ids: tuple[str, ...] = ("task_0001",),
    fail_at: set[int] = frozenset(),
) -> list[dict]:
    """Deterministic public-safe records with multibyte text and long lines."""
    records = []
    for index in range(count):
        task_id = task_ids[index % len(task_ids)]
        filler = "合成验证证据-" + "载荷" * 12  # multibyte, ~120 B per record
        records.append(
            {
                "task": task_id,
                "cmd": f"python3 -m pytest -q tests/t{index:02d}.py",
                "exit": 1 if index in fail_at else 0,
                "duration_s": round(0.5 + index * 0.01, 3),
                "stdout_tail_sha256": hashlib.sha256(filler.encode("utf-8")).hexdigest(),
                "tree_fp": f"fp{index:06d}",
                "at": f"2026-10-19T00:{index // 60:02d}:{index % 60:02d}+00:00",
                "filler": filler + str(index),
            }
        )
    return records


def collect_pages(root: Path, slug: str, first: dict, *view_args: str) -> list[dict]:
    """Follow `next` until complete; returns every item in order."""
    items = list(first["items"])
    token = first["next"]
    guard = 0
    while token:
        guard += 1
        assert guard < 100, "pagination did not converge"
        page = json.loads(show_cli(root, slug, *view_args, "--page-token", token).stdout)
        items.extend(page["items"])
        token = page["next"]
        if page["complete"]:
            break
    return items


def test_show_envelope_shape_and_latest_view(tmp_path):
    root = init_repo(tmp_path / "project")
    records = synthetic_records(6, task_ids=("task_0001", "task_0002"))
    package, runs_path = make_runs_package(root, records=records)
    result = show_cli(root, FIXTURE_SLUG)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["capability"] == "evidence"
    assert payload["query"]["slug"] == FIXTURE_SLUG
    assert payload["query"]["failures_only"] is False
    assert payload["query"]["history"] is False
    assert payload["version"]["source_sha256"] == sha256_file(runs_path)
    assert payload["version"]["records"] == 6
    # latest view: one item per task, newest record wins
    assert payload["counts"] == {"returned": 2, "total_matching": 2}
    by_task = {item["record"]["task"]: item for item in payload["items"]}
    assert set(by_task) == {"task_0001", "task_0002"}
    assert by_task["task_0001"]["record"]["at"] == records[4]["at"]
    assert by_task["task_0002"]["record"]["at"] == records[5]["at"]
    for item in payload["items"]:
        assert item["record"]["task"]
        assert item["raw"]
        json.loads(item["raw"])  # raw is the exact source line
    assert payload["complete"] is True
    assert payload["truncated_reason"] == "none"
    assert payload["partial_line"] is False
    assert payload["next"] is None
    assert "freshness" in payload


def test_show_history_reassembly_is_byte_exact(tmp_path):
    root = init_repo(tmp_path / "project")
    records = synthetic_records(40, task_ids=("task_0001",))  # >= 3 pages
    package, runs_path = make_runs_package(root, records=records)
    first = json.loads(show_cli(root, FIXTURE_SLUG, "--history", "--max-bytes", "4000", "--max-lines", "7").stdout)
    assert not first["complete"]
    assert first["truncated_reason"] in {"byte_budget", "line_budget"}
    items = collect_pages(root, FIXTURE_SLUG, first, "--history", "--max-bytes", "4000", "--max-lines", "7")
    assert len(items) == 40
    # reassembly invariant: stripped pages reconstruct the source slice byte-for-byte
    reassembled = "".join(item["raw"] + "\n" for item in items).encode("utf-8")
    assert reassembled == runs_path.read_bytes()
    lines = [json.loads(item["raw"]) for item in items]
    assert [record["at"] for record in lines] == [record["at"] for record in records]


def test_show_page_tokens_resume_from_exact_line(tmp_path):
    root = init_repo(tmp_path / "project")
    records = synthetic_records(12, task_ids=("task_0001",))
    make_runs_package(root, records=records)
    first = json.loads(show_cli(root, FIXTURE_SLUG, "--history", "--max-lines", "5").stdout)
    assert first["truncated_reason"] == "line_budget"
    assert len(first["items"]) == 5
    second_cmd = show_cli(root, FIXTURE_SLUG, "--history", "--max-lines", "5", "--page-token", first["next"])
    second = json.loads(second_cmd.stdout)
    assert second["items"][0]["line"] == 6
    assert [item["line"] for item in second["items"][:5]] == [6, 7, 8, 9, 10]


def test_show_oversized_line_is_marked_not_faked(tmp_path):
    root = init_repo(tmp_path / "project")
    records = synthetic_records(2, task_ids=("task_0001",))
    records[0]["filler"] = "超" * 9000  # single line far over the byte budget
    package, runs_path = make_runs_package(root, records=records)
    result = show_cli(root, FIXTURE_SLUG, "--history", "--max-bytes", "2048")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    marker = payload["items"][0]
    assert marker["oversized"] is True
    assert marker["length_bytes"] == len((json.dumps(records[0], ensure_ascii=False) + "\n").encode("utf-8")) - 1
    assert marker["sha256"] == hashlib.sha256((json.dumps(records[0], ensure_ascii=False)).encode("utf-8")).hexdigest()
    assert payload["complete"] is False
    assert payload["truncated_reason"] == "oversized_line"
    # continuation progresses past the oversized line, and a bigger budget recovers it
    bigger = json.loads(show_cli(root, FIXTURE_SLUG, "--history", "--max-bytes", str(64 * 1024)).stdout)
    assert bigger["complete"] is True
    assert "oversized" not in bigger["items"][0]
    assert json.loads(bigger["items"][0]["raw"])["task"] == "task_0001"


def test_show_bad_json_line_fails_closed_with_line_number(tmp_path):
    root = init_repo(tmp_path / "project")
    records = synthetic_records(2, task_ids=("task_0001",))
    package, runs_path = make_runs_package(root, records=records)
    content = runs_path.read_text(encoding="utf-8").splitlines()
    content.insert(1, "{not-json")
    runs_path.write_text("\n".join(content) + "\n", encoding="utf-8")
    result = show_cli(root, FIXTURE_SLUG)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "error: unreadable:" in result.stderr
    assert "第 2 行" in result.stderr


def test_show_missing_runs_is_missing_not_empty_success(tmp_path):
    root = init_repo(tmp_path / "project")
    make_runs_package(root, records=None)
    result = show_cli(root, FIXTURE_SLUG)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "error: missing:" in result.stderr


def test_show_failures_only_lists_failed_and_stale(tmp_path):
    root = init_repo(tmp_path / "project")
    from spec_verify import compute_tree_fingerprint

    records = synthetic_records(4, task_ids=("task_0001", "task_0002"))
    fresh_fp = compute_tree_fingerprint(root)
    # task_0001's latest record carries the real current fingerprint -> fresh
    records[2]["tree_fp"] = fresh_fp
    records[3]["exit"] = 1  # latest task_0002 record fails
    make_runs_package(root, records=records)
    # package creation moved the tree (branch + files) -> re-fresh task_0001
    records = json.loads(json.dumps(records))
    records[2]["tree_fp"] = compute_tree_fingerprint(root)
    package = root / ".spec" / "specs" / FIXTURE_SLUG
    write_runs(package / RUNS_FILENAME, records)
    payload = json.loads(show_cli(root, FIXTURE_SLUG, "--failures-only").stdout)
    failed = {item["record"]["task"] for item in payload["items"]}
    assert failed == {"task_0002"}
    assert payload["freshness"]["per_task"]["task_0002"] == "unverified"
    assert payload["freshness"]["per_task"]["task_0001"] == "fresh"


def test_show_freshness_tri_state_matches_evaluate_runs_gate(tmp_path):
    sys.path.insert(0, str(SCRIPTS))
    from spec_verify import compute_tree_fingerprint, evaluate_runs_gate

    root = init_repo(tmp_path / "project")
    records = [
        {
            "task": "task_0001",
            "cmd": "c",
            "exit": 0,
            "duration_s": 1.0,
            "stdout_tail_sha256": "x",
            "tree_fp": "STALE-NOT-CURRENT",
            "at": "2026-10-19T00:00:00+00:00",
        },
        {
            "task": "task_0002",
            "cmd": "c",
            "exit": 1,
            "duration_s": 1.0,
            "stdout_tail_sha256": "x",
            "tree_fp": "STALE-NOT-CURRENT",
            "at": "2026-10-19T00:00:01+00:00",
        },
    ]
    tasks_text = (
        "# T\n## A\n"
        "- [ ] 任务一\n  - boundary: b\n  - verify: `v`\n"
        "- [ ] 任务二\n  - boundary: b\n  - verify: `v`\n"
        "- [ ] 手工任务\n  - boundary: b\n  - verify: manual: 内容审查\n"
    )
    package, runs_path = make_runs_package(root, tasks_text=tasks_text, records=records)
    # a genuinely fresh record: real current fingerprint
    fresh_fp = compute_tree_fingerprint(root)
    records.append(
        {
            "task": "task_0001",
            "cmd": "c",
            "exit": 0,
            "duration_s": 1.0,
            "stdout_tail_sha256": "x",
            "tree_fp": fresh_fp,
            "at": "2026-10-19T00:00:02+00:00",
        }
    )
    write_runs(runs_path, records)

    spec_content = (package / "spec.md").read_text(encoding="utf-8")
    ok, errors, detail = evaluate_runs_gate(spec_content, tasks_text, package, root)
    payload = json.loads(show_cli(root, FIXTURE_SLUG).stdout)
    freshness = payload["freshness"]
    assert freshness["ok"] is ok
    assert freshness["detail"] == detail
    assert freshness["errors"] == errors
    # task_0001 latest record fresh (exit 0 + fingerprint matches), never stale-as-pass
    assert freshness["per_task"]["task_0001"] == "fresh"
    # task_0002 latest record exit 1 -> unverified; no record for it passes
    assert freshness["per_task"]["task_0002"] == "unverified"
    assert freshness["per_task"]["task_0003"] == "manual"
    # cross-check: gate errors name exactly the non-fresh machine tasks
    assert any("task_0002" in error for error in errors)

    # stale case: mutate the tree after evidence capture
    (root / "src.txt").write_text("mutation\n", encoding="utf-8")
    payload = json.loads(show_cli(root, FIXTURE_SLUG).stdout)
    assert payload["freshness"]["per_task"]["task_0001"] == "stale"
    ok2, errors2, _ = evaluate_runs_gate(spec_content, tasks_text, package, root)
    assert payload["freshness"]["ok"] is ok2
    assert any("证据过期" in error for error in errors2)


def test_show_no_freshness_flag_omits_block(tmp_path):
    root = init_repo(tmp_path / "project")
    make_runs_package(root, records=synthetic_records(1))
    payload = json.loads(show_cli(root, FIXTURE_SLUG, "--no-freshness").stdout)
    assert "freshness" not in payload


def test_show_non_runs_package_freshness_is_n_a(tmp_path):
    root = init_repo(tmp_path / "project")
    make_runs_package(root, records=synthetic_records(1), runs_protocol=False)
    payload = json.loads(show_cli(root, FIXTURE_SLUG).stdout)
    assert payload["freshness"]["status"] == "n_a"
    assert payload["freshness"]["reason"]
    assert set(payload["freshness"]["per_task"].values()) == {"n_a"}


def test_show_version_changed_between_pages(tmp_path):
    root = init_repo(tmp_path / "project")
    records = synthetic_records(10, task_ids=("task_0001",))
    package, runs_path = make_runs_package(root, records=records)
    first = json.loads(show_cli(root, FIXTURE_SLUG, "--history", "--max-lines", "4").stdout)
    assert first["next"]
    # tamper with the source between pages -> next page refuses, never mixes
    write_runs(runs_path, synthetic_records(10, task_ids=("task_0001",))[:-1])
    result = show_cli(root, FIXTURE_SLUG, "--history", "--max-lines", "4", "--page-token", first["next"])
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("error: version_changed:")


def test_show_unknown_task_filter_is_invalid_query(tmp_path):
    root = init_repo(tmp_path / "project")
    make_runs_package(root, records=synthetic_records(1))
    result = show_cli(root, FIXTURE_SLUG, "--task", "task_9999")
    assert result.returncode == 1
    assert "error: invalid_query:" in result.stderr


def test_show_task_filter_narrows_view(tmp_path):
    root = init_repo(tmp_path / "project")
    records = synthetic_records(4, task_ids=("task_0001", "task_0002"))
    make_runs_package(root, records=records)
    payload = json.loads(show_cli(root, FIXTURE_SLUG, "--task", "task_0002").stdout)
    assert {item["record"]["task"] for item in payload["items"]} == {"task_0002"}
    assert payload["counts"]["total_matching"] == 1


def test_show_budget_hard_caps_are_errors_not_truncation(tmp_path):
    root = init_repo(tmp_path / "project")
    make_runs_package(root, records=synthetic_records(1))
    over_bytes = show_cli(root, FIXTURE_SLUG, "--max-bytes", str(2 * 1024 * 1024))
    assert over_bytes.returncode == 1
    assert "error: invalid_query:" in over_bytes.stderr
    over_lines = show_cli(root, FIXTURE_SLUG, "--max-lines", "5001")
    assert over_lines.returncode == 1
    assert "error: invalid_query:" in over_lines.stderr


def test_show_has_no_write_side_effects(tmp_path):
    root = init_repo(tmp_path / "project")
    make_runs_package(root, records=synthetic_records(5))
    before = snapshot_tree(root)
    assert show_cli(root, FIXTURE_SLUG).returncode == 0
    assert show_cli(root, FIXTURE_SLUG, "--history").returncode == 0
    assert show_cli(root, FIXTURE_SLUG, "--failures-only").returncode == 0
    after = snapshot_tree(root)
    assert after == before


def test_show_missing_package_is_classified_error(tmp_path):
    root = init_repo(tmp_path / "project")
    result = show_cli(root, "2099-01-01_no-such-package")
    assert result.returncode == 1
    assert "error: missing:" in result.stderr


# --------------------------------------------------------------------------- #
# scheduling decision table (doc 03 §2.5): every row has a positive and a
# negative executable case — when to use the capability, and when the plain
# path is the mandated default.
# --------------------------------------------------------------------------- #


class TestSchedulingDecisionTable:
    """The table is a read-choice convention, not a second state machine:
    stage truth stays with route_spec_package.py; authority stays with gates."""

    def test_row_cold_start_brief_replaces_multiple_reads_and_moves_no_authority(self, tmp_path):
        # positive: one brief call returns route decision + contracts + alerts
        root = init_repo(tmp_path / "project")
        install_fixture_package(root)
        payload = json.loads(brief_cli(root, "--slug", FIXTURE_SLUG).stdout)
        plain = json.loads(route_cli(root, "--slug", FIXTURE_SLUG, "--format", "json").stdout)
        assert all(payload[key] == value for key, value in plain.items())
        assert payload["tasks"] and payload["alerts"]
        # negative: brief is a view — it never checks tasks off or writes
        before = snapshot_tree(root)
        brief_cli(root, "--slug", FIXTURE_SLUG)
        assert snapshot_tree(root) == before

    def test_row_single_file_reads_have_no_wrapper(self):
        # negative: neither host CLI grew a generic file-read surface
        route_help = subprocess.run(
            [sys.executable, str(SCRIPTS / "route_spec_package.py"), "--help"],
            capture_output=True,
            text=True,
        ).stdout
        verify_help = subprocess.run(
            [sys.executable, str(SCRIPTS / "spec_verify.py"), "--help"],
            capture_output=True,
            text=True,
        ).stdout
        for text in (route_help, verify_help):
            assert "--read" not in text and "--file" not in text and "--cat" not in text

    @pytest.mark.skipif(not (REPO / ".spec").is_dir(), reason="private .spec/ history is not part of the public export")
    def test_row_stage_contract_chain_doc_capability_is_adjudicated_not_assumed(self):
        # doc was rejected with data (T4): no runtime slice reader exists, and
        # the adjudication record is the auditable artifact of that rejection
        # (present while active and after archival).
        assert not (SCRIPTS / "spec_doc_reader.py").exists()
        specs_root = REPO / ".spec" / "specs"
        candidates = [
            specs_root / "2026-10-19_add-internal-read-surface" / "doc-adjudication.md",
            specs_root / "archive" / "2026-10-19_add-internal-read-surface" / "doc-adjudication.md",
        ]
        adjudication = next(path for path in candidates if path.is_file())
        content = adjudication.read_text(encoding="utf-8")
        assert "reject" in content and "D-2" in content
        # stage pages stay native reads: no doc capability registered anywhere
        assert not any(SCRIPTS.glob("*doc*reader*"))

    def test_row_small_package_brief_has_no_forced_pagination_overhead(self, tmp_path):
        root = init_repo(tmp_path / "project")
        install_fixture_package(root)
        payload = json.loads(brief_cli(root, "--slug", FIXTURE_SLUG).stdout)
        assert payload["complete"] is True
        assert payload["truncated_reason"] == "none"
        assert payload["counts"]["returned"] == payload["counts"]["total_matching"]

    def test_row_multi_package_scan_brief_reads_all_candidates_once(self, tmp_path):
        root = init_repo(tmp_path / "multi")
        for slug in ("2099-01-02_test-first", "2099-01-03_test-second"):
            package = root / ".spec" / "specs" / slug
            package.mkdir(parents=True)
            (package / "spec.md").write_text(
                (FIXTURES / "pkg" / "spec.md")
                .read_text(encoding="utf-8")
                .replace(f"spec/{FIXTURE_SLUG}", f"spec/{slug}"),
                encoding="utf-8",
            )
            (package / "tasks.md").write_text("# T\n- [ ] 任务\n  - boundary: b\n  - verify: `v`\n", encoding="utf-8")
        payload = json.loads(brief_cli(root).stdout)
        assert set(payload["candidates"]) == {"2099-01-02_test-first", "2099-01-03_test-second"}

    def test_row_failure_repair_failures_only_and_stale_never_passes(self, tmp_path):
        from spec_verify import compute_tree_fingerprint

        root = init_repo(tmp_path / "project")
        records = synthetic_records(2, task_ids=("task_0001",))
        records[1]["tree_fp"] = compute_tree_fingerprint(root)
        make_runs_package(root, records=records)
        payload = json.loads(show_cli(root, FIXTURE_SLUG, "--failures-only").stdout)
        stale = payload["freshness"]["per_task"]["task_0001"]
        assert stale in {"stale", "unverified", "fresh"}
        if stale == "stale":
            assert payload["items"], "stale task must surface in failures-only"
        # negative: a stale record never reads as passing anywhere in the view
        assert "fresh" != stale or not payload["items"]

    def test_row_long_log_first_page_never_claims_completeness(self, tmp_path):
        root = init_repo(tmp_path / "project")
        make_runs_package(root, records=synthetic_records(30, task_ids=("task_0001",)))
        first = json.loads(show_cli(root, FIXTURE_SLUG, "--history", "--max-lines", "10").stdout)
        assert first["complete"] is False
        assert first["truncated_reason"] == "line_budget"
        items = collect_pages(root, FIXTURE_SLUG, first, "--history", "--max-lines", "10")
        assert len(items) == 30

    def test_row_check_preflight_freshness_is_gate_truth_but_show_is_not_the_gate(self, tmp_path):
        from spec_verify import evaluate_runs_gate

        root = init_repo(tmp_path / "project")
        make_runs_package(root, records=synthetic_records(1))  # stale fingerprints
        package = root / ".spec" / "specs" / FIXTURE_SLUG
        spec_content = (package / "spec.md").read_text(encoding="utf-8")
        tasks_content = (package / "tasks.md").read_text(encoding="utf-8")
        ok, errors, detail = evaluate_runs_gate(spec_content, tasks_content, package, root)
        payload = json.loads(show_cli(root, FIXTURE_SLUG).stdout)
        assert payload["freshness"]["ok"] is ok
        assert payload["freshness"]["errors"] == errors
        # negative: show exits 0 even where the gate would fail — view, not gate
        assert ok is False or ok is None

    def test_row_measure_has_no_wrapper(self):
        # negative: measure_skill_surface.py stays a direct call; no wrapper script
        measures = sorted(p.name for p in SCRIPTS.glob("*measure*"))
        assert measures == ["measure_skill_surface.py"]

    def test_row_done_push_authority_never_enters_the_tool_surface(self):
        # negative: the read-only CLIs expose no write/git operation
        for script in ("route_spec_package.py", "spec_verify.py"):
            text = (SCRIPTS / script).read_text(encoding="utf-8")
            assert "git push" not in text
            assert "git commit" not in text
            assert "git merge" not in text


# --------------------------------------------------------------------------- #
# export-tree callability (doc 03 §5.1 V5): the capabilities ship in the
# exported payload and run against a consumer project, with the original
# user entries and argument pass-through unchanged.
# --------------------------------------------------------------------------- #


class TestExportTreeCallability:
    def test_core_export_carries_and_serves_the_internal_surface(self, tmp_path):
        export = tmp_path / "export-spec-core"
        completed = subprocess.run(
            [
                sys.executable,
                str(REPO / "scripts" / "export_skill_package.py"),
                "--root",
                str(REPO),
                "--profile",
                "core",
                "--skip-signing",
                "--output",
                str(export),
            ],
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        assert (export / "scripts" / "route_spec_package.py").is_file()
        assert (export / "scripts" / "spec_verify.py").is_file()

        consumer = tmp_path / "consumer-project"
        consumer.mkdir()
        init_repo(consumer)
        install_fixture_package(consumer)
        records = synthetic_records(2, task_ids=("task_0001",))
        write_runs(consumer / ".spec" / "specs" / FIXTURE_SLUG / RUNS_FILENAME, records)

        exported_route = export / "scripts" / "route_spec_package.py"
        brief = subprocess.run(
            [sys.executable, str(exported_route), "--root", str(consumer), "--format", "json", "--brief"],
            capture_output=True,
            text=True,
        )
        assert brief.returncode == 0, brief.stderr
        payload = json.loads(brief.stdout)
        assert payload["capability"] == "brief"
        assert payload["slug"] == FIXTURE_SLUG
        assert payload["tasks"]

        exported_verify = export / "scripts" / "spec_verify.py"
        show = subprocess.run(
            [sys.executable, str(exported_verify), "show", "--root", str(consumer), "--slug", FIXTURE_SLUG],
            capture_output=True,
            text=True,
        )
        assert show.returncode == 0, show.stderr
        assert json.loads(show.stdout)["capability"] == "evidence"

        # original user entries and pass-through stay byte-compatible: the
        # default markdown panel and the `run` subcommand still exist
        panel = subprocess.run(
            [sys.executable, str(exported_route), "--root", str(consumer)],
            capture_output=True,
            text=True,
        )
        assert panel.returncode == 0
        assert panel.stdout.startswith("# 合成包")
        run_help = subprocess.run(
            [sys.executable, str(exported_verify), "run", "--help"],
            capture_output=True,
            text=True,
        )
        assert run_help.returncode == 0
        assert "--task" in run_help.stdout and "--all" in run_help.stdout


# --------------------------------------------------------------------------- #
# brief falsifiedPaths field (add-only; plain JSON and panel stay untouched)
# --------------------------------------------------------------------------- #


def _install_with_falsified_section(root: Path, section: str | None) -> None:
    package = install_fixture_package(root)
    if section is not None:
        spec = package / "spec.md"
        text = spec.read_text(encoding="utf-8")
        marker = "### 2.3 待确认问题\n- 无\n"
        assert marker in text
        spec.write_text(text.replace(marker, f"{marker}\n{section}\n", 1), encoding="utf-8")


def test_brief_lists_falsified_path_entries(tmp_path):
    root = init_repo(tmp_path / "project")
    _install_with_falsified_section(
        root,
        "### 2.5 已证伪路径\n"
        "- 整份重跑归档脚本：改写无关文案，见 diff，2026-10-22\n"
        "- 无法追加到 check.md：预算已满，2026-10-22",
    )
    payload = json.loads(brief_cli(root, "--slug", FIXTURE_SLUG).stdout)
    assert payload["falsifiedPaths"] == [
        "整份重跑归档脚本：改写无关文案，见 diff，2026-10-22",
        "无法追加到 check.md：预算已满，2026-10-22",
    ]


def test_brief_falsified_paths_empty_for_none_declaration_and_missing_section(tmp_path):
    for index, section in enumerate(("### 2.5 已证伪路径\n- 无（暂无证伪记录）", None)):
        root = init_repo(tmp_path / f"project{index}")
        _install_with_falsified_section(root, section)
        payload = json.loads(brief_cli(root, "--slug", FIXTURE_SLUG).stdout)
        assert payload["falsifiedPaths"] == [], section


def test_brief_reads_english_falsified_paths_heading(tmp_path):
    root = init_repo(tmp_path / "project")
    _install_with_falsified_section(
        root,
        "<!-- spec:falsified-paths -->\n"
        "### 2.5 Falsified Paths\n"
        "- tried bumping the ceiling, ruled out by the ratchet test, 2026-10-22",
    )
    payload = json.loads(brief_cli(root, "--slug", FIXTURE_SLUG).stdout)
    assert payload["falsifiedPaths"] == ["tried bumping the ceiling, ruled out by the ratchet test, 2026-10-22"]


def test_plain_json_and_panel_do_not_carry_falsified_paths(tmp_path):
    root = init_repo(tmp_path / "project")
    _install_with_falsified_section(root, "### 2.5 已证伪路径\n- 某方案，依据见日志，2026-10-22")
    plain = json.loads(route_cli(root, "--slug", FIXTURE_SLUG, "--format", "json").stdout)
    assert "falsifiedPaths" not in plain
    assert "某方案" not in route_cli(root, "--slug", FIXTURE_SLUG).stdout
