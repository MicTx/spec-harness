from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import chain_session  # noqa: E402
import chain_spawn_support as support  # noqa: E402
from chain_spawn_support import ChainSpawnError  # noqa: E402

OWNER_A = "owner.alpha-1"
OWNER_B = "owner.beta-2"
OWNER_HASH_A = hashlib.sha256(OWNER_A.encode("utf-8")).hexdigest()

KNOWN_CLIENTS = [
    "claude",
    "claude-desktop",
    "codex",
    "cursor",
    "devin",
    "gemini",
    "grok",
    "hermes",
    "opencode",
    "openclaw",
    "pi",
    "zcode",
]
GENERIC_CLIENT = "acme-agent_9.x"

requires_bash = pytest.mark.skipif(
    subprocess.run(["bash", "--version"], capture_output=True).returncode != 0 or not Path("/bin/bash").exists(),
    reason="bash not available",
)
requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")

MASTER_REL = ".spec/plans/2026-01-02_demo/master.md"
DETAIL_REL = ".spec/plans/2026-01-02_demo/01-alpha.md"

MASTER_SEED = "# Demo master\n\n## Goal\n\n- build the demo\n"
MASTER_EXPANDED = (
    "# Demo master\n\n## Goal\n\n- build the demo\n\n"
    "- [ ] Feature alpha\n  - boundary: alpha\n  - verify: check\n"
    "- [ ] Feature beta\n  - boundary: beta\n  - verify: check\n\n"
    "- [alpha detail](01-alpha.md)\n"
)
DETAIL_DOC = (
    "# Alpha detail\n\n"
    "## Business Logic\n- logic\n\n"
    "## Data Model\n- model\n\n"
    "## Data Flow\n- flow\n\n"
    "## Boundary\n- boundary\n\n"
    "## Acceptance\n- hook\n"
)

PACKAGE_SPEC = """\
# Pkg - 项目范围

## 1. 问题定义
- **项目目标**：验证链恢复
- **目标用户**：维护者
- **核心价值**：状态可信

## 2. 假设与待确认
### 2.1 已确认事实
- 已确认
### 2.2 关键假设
- 使用标准库
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- 不改业务代码
## 4. 最小实现路径
- 复用 helper
### 6.1 行为成效指标
- metric <= 1 -> verify: diff
- Git integration branch：`{branch}`
"""
TASKS_TODO = "- [ ] Build\n  - boundary: scope\n  - verify: check\n"
TASKS_DONE = "- [x] Build\n  - boundary: scope\n  - verify: pytest\n"
CHECKLIST_DONE = """\
## 跨载体一致性
- [x] Consistent
## 行为成效
- [x] Metrics
## 验收证据
- 脚本验证：pytest
**验收结果**：通过
"""
SUMMARY_V1 = """# Archived - 完成总结
## 交付结论
- 完成
## 假设回顾
- verified
## 交付范围
- delivered
## 简化决策
- simple
## 变更边界
- scripts
## 验证证据
- pytest
## 门禁证据
- passed
## 问题处置
```json
{"version": 1, "issues": []}
```
"""

PLAN_DOC_REL = ".spec/plans/2026-02-03_build/master.md"
PLAN_DOC = (
    "# Build master\n\n## Goal\n\n- ship it\n\n"
    "- [ ] First package\n  - boundary: p1\n  - verify: check\n"
    "- [ ] Second package\n  - boundary: p2\n  - verify: check\n"
)


def git_init(root: Path, branch: str = "main") -> None:
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=root, check=True)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def summary_path(root: Path, chain: str) -> Path:
    return support.chain_dir(root, chain) / support.CHAIN_STATE_FILE


def journal_path(root: Path, chain: str) -> Path:
    return support.chain_runtime_dir(root, chain) / support.CHAIN_SPAWNS_FILE


def read_summary(root: Path, chain: str) -> dict:
    return json.loads(summary_path(root, chain).read_text(encoding="utf-8"))


def write_summary(root: Path, chain: str, state: dict) -> None:
    path = summary_path(root, chain)
    path.parent.mkdir(parents=True, exist_ok=True)
    support.atomic_write(path, json.dumps(state, indent=2, sort_keys=True) + "\n")


def journal_rows(root: Path, chain: str) -> list[dict]:
    path = journal_path(root, chain)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def run_cli(capsys, *argv):
    args = [str(arg) for arg in argv]
    if "--format" not in args:
        args = args + ["--format", "json"]
    code = chain_session.main(args)
    captured = capsys.readouterr()
    payload = json.loads(captured.out) if captured.out.strip() else None
    return code, payload, captured.err


def cli_ok(capsys, *argv) -> dict:
    code, payload, err = run_cli(capsys, *argv)
    assert code == 0, err
    assert payload is not None
    return payload


def cli_err(capsys, *argv) -> str:
    code, payload, err = run_cli(capsys, *argv)
    assert code == 1, (code, payload, err)
    return err


def mut_args(chain: str, root: Path, owner: str, revision: int, chain_id: str = "test-chain") -> tuple:
    return (
        "--chain",
        chain,
        "--root",
        str(root),
        "--owner",
        owner,
        "--expected-revision",
        str(revision),
        "--chain-id",
        chain_id,
        "--format",
        "json",
    )


def autoplan_begin(
    capsys,
    root: Path,
    *,
    step_id: str,
    kind: str,
    revision: int,
    owner: str = OWNER_A,
    chain_id: str = "test-chain",
    target=None,
    plan=None,
    extra=(),
) -> dict:
    argv = [
        "begin",
        *mut_args("autoplan", root, owner, revision, chain_id),
        "--step-id",
        step_id,
        "--client",
        "devin",
        "--surface",
        "desktop",
        "--kind",
        kind,
    ]
    if target is not None:
        argv += ["--target", target]
    if plan is not None:
        argv += ["--plan", plan]
    argv += list(extra)
    return cli_ok(capsys, *argv)


def finish(
    capsys, root: Path, chain: str, *, revision: int, owner: str = OWNER_A, chain_id: str = "test-chain", extra=()
) -> dict:
    return cli_ok(capsys, "finish", *mut_args(chain, root, owner, revision, chain_id), *extra)


def no_launch_guards(monkeypatch) -> None:
    import autoplan_spawn
    import autorun_spawn

    def refuse(*a, **k):
        raise AssertionError("current-session path must not launch windows/workers")

    for module in (support, autorun_spawn, autoplan_spawn):
        for name in (
            "resolve_terminal_backend",
            "open_tmux_session",
            "spawn_tmux_payload",
            "run_terminal_open",
            "build_terminal_command",
            "close_terminal_windows",
            "verify_worker_start",
            "recycle_previous_window",
        ):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, refuse)


def test_no_launch_guards_covers_spawn_module_bindings():
    import autoplan_spawn
    import autorun_spawn

    modules = (support, autorun_spawn, autoplan_spawn)
    originals = {module: module.build_terminal_command for module in modules}
    with pytest.MonkeyPatch.context() as ctx:
        no_launch_guards(ctx)
        patched = [module.build_terminal_command for module in modules]
        assert len({id(fn) for fn in patched}) == 1
        assert patched[0] is not originals[support]
    for module in modules:
        assert module.build_terminal_command is originals[module]


class TestRoute:
    @pytest.mark.parametrize("client", KNOWN_CLIENTS + [GENERIC_CLIENT])
    @pytest.mark.parametrize("surface", ["desktop", "ide-plugin", "unknown"])
    def test_noncli_surfaces_select_current(self, client, surface, monkeypatch):
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("CODEX_SANDBOX", "1")
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
        payload = chain_session.route_payload(client=client, surface=surface, execution="auto")
        assert payload["execution_mode"] == "current"
        assert payload["worker"] is None
        assert payload["native_subagents"] == "optional"

    @pytest.mark.parametrize("client", ["claude", "codex", "pi"])
    def test_verified_cli_adapters_select_fresh(self, client):
        payload = chain_session.route_payload(client=client, surface="cli", execution="auto")
        assert payload["execution_mode"] == "fresh"
        assert payload["worker"] == client

    @pytest.mark.parametrize(
        "client", [c for c in KNOWN_CLIENTS if c not in ("claude", "codex", "pi")] + [GENERIC_CLIENT]
    )
    def test_other_cli_clients_stay_current(self, client):
        payload = chain_session.route_payload(client=client, surface="cli", execution="auto")
        assert payload["execution_mode"] == "current"

    def test_payload_contract_shape(self):
        payload = chain_session.route_payload(client="devin", surface="desktop")
        assert payload == {
            "execution_mode": "current",
            "client": "devin",
            "surface": "desktop",
            "worker": None,
            "requirements": ["workspace-read", "workspace-write", "python>=3.9", "git"],
            "native_subagents": "optional",
        }

    def test_explicit_fresh_requires_verified_worker(self):
        with pytest.raises(ChainSpawnError):
            chain_session.route_payload(client="cursor", surface="cli", execution="fresh")
        payload = chain_session.route_payload(client="cursor", surface="cli", execution="fresh", worker="codex")
        assert payload["worker"] == "codex"
        with pytest.raises(ChainSpawnError):
            chain_session.route_payload(client="codex", surface="cli", execution="fresh", worker="cursor")

    def test_current_rejects_worker(self):
        with pytest.raises(ChainSpawnError):
            chain_session.route_payload(client="devin", surface="desktop", execution="current", worker="pi")

    @pytest.mark.parametrize("client", ["", "x" * 65, "has space", "uniçode", "a/b", "a\\b", ".", ".."])
    def test_invalid_client_identifiers_refuse(self, client):
        with pytest.raises(ChainSpawnError):
            chain_session.route_payload(client=client, surface="desktop")

    @pytest.mark.parametrize("surface", ["tty", "cli ", "CLI", ""])
    def test_invalid_surface_refuses(self, surface):
        with pytest.raises(ChainSpawnError):
            chain_session.route_payload(client="devin", surface=surface)

    @pytest.mark.parametrize("execution", ["spawn", "new", ""])
    def test_invalid_execution_refuses(self, execution):
        with pytest.raises(ChainSpawnError):
            chain_session.route_payload(execution=execution)

    def test_route_cli_json(self, capsys):
        payload = cli_ok(
            capsys,
            "route",
            "--chain",
            "autorun",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--execution",
            "auto",
            "--format",
            "json",
        )
        assert payload["execution_mode"] == "current"

    def test_route_is_pure_no_files(self, tmp_path, capsys, monkeypatch):
        git_init(tmp_path)
        no_launch_guards(monkeypatch)
        before = tree_bytes(tmp_path)
        cli_ok(capsys, "route", "--chain", "autoplan", "--client", "codex", "--surface", "cli")
        assert tree_bytes(tmp_path) == before


def seed_autoplan(root: Path) -> None:
    git_init(root)
    write(root / MASTER_REL, MASTER_SEED)


def expand_master(root: Path) -> None:
    write(root / MASTER_REL, MASTER_EXPANDED)


def autoplan_to_ready(
    capsys, root: Path, *, owner: str = OWNER_A, review_mode: str = "main", chain_id: str = "test-chain"
) -> dict:
    seed_autoplan(root)
    first = autoplan_begin(
        capsys,
        root,
        step_id="framework-1",
        kind="framework",
        revision=0,
        owner=owner,
        chain_id=chain_id,
        plan=MASTER_REL,
    )
    assert first["state"]["pass"] == 1
    repeat = autoplan_begin(
        capsys,
        root,
        step_id="framework-1",
        kind="framework",
        revision=1,
        owner=owner,
        chain_id=chain_id,
        plan=MASTER_REL,
    )
    assert repeat["state"]["pass"] == 1
    assert repeat["state"]["revision"] == 1
    expand_master(root)
    finish(capsys, root, "autoplan", revision=1, owner=owner, chain_id=chain_id)
    autoplan_begin(
        capsys,
        root,
        step_id="detail-alpha",
        kind="detail",
        revision=2,
        owner=owner,
        chain_id=chain_id,
        target=DETAIL_REL,
    )
    write(root / DETAIL_REL, DETAIL_DOC)
    finish(capsys, root, "autoplan", revision=3, owner=owner, chain_id=chain_id)
    autoplan_begin(capsys, root, step_id="review-1", kind="review", revision=4, owner=owner, chain_id=chain_id)
    done = finish(
        capsys,
        root,
        "autoplan",
        revision=5,
        owner=owner,
        chain_id=chain_id,
        extra=("--review-mode", review_mode, "--review-note", "structure and consistency reviewed"),
    )
    return done


class TestReadOnly:
    def test_status_route_and_dry_run_write_nothing(self, tmp_path, capsys, monkeypatch):
        git_init(tmp_path)
        no_launch_guards(monkeypatch)
        before = tree_bytes(tmp_path)
        cli_ok(capsys, "route", "--chain", "autorun", "--client", "pi", "--surface", "cli")
        payload = cli_ok(capsys, "status", "--chain", "autorun", "--root", str(tmp_path), "--format", "json")
        assert payload["started"] is False
        write(tmp_path / MASTER_REL, MASTER_SEED)
        before = tree_bytes(tmp_path)
        cli_ok(
            capsys,
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 0),
            "--step-id",
            "s1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "framework",
            "--plan",
            MASTER_REL,
            "--dry-run",
        )
        assert tree_bytes(tmp_path) == before

    def test_dry_run_on_corrupt_and_legacy_state_is_read_only(self, tmp_path, capsys, monkeypatch):
        git_init(tmp_path)
        no_launch_guards(monkeypatch)
        summary = summary_path(tmp_path, "autorun")
        summary.parent.mkdir(parents=True)
        summary.write_text("{corrupt", encoding="utf-8")
        before = tree_bytes(tmp_path)
        cli_ok(capsys, "status", "--chain", "autorun", "--root", str(tmp_path), "--format", "json")
        assert tree_bytes(tmp_path) == before
        summary.write_text(json.dumps({"lifecycle": "active", "round": 2, "host": "codex"}), encoding="utf-8")
        before = tree_bytes(tmp_path)
        payload = cli_ok(capsys, "status", "--chain", "autorun", "--root", str(tmp_path), "--format", "json")
        assert payload["legacy"] is True
        assert payload["started"] is False
        assert tree_bytes(tmp_path) == before

    def test_status_on_current_state_is_read_only(self, tmp_path, capsys, monkeypatch):
        no_launch_guards(monkeypatch)
        autoplan_to_ready(capsys, tmp_path)
        before = tree_bytes(tmp_path)
        payload = cli_ok(capsys, "status", "--chain", "autoplan", "--root", str(tmp_path), "--format", "json")
        assert payload["lifecycle"] == "ready"
        cli_ok(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 6), "--dry-run")
        assert tree_bytes(tmp_path) == before


class TestAutoplanLifecycle:
    def test_framework_detail_review_to_ready_and_close(self, tmp_path, capsys):
        done = autoplan_to_ready(capsys, tmp_path)
        state = done["state"]
        assert state["lifecycle"] == "ready"
        assert state["step"]["kind"] == "review"
        assert state["step"]["outcome"] == "passed"
        assert state["feature_fingerprint"]
        assert "worker" not in state and "backend" not in state and "pid" not in state
        master = (tmp_path / MASTER_REL).read_text(encoding="utf-8")
        assert "- [ ] Feature alpha" in master
        assert not (tmp_path / ".spec" / "specs" / "2026-01-02_demo").exists()

        status = cli_ok(capsys, "status", "--chain", "autoplan", "--root", str(tmp_path), "--format", "json")
        assert status["lifecycle"] == "ready"
        assert status["ready_valid"] is True

        closed = cli_ok(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 6))
        assert closed["status"] == "closed"
        again = cli_ok(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 0))
        assert again["status"] == "already_closed"

        restarted = autoplan_begin(
            capsys,
            tmp_path,
            step_id="framework-2",
            kind="framework",
            revision=0,
            plan=MASTER_REL,
            chain_id="next-chain",
        )
        assert restarted["state"]["pass"] == 1
        assert restarted["state"]["revision"] == 1
        assert restarted["state"]["chain_id"] != state["chain_id"]

    def test_ready_framework_revision_begin_preserves_receipts(self, tmp_path, capsys):
        done = autoplan_to_ready(capsys, tmp_path)
        completed = list(done["state"]["completed_step_ids"])
        master = tmp_path / MASTER_REL
        master.write_text(
            master.read_text(encoding="utf-8").replace("- [ ] Feature alpha", "- [x] Feature alpha"),
            encoding="utf-8",
        )
        for kind, extra in (
            ("detail", ("--target", DETAIL_REL)),
            ("review", ()),
        ):
            err = cli_err(
                capsys,
                "begin",
                *mut_args("autoplan", tmp_path, OWNER_A, 6),
                "--step-id",
                f"blocked-{kind}",
                "--client",
                "devin",
                "--surface",
                "desktop",
                "--kind",
                kind,
                *extra,
            )
            assert "ready" in err
        revised = autoplan_begin(capsys, tmp_path, step_id="framework-2", kind="framework", revision=6, plan=MASTER_REL)
        state = revised["state"]
        assert state["lifecycle"] == "active"
        assert state["revision"] == 7
        assert state["chain_id"] == done["state"]["chain_id"]
        assert state["step"]["id"] == "framework-2"
        assert state["step"]["kind"] == "framework"
        assert state["completed_step_ids"] == completed + ["review-1"]

    def test_independent_review_label_records_no_fake_worker(self, tmp_path, capsys):
        done = autoplan_to_ready(capsys, tmp_path, review_mode="independent")
        proof = done["state"]["step"]["proof"]
        assert proof["review_mode"] == "independent"
        for key in ("worker", "tty", "window_id", "backend", "shell_command", "pid"):
            assert key not in done["state"]

    def test_revise_outcome_allows_repair_then_review(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        expand_master(tmp_path)
        finish(capsys, tmp_path, "autoplan", revision=1)
        autoplan_begin(capsys, tmp_path, step_id="d1", kind="detail", revision=2, target=DETAIL_REL)
        write(tmp_path / DETAIL_REL, DETAIL_DOC)
        finish(capsys, tmp_path, "autoplan", revision=3)
        autoplan_begin(capsys, tmp_path, step_id="r1", kind="review", revision=4)
        revised = finish(
            capsys,
            tmp_path,
            "autoplan",
            revision=5,
            extra=("--outcome", "revise", "--review-mode", "main", "--review-note", "boundary missing"),
        )
        assert revised["state"]["lifecycle"] == "active"
        assert revised["state"]["step"]["outcome"] == "revise"
        autoplan_begin(capsys, tmp_path, step_id="d1-fix", kind="detail", revision=6, target=DETAIL_REL)
        finish(capsys, tmp_path, "autoplan", revision=7)
        autoplan_begin(capsys, tmp_path, step_id="r2", kind="review", revision=8)
        ready = finish(capsys, tmp_path, "autoplan", revision=9, extra=("--review-mode", "main", "--review-note", "ok"))
        assert ready["state"]["lifecycle"] == "ready"

    def test_framework_revision_may_refresh_features(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        expand_master(tmp_path)
        finish(capsys, tmp_path, "autoplan", revision=1)
        autoplan_begin(capsys, tmp_path, step_id="f2", kind="framework", revision=2, plan=MASTER_REL)
        write(tmp_path / MASTER_REL, MASTER_EXPANDED + "- [ ] Feature gamma\n  - boundary: g\n  - verify: check\n")
        done = finish(capsys, tmp_path, "autoplan", revision=3)
        assert done["state"]["pass"] == 2
        expected = support.feature_fingerprint([tmp_path / MASTER_REL], tmp_path)
        assert done["state"]["feature_fingerprint"] == expected

    def test_first_begin_must_be_framework(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 0),
            "--step-id",
            "d1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "detail",
            "--target",
            DETAIL_REL,
            "--plan",
            MASTER_REL,
        )
        assert "framework" in err


class TestRefusals:
    def _state_unchanged(self, root: Path, chain: str, before: dict) -> None:
        assert read_summary(root, chain) == before

    def test_detail_target_must_be_master_linked_relative_md(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        expand_master(tmp_path)
        finish(capsys, tmp_path, "autoplan", revision=1)
        before = read_summary(tmp_path, "autoplan")
        for bad in (
            "../outside.md",
            "/tmp/absolute.md",
            ".spec/plans/archive/old.md",
            ".spec/plans/other.md",
            str(tmp_path / DETAIL_REL),
        ):
            err = cli_err(
                capsys,
                "begin",
                *mut_args("autoplan", tmp_path, OWNER_A, 2),
                "--step-id",
                "d-bad",
                "--client",
                "devin",
                "--surface",
                "desktop",
                "--kind",
                "detail",
                "--target",
                bad,
            )
            assert err
        self._state_unchanged(tmp_path, "autoplan", before)

    def test_detail_target_symlink_refused(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        expand_master(tmp_path)
        finish(capsys, tmp_path, "autoplan", revision=1)
        outside = write(tmp_path / "real-detail.md", DETAIL_DOC)
        link = tmp_path / DETAIL_REL
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(outside.name if False else outside)
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 2),
            "--step-id",
            "d1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "detail",
            "--target",
            DETAIL_REL,
        )
        assert err
        assert not summary_path(tmp_path, "autoplan").read_text().count('"pass": 2')

    def test_wrong_schema_and_malformed_state_refuse(self, tmp_path, capsys):
        git_init(tmp_path)
        write_summary(tmp_path, "autoplan", {"schema_version": 99, "execution_mode": "current", "lifecycle": "active"})
        err = cli_err(capsys, "status", "--chain", "autoplan", "--root", str(tmp_path))
        assert "unsupported" in err
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 0),
            "--step-id",
            "f1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "framework",
        )
        assert "diverged" in err

        write_summary(
            tmp_path,
            "autoplan",
            {
                "schema_version": 2,
                "execution_mode": "current",
                "chain": "autoplan",
                "chain_id": "x",
                "revision": True,
                "pass": "x",
                "step": {"id": "s"},
                "completed_step_ids": [],
                "lifecycle": "active",
                "updated_at": "t",
            },
        )
        err = cli_err(capsys, "finish", *mut_args("autoplan", tmp_path, OWNER_A, 1))
        assert err
        payload = cli_ok(capsys, "status", "--chain", "autoplan", "--root", str(tmp_path), "--format", "json")
        assert payload["diverged"]

    def test_review_finish_requires_mode_note_and_passing_gate(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        expand_master(tmp_path)
        finish(capsys, tmp_path, "autoplan", revision=1)
        autoplan_begin(capsys, tmp_path, step_id="d1", kind="detail", revision=2, target=DETAIL_REL)
        write(tmp_path / DETAIL_REL, DETAIL_DOC)
        finish(capsys, tmp_path, "autoplan", revision=3)
        autoplan_begin(capsys, tmp_path, step_id="r1", kind="review", revision=4)
        before = read_summary(tmp_path, "autoplan")
        err = cli_err(capsys, "finish", *mut_args("autoplan", tmp_path, OWNER_A, 5), "--review-note", "done")
        assert "review-mode" in err
        err = cli_err(capsys, "finish", *mut_args("autoplan", tmp_path, OWNER_A, 5), "--review-mode", "main")
        assert "review-note" in err
        write(tmp_path / MASTER_REL, "# Master\n\n- [ ] Feature alpha\n- [ ] Feature beta\n\n- [d](01-alpha.md)\n")
        write(tmp_path / MASTER_REL, MASTER_EXPANDED + "- [missing](02-missing.md)\n")
        err = cli_err(
            capsys,
            "finish",
            *mut_args("autoplan", tmp_path, OWNER_A, 5),
            "--review-mode",
            "main",
            "--review-note",
            "done",
        )
        assert "02-missing.md" in err
        self._state_unchanged(tmp_path, "autoplan", before)

    def test_scope_change_during_detail_refuses(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        expand_master(tmp_path)
        finish(capsys, tmp_path, "autoplan", revision=1)
        autoplan_begin(capsys, tmp_path, step_id="d1", kind="detail", revision=2, target=DETAIL_REL)
        write(tmp_path / MASTER_REL, MASTER_EXPANDED.replace("Feature alpha", "Feature alpha reworded"))
        before = read_summary(tmp_path, "autoplan")
        err = cli_err(capsys, "finish", *mut_args("autoplan", tmp_path, OWNER_A, 3))
        assert "planning features changed" in err
        self._state_unchanged(tmp_path, "autoplan", before)

    def test_finish_framework_requires_goal_and_features(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        before = read_summary(tmp_path, "autoplan")
        err = cli_err(capsys, "finish", *mut_args("autoplan", tmp_path, OWNER_A, 1))
        assert "feature" in err.lower()
        self._state_unchanged(tmp_path, "autoplan", before)

    def test_active_step_blocks_new_begin_and_finish(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        before = read_summary(tmp_path, "autoplan")
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 1),
            "--step-id",
            "d1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "detail",
            "--target",
            DETAIL_REL,
        )
        assert "finish or resume it before a new begin" in err
        self._state_unchanged(tmp_path, "autoplan", before)


class TestOwnership:
    def test_owner_mismatch_and_stale_revision_refuse(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        before = read_summary(tmp_path, "autoplan")
        err = cli_err(capsys, "finish", *mut_args("autoplan", tmp_path, OWNER_B, 1))
        assert "owner" in err.lower()
        err = cli_err(capsys, "finish", *mut_args("autoplan", tmp_path, OWNER_A, 9))
        assert "revision" in err.lower()
        self = TestRefusals()
        self._state_unchanged(tmp_path, "autoplan", before)

    def test_same_operation_retry_returns_prior_receipt(self, tmp_path, capsys, monkeypatch):
        seed_autoplan(tmp_path)
        calls = {"n": 0}
        real_atomic_write = support.atomic_write

        def flaky(path, content):
            calls["n"] += 1
            if calls["n"] == 1 and str(path).endswith("chain.json"):
                raise OSError("injected summary write failure")
            return real_atomic_write(path, content)

        monkeypatch.setattr(chain_session, "_atomic_write", flaky)
        argv = [
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 0),
            "--step-id",
            "f1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "framework",
            "--plan",
            MASTER_REL,
        ]
        code, payload, err = run_cli(capsys, *argv)
        assert code == 1
        rows = journal_rows(tmp_path, "autoplan")
        assert len(rows) == 1
        payload = cli_ok(capsys, *argv)
        assert payload["duplicate"] is True
        assert len(journal_rows(tmp_path, "autoplan")) == 1
        assert read_summary(tmp_path, "autoplan")["revision"] == 1

    def test_torn_journal_tail_recovers_read_only(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        with journal_path(tmp_path, "autoplan").open("a", encoding="utf-8") as handle:
            handle.write('{"event": "current_transition", "stat')
        payload = cli_ok(capsys, "status", "--chain", "autoplan", "--root", str(tmp_path), "--format", "json")
        assert payload["revision"] == 1
        assert payload["step"]["id"] == "f1"

    def test_summary_only_chain_requires_takeover(self, tmp_path, capsys):
        git_init(tmp_path)
        write(tmp_path / MASTER_REL, MASTER_EXPANDED)
        state = {
            "schema_version": 2,
            "execution_mode": "current",
            "chain": "autoplan",
            "chain_id": "foreign-1",
            "revision": 3,
            "host": "devin",
            "client_surface": "desktop",
            "plan_docs": [MASTER_REL],
            "pass": 1,
            "max_passes": None,
            "feature_fingerprint": None,
            "step": {
                "id": "f1",
                "kind": "framework",
                "target": None,
                "slug": None,
                "status": "active",
                "outcome": None,
            },
            "completed_step_ids": [],
            "lifecycle": "active",
            "updated_at": "2026-10-20T00:00:00Z",
            "model": None,
            "model_control": "host",
            "permission_effective": "host",
        }
        write_summary(tmp_path, "autoplan", state)
        payload = cli_ok(capsys, "status", "--chain", "autoplan", "--root", str(tmp_path), "--format", "json")
        assert payload["started"] is True
        err = cli_err(capsys, "finish", *mut_args("autoplan", tmp_path, OWNER_B, 3, "foreign-1"))
        assert "--takeover" in err
        resumed = cli_ok(capsys, "resume", *mut_args("autoplan", tmp_path, OWNER_B, 3, "foreign-1"), "--takeover")
        assert resumed["state"]["revision"] == 4
        finish(capsys, tmp_path, "autoplan", revision=4, owner=OWNER_B, chain_id="foreign-1")
        assert read_summary(tmp_path, "autoplan")["pass"] == 1

    def test_paused_step_accepts_new_owner(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        paused = cli_ok(capsys, "pause", *mut_args("autoplan", tmp_path, OWNER_A, 1), "--reason", "handoff to teammate")
        assert paused["state"]["step"]["status"] == "paused"
        resumed = cli_ok(capsys, "resume", *mut_args("autoplan", tmp_path, OWNER_B, 2))
        assert resumed["state"]["step"]["status"] == "active"
        rows = journal_rows(tmp_path, "autoplan")
        assert rows[-1]["owner_hash"] == hashlib.sha256(OWNER_B.encode()).hexdigest()

    def test_active_step_requires_explicit_takeover(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        err = cli_err(capsys, "resume", *mut_args("autoplan", tmp_path, OWNER_B, 1))
        assert "takeover" in err.lower()
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_B, 1),
            "--step-id",
            "hijack",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "framework",
        )
        assert "owner" in err.lower()
        resumed = cli_ok(capsys, "resume", *mut_args("autoplan", tmp_path, OWNER_B, 1), "--takeover")
        assert resumed["state"]["revision"] == 2
        expand_master(tmp_path)
        finish(capsys, tmp_path, "autoplan", revision=2, owner=OWNER_B)

    def test_completed_step_id_cannot_silently_rerun(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        expand_master(tmp_path)
        finish(capsys, tmp_path, "autoplan", revision=1)
        repeat = autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=2, plan=MASTER_REL)
        assert repeat["state"]["pass"] == 1
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 2),
            "--step-id",
            "f1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "review",
        )
        assert "different work" in err

    def test_closed_current_does_not_resurrect_from_audit_tail(self, tmp_path, capsys):
        autoplan_to_ready(capsys, tmp_path)
        cli_ok(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 6))
        summary_path(tmp_path, "autoplan").write_text("{corrupt", encoding="utf-8")
        state, status = support.read_state(
            support.chain_dir(tmp_path, "autoplan"),
            support.chain_runtime_dir(tmp_path, "autoplan"),
            "autoplan",
        )
        assert state is None
        assert status == support.READ_CORRUPT
        assert summary_path(tmp_path, "autoplan").read_text(encoding="utf-8") == "{corrupt"
        assert chain_session.has_current_state(tmp_path, "autoplan") is False

    def test_fresh_spawn_refuses_active_current_before_backend(self, tmp_path, capsys, monkeypatch):
        import autorun_spawn

        seed_autoplan(tmp_path)
        write(tmp_path / PLAN_DOC_REL, PLAN_DOC)
        package_dir = tmp_path / ".spec" / "specs" / "2026-10-01_fix-one"
        package_dir.mkdir(parents=True)
        write(package_dir / "spec.md", PACKAGE_SPEC.format(branch="spec/2026-10-01_fix-one"))
        write(package_dir / "tasks.md", TASKS_TODO)
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "-b", "spec/2026-10-01_fix-one"], check=True)
        cli_ok(
            capsys,
            "begin",
            *mut_args("autorun", tmp_path, OWNER_A, 0),
            "--step-id",
            "2026-10-01_fix-one",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--slug",
            "2026-10-01_fix-one",
            "--plan",
            PLAN_DOC_REL,
        )

        called = {"backend": False}

        def fail_backend(*a, **k):
            called["backend"] = True
            raise AssertionError("backend selection must not run for an active current chain")

        monkeypatch.setattr(support, "resolve_terminal_backend", fail_backend)
        code = autorun_spawn.main(["spawn", "--root", str(tmp_path), "--host", "codex"])
        assert code == 1
        assert called["backend"] is False
        assert "current" in capsys.readouterr().err


class TestCaps:
    def test_autorun_default_cap_is_five(self, tmp_path, capsys):
        git_init(tmp_path)
        write(tmp_path / PLAN_DOC_REL, PLAN_DOC)
        slug = "2026-10-01_fix-one"
        package_dir = tmp_path / ".spec" / "specs" / slug
        package_dir.mkdir(parents=True)
        write(package_dir / "spec.md", PACKAGE_SPEC.format(branch=f"spec/{slug}"))
        write(package_dir / "tasks.md", TASKS_TODO)
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "-b", f"spec/{slug}"], check=True)
        payload = cli_ok(
            capsys,
            "begin",
            *mut_args("autorun", tmp_path, OWNER_A, 0),
            "--step-id",
            slug,
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--slug",
            slug,
            "--plan",
            PLAN_DOC_REL,
        )
        assert payload["state"]["max_rounds"] == 5
        assert payload["state"]["round"] == 1

    def test_autoplan_default_uncapped(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        payload = autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        assert payload["state"]["max_passes"] is None

    def test_cap_counts_only_new_units(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        payload = autoplan_begin(
            capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL, extra=("--max-passes", "2")
        )
        assert payload["state"]["max_passes"] == 2
        expand_master(tmp_path)
        finish(capsys, tmp_path, "autoplan", revision=1)
        autoplan_begin(capsys, tmp_path, step_id="d1", kind="detail", revision=2, target=DETAIL_REL)
        write(tmp_path / DETAIL_REL, DETAIL_DOC)
        finish(capsys, tmp_path, "autoplan", revision=3)
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 4),
            "--step-id",
            "r1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "review",
        )
        assert "cap reached" in err
        repeat = autoplan_begin(capsys, tmp_path, step_id="d1", kind="detail", revision=4, target=DETAIL_REL)
        assert repeat["state"]["pass"] == 2

    def test_cap_flag_exclusive_and_chain_scoped(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        with pytest.raises(SystemExit):
            chain_session.main(
                [
                    "begin",
                    *mut_args("autoplan", tmp_path, OWNER_A, 0),
                    "--step-id",
                    "f1",
                    "--client",
                    "devin",
                    "--surface",
                    "desktop",
                    "--kind",
                    "framework",
                    "--max-passes",
                    "3",
                    "--unbounded",
                ]
            )
        capsys.readouterr()
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autorun", tmp_path, OWNER_A, 0),
            "--step-id",
            "p1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--slug",
            "2026-10-01_fix-x",
            "--max-passes",
            "2",
        )
        assert "max-passes" in err
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 0),
            "--step-id",
            "f1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "framework",
            "--max-rounds",
            "2",
        )
        assert "max-rounds" in err
        assert not summary_path(tmp_path, "autoplan").exists()

    def test_model_permission_flags_rejected_without_mutation(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        for extra in (("--model", "x"), ("--reasoning", "high"), ("--permission-mode", "bypass")):
            with pytest.raises(SystemExit):
                chain_session.main(
                    [
                        "begin",
                        *mut_args("autoplan", tmp_path, OWNER_A, 0),
                        "--step-id",
                        "f1",
                        "--client",
                        "devin",
                        "--surface",
                        "desktop",
                        "--kind",
                        "framework",
                        *extra,
                    ]
                )
            capsys.readouterr()
        assert not summary_path(tmp_path, "autoplan").exists()


def make_active_package(root: Path, slug: str, branch: str) -> Path:
    package = root / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    write(package / "spec.md", PACKAGE_SPEC.format(branch=branch))
    write(package / "tasks.md", TASKS_TODO)
    write(package / "checklist.md", CHECKLIST_DONE.replace("[x]", "[ ]").replace("通过", "待修复"))
    return package


def archive_package(root: Path, slug: str) -> Path:
    package = root / ".spec" / "specs" / slug
    write(package / "tasks.md", TASKS_DONE)
    write(package / "checklist.md", CHECKLIST_DONE)
    archive_root = root / ".spec" / "specs" / "archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    archived = package.rename(archive_root / slug)
    write(archived / "completion-summary.md", SUMMARY_V1)
    return archived


def autorun_begin(
    capsys,
    root: Path,
    slug: str,
    *,
    step_id: str,
    revision: int,
    owner: str = OWNER_A,
    chain_id: str = "test-chain",
    plan=PLAN_DOC_REL,
) -> dict:
    return cli_ok(
        capsys,
        "begin",
        *mut_args("autorun", root, owner, revision, chain_id),
        "--step-id",
        step_id,
        "--client",
        "devin",
        "--surface",
        "desktop",
        "--slug",
        slug,
        "--plan",
        plan,
    )


class TestAutorunPackages:
    def test_begin_requires_exactly_one_matching_active_package(self, tmp_path, capsys):
        git_init(tmp_path)
        write(tmp_path / PLAN_DOC_REL, PLAN_DOC)
        slug_a = "2026-10-01_fix-one"
        slug_b = "2026-10-02_fix-two"
        make_active_package(tmp_path, slug_a, f"spec/{slug_a}")
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "-b", f"spec/{slug_a}"], check=True)
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autorun", tmp_path, OWNER_A, 0),
            "--step-id",
            slug_b,
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--slug",
            slug_b,
            "--plan",
            PLAN_DOC_REL,
        )
        assert "active Development Record" in err
        make_active_package(tmp_path, slug_b, f"spec/{slug_b}")
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autorun", tmp_path, OWNER_A, 0),
            "--step-id",
            slug_a,
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--slug",
            slug_a,
            "--plan",
            PLAN_DOC_REL,
        )
        assert "multiple active packages" in err

    def test_two_rounds_increment_and_same_slug_dedupes(self, tmp_path, capsys):
        git_init(tmp_path)
        write(tmp_path / PLAN_DOC_REL, PLAN_DOC)
        slug_a = "2026-10-01_fix-one"
        slug_b = "2026-10-02_fix-two"
        make_active_package(tmp_path, slug_a, f"spec/{slug_a}")
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "-b", f"spec/{slug_a}"], check=True)
        first = autorun_begin(capsys, tmp_path, slug_a, step_id=slug_a, revision=0)
        assert first["state"]["round"] == 1
        repeat = autorun_begin(capsys, tmp_path, slug_a, step_id=slug_a, revision=1)
        assert repeat["state"]["round"] == 1
        assert repeat["duplicate"] is True
        assert repeat["state"]["revision"] == 1

        archive_package(tmp_path, slug_a)
        finish(capsys, tmp_path, "autorun", revision=1)
        state = read_summary(tmp_path, "autorun")
        assert state["lifecycle"] == "active"

        make_active_package(tmp_path, slug_b, f"spec/{slug_b}")
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "-b", f"spec/{slug_b}"], check=True)
        second = autorun_begin(capsys, tmp_path, slug_b, step_id=slug_b, revision=2)
        assert second["state"]["round"] == 2
        assert second["state"]["step"]["slug"] == slug_b
        archive_package(tmp_path, slug_b)
        done = finish(capsys, tmp_path, "autorun", revision=3)
        assert done["state"]["lifecycle"] == "active"

        write(tmp_path / PLAN_DOC_REL, PLAN_DOC.replace("- [ ]", "- [x]"))
        closed = cli_ok(capsys, "close", *mut_args("autorun", tmp_path, OWNER_A, 4))
        assert closed["status"] == "closed"

    def test_finish_refuses_incomplete_or_missing_archive(self, tmp_path, capsys):
        git_init(tmp_path)
        write(tmp_path / PLAN_DOC_REL, PLAN_DOC)
        slug = "2026-10-01_fix-one"
        make_active_package(tmp_path, slug, f"spec/{slug}")
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "-b", f"spec/{slug}"], check=True)
        autorun_begin(capsys, tmp_path, slug, step_id=slug, revision=0)
        before = read_summary(tmp_path, "autorun")
        err = cli_err(capsys, "finish", *mut_args("autorun", tmp_path, OWNER_A, 1))
        assert "must be archived" in err
        archived = archive_package(tmp_path, slug)
        write(archived / "completion-summary.md", "# short\n")
        err = cli_err(capsys, "finish", *mut_args("autorun", tmp_path, OWNER_A, 1))
        assert "completion summary" in err
        assert read_summary(tmp_path, "autorun") == before
        write(archived / "completion-summary.md", SUMMARY_V1)
        done = finish(capsys, tmp_path, "autorun", revision=1)
        assert done["state"]["step"]["status"] == "complete"
        assert done["state"]["step"]["proof"]["archive_hashes"]["spec.md"]

    def test_begin_refuses_missing_or_edited_plan(self, tmp_path, capsys):
        git_init(tmp_path)
        slug = "2026-10-01_fix-one"
        make_active_package(tmp_path, slug, f"spec/{slug}")
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "-b", f"spec/{slug}"], check=True)
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autorun", tmp_path, OWNER_A, 0),
            "--step-id",
            slug,
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--slug",
            slug,
            "--plan",
            PLAN_DOC_REL,
        )
        assert "does not exist" in err
        write(tmp_path / PLAN_DOC_REL, PLAN_DOC)
        autorun_begin(capsys, tmp_path, slug, step_id=slug, revision=0)
        archive_package(tmp_path, slug)
        write(tmp_path / PLAN_DOC_REL, PLAN_DOC.replace("First package", "First package reworded"))
        err = cli_err(capsys, "finish", *mut_args("autorun", tmp_path, OWNER_A, 1))
        assert "planning features changed" in err

    def test_complete_chain_close(self, tmp_path, capsys):
        git_init(tmp_path)
        write(tmp_path / PLAN_DOC_REL, "- [ ] Only feature\n  - boundary: x\n  - verify: check\n")
        slug = "2026-10-01_fix-one"
        make_active_package(tmp_path, slug, f"spec/{slug}")
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "-b", f"spec/{slug}"], check=True)
        autorun_begin(capsys, tmp_path, slug, step_id=slug, revision=0)
        archive_package(tmp_path, slug)
        write(tmp_path / PLAN_DOC_REL, "- [x] Only feature\n  - boundary: x\n  - verify: check\n")
        done = finish(capsys, tmp_path, "autorun", revision=1)
        assert done["state"]["lifecycle"] == "complete"
        closed = cli_ok(capsys, "close", *mut_args("autorun", tmp_path, OWNER_A, 2))
        assert closed["status"] == "closed"


class TestWorktreeAndPlatform:
    @requires_git
    def test_linked_worktree_runtime_lives_under_real_git_dir(self, tmp_path, capsys):
        main_repo = tmp_path / "main"
        main_repo.mkdir()
        git_init(main_repo)
        git_env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
        write(main_repo / "seed.txt", "seed\n")
        subprocess.run(["git", "-C", str(main_repo), "add", "seed.txt"], check=True)
        subprocess.run(["git", "-C", str(main_repo), "commit", "-qm", "seed"], check=True, env=git_env)
        worktree = tmp_path / "wt"
        subprocess.run(
            ["git", "-C", str(main_repo), "worktree", "add", str(worktree), "-b", "wt-branch"],
            check=True,
            capture_output=True,
        )
        assert (worktree / ".git").is_file()

        write(worktree / MASTER_REL, MASTER_SEED)
        payload = autoplan_begin(capsys, worktree, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        assert payload["state"]["revision"] == 1
        git_dir = Path(
            subprocess.run(
                ["git", "-C", str(worktree), "rev-parse", "--absolute-git-dir"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        journal = git_dir / "spec-chain" / "autoplan" / "spawns.jsonl"
        assert journal.is_file(), f"journal must live under the resolved git dir, got {journal}"
        assert (worktree / ".git").is_file()
        assert summary_path(worktree, "autoplan").is_file()

    def test_windows_mutation_gets_wsl2_guidance(self, tmp_path, capsys, monkeypatch):
        git_init(tmp_path)
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 0),
            "--step-id",
            "f1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "framework",
        )
        assert "WSL2" in err
        assert not summary_path(tmp_path, "autoplan").exists()


class TestLegacyDispatch:
    def test_legacy_active_state_refuses_current_begin(self, tmp_path, capsys):
        git_init(tmp_path)
        write_summary(
            tmp_path,
            "autorun",
            {
                "lifecycle": "active",
                "round": 1,
                "host": "codex",
                "step": {"status": "active"},
                "plan_docs": [PLAN_DOC_REL],
            },
        )
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autorun", tmp_path, OWNER_A, 0),
            "--step-id",
            "x",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--slug",
            "2026-10-01_fix-x",
        )
        assert "legacy fresh-session" in err
        assert chain_session.has_current_state(tmp_path, "autorun") is False

    def test_legacy_closed_marker_stays_compatible(self, tmp_path, capsys):
        git_init(tmp_path)
        import chain_lifecycle

        digest = "a" * 64
        write_summary(
            tmp_path,
            "autorun",
            {
                "lifecycle": "closed",
                "chain": "autorun",
                "archive": f".spec/autorun/history/{digest}.json",
                "closed_at": "2026-01-01T00:00:00Z",
                "previous_digest": digest,
            },
        )
        payload = chain_lifecycle.close_chain(tmp_path, "autorun")
        assert payload["status"] == "already_closed"
        assert chain_session.has_current_state(tmp_path, "autorun") is False

    def test_old_status_dispatches_to_current_view(self, tmp_path, capsys):
        import autoplan_spawn

        autoplan_to_ready(capsys, tmp_path)
        payload = autoplan_spawn.status_payload(tmp_path)
        assert payload.get("execution_mode") == "current"
        assert payload["lifecycle"] == "ready"
        rendered = autoplan_spawn._render_status(payload)
        assert "ready" in rendered

    def test_old_close_refuses_active_current_with_guidance(self, tmp_path, capsys):
        import chain_lifecycle

        autoplan_to_ready(capsys, tmp_path)
        with pytest.raises(ChainSpawnError) as exc:
            chain_lifecycle.close_chain(tmp_path, "autoplan")
        assert "chain_session.py" in str(exc.value)

    def test_old_spawn_statuses_untouched_by_current_rows(self, tmp_path, capsys):
        autoplan_to_ready(capsys, tmp_path)
        summary_path(tmp_path, "autoplan").write_text("{corrupt", encoding="utf-8")
        state, status = support.read_state(
            support.chain_dir(tmp_path, "autoplan"),
            support.chain_runtime_dir(tmp_path, "autoplan"),
            "autoplan",
        )
        assert state is None
        assert status == support.READ_CORRUPT


def install_env(home: Path, extra: dict | None = None) -> dict:
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "BACKUP_ROOT": str(home / ".spec-skill-backups"),
        "SPEC_SKIP_SIGNING": "1",
    }
    env.update({k: v for k, v in os.environ.items() if k in ("TMPDIR",)})
    if extra:
        env.update(extra)
    return env


def run_installer(home: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = install_env(home, extra_env)
    return subprocess.run(
        ["bash", str(ROOT / "install.sh")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )


class TestInstallHosts:
    @requires_bash
    def test_devin_cursor_full_install(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        result = run_installer(home, {"INSTALL_HOSTS": "devin,cursor", "INSTALL_PROFILE": "full"})
        assert result.returncode == 0, result.stderr
        for skills_root in (home / ".config" / "devin" / "skills", home / ".cursor" / "skills"):
            skill = skills_root / "spec"
            assert (skill / "SKILL.md").is_file()
            assert (skill / "scripts" / "chain_session.py").is_file()
            assert (skill / ".spec-skill-install").is_file()
        for rel in (
            ".claude",
            ".claude-desktop",
            ".codex",
            ".gemini",
            ".grok",
            ".config/opencode",
            ".openclaw",
            ".hermes",
            ".pi",
            ".zcode",
        ):
            assert not (home / rel).exists(), rel

    @requires_bash
    def test_devin_cursor_core_excludes_chain_runtime(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        result = run_installer(home, {"INSTALL_HOSTS": "devin-desktop,cursor-cli", "INSTALL_PROFILE": "core"})
        assert result.returncode == 0, result.stderr
        for skills_root in (home / ".config" / "devin" / "skills", home / ".cursor" / "skills"):
            skill = skills_root / "spec"
            assert (skill / "SKILL.md").is_file()
            assert not (skill / "scripts" / "chain_session.py").exists()
            assert not (skill / "scripts" / "autorun_spawn.py").exists()

    @requires_bash
    def test_foreign_devin_skill_protected_and_force_recovers(self, tmp_path):
        home = tmp_path / "home"
        foreign = home / ".config" / "devin" / "skills" / "spec"
        foreign.mkdir(parents=True)
        write(foreign / "SKILL.md", "# foreign spec\n")
        refused = run_installer(home, {"INSTALL_HOSTS": "devin", "INSTALL_PROFILE": "full"})
        assert refused.returncode != 0
        assert (foreign / "SKILL.md").read_text() == "# foreign spec\n"
        forced = run_installer(home, {"INSTALL_HOSTS": "devin", "INSTALL_PROFILE": "full", "FORCE": "1"})
        assert forced.returncode == 0, forced.stderr
        assert (foreign / "SKILL.md").is_file()
        assert (foreign / "scripts" / "chain_session.py").is_file()
        assert list((home / ".spec-skill-backups").rglob("SKILL.md")), "foreign skill must be backed up"

    @requires_bash
    def test_devin_detection_via_cli_and_dir(self, tmp_path):
        home = tmp_path / "home"
        stubs = tmp_path / "stubs"
        stub = stubs / "devin"
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
        env = install_env(home)
        env["PATH"] = f"{stubs}:{env['PATH']}"
        home.mkdir(exist_ok=True)
        result = subprocess.run(
            ["bash", str(ROOT / "install.sh")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env=env,
            timeout=300,
        )
        assert result.returncode == 0, result.stderr
        assert (home / ".config" / "devin" / "skills" / "spec" / "SKILL.md").is_file()

        home2 = tmp_path / "home2"
        (home2 / ".cursor").mkdir(parents=True)
        result = subprocess.run(
            ["bash", str(ROOT / "install.sh")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env=install_env(home2),
            timeout=300,
        )
        assert result.returncode == 0, result.stderr
        assert (home2 / ".cursor" / "skills" / "spec" / "SKILL.md").is_file()


GIT_COMMIT_ENV = {
    "GIT_AUTHOR_NAME": "Chain Test",
    "GIT_AUTHOR_EMAIL": "chain@test.invalid",
    "GIT_COMMITTER_NAME": "Chain Test",
    "GIT_COMMITTER_EMAIL": "chain@test.invalid",
}


def git_commit_all(root: Path, message: str) -> None:
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", message],
        check=True,
        capture_output=True,
        env={**os.environ, **GIT_COMMIT_ENV},
    )


def current_state_dict(chain: str, **overrides) -> dict:
    state = {
        "schema_version": 2,
        "execution_mode": "current",
        "chain": chain,
        "chain_id": "cid-1",
        "revision": 1,
        "host": "devin",
        "client_surface": "desktop",
        "plan_docs": [MASTER_REL],
        "pass": 1,
        "max_passes": None,
        "feature_fingerprint": None,
        "step": {
            "id": "f1",
            "kind": "framework",
            "target": None,
            "slug": None,
            "status": "active",
            "outcome": None,
        },
        "completed_step_ids": [],
        "lifecycle": "active",
        "updated_at": "2026-10-20T00:00:00Z",
        "model": None,
        "model_control": "host",
        "permission_effective": "host",
    }
    if chain == "autorun":
        state["round"] = state.pop("pass")
        state["max_rounds"] = state.pop("max_passes")
        state["plan_docs"] = [PLAN_DOC_REL]
        state["step"]["kind"] = "package"
        state["step"]["slug"] = "2026-10-01_fix-one"
    state.update(overrides)
    return state


def journal_row(state: dict, operation_id: str = "ab" * 32, owner: str = OWNER_A) -> dict:
    return {
        "event": "current_transition",
        "state": state,
        "owner_hash": hashlib.sha256(owner.encode("utf-8")).hexdigest(),
        "operation_id": operation_id,
    }


def append_journal(root: Path, chain: str, row) -> None:
    path = journal_path(root, chain)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(row if isinstance(row, str) else json.dumps(row) + "\n")


def runtime_blob(root: Path, chain: str) -> bytes:
    blob = b""
    for base in (
        root / ".spec" / chain,
        support.chain_runtime_dir(root, chain),
    ):
        if base.exists():
            for path in sorted(base.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    blob += path.read_bytes()
    return blob


class TestCasAndReplay:
    def test_stale_expected_revision_refuses_without_writes(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        expand_master(tmp_path)
        finish(capsys, tmp_path, "autoplan", revision=1)
        summary_before = summary_path(tmp_path, "autoplan").read_bytes()
        journal_before = journal_path(tmp_path, "autoplan").read_bytes()
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 0),
            "--step-id",
            "d1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "detail",
            "--target",
            DETAIL_REL,
        )
        assert "stale revision" in err
        assert summary_path(tmp_path, "autoplan").read_bytes() == summary_before
        assert journal_path(tmp_path, "autoplan").read_bytes() == journal_before

    def test_old_owner_exact_begin_replay_after_takeover_refuses(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        argv = [
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 0),
            "--step-id",
            "f1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "framework",
            "--plan",
            MASTER_REL,
        ]
        cli_ok(capsys, *argv)
        resumed = cli_ok(capsys, "resume", *mut_args("autoplan", tmp_path, OWNER_B, 1), "--takeover")
        assert resumed["state"]["revision"] == 2
        err = cli_err(capsys, *argv)
        assert "owner" in err
        assert len(journal_rows(tmp_path, "autoplan")) == 2
        assert read_summary(tmp_path, "autoplan")["revision"] == 2

    @requires_git
    def test_concurrent_next_begins_single_commit(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        expand_master(tmp_path)
        finish(capsys, tmp_path, "autoplan", revision=1)
        base = [
            sys.executable,
            str(SCRIPTS / "chain_session.py"),
            "begin",
            "--chain",
            "autoplan",
            "--root",
            str(tmp_path),
            "--owner",
            OWNER_A,
            "--expected-revision",
            "2",
            "--chain-id",
            "test-chain",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "detail",
            "--target",
            DETAIL_REL,
            "--format",
            "json",
        ]
        proc_a = subprocess.Popen(base + ["--step-id", "d1"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        proc_b = subprocess.Popen(base + ["--step-id", "d2"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        code_a = proc_a.wait(timeout=60)
        code_b = proc_b.wait(timeout=60)
        assert sorted([code_a, code_b]) == [0, 1]
        loser = proc_b if code_b else proc_a
        err = loser.stderr.read() if loser.stderr else ""
        assert "revision" in err or "lock" in err.lower() or "busy" in err.lower()
        assert len(journal_rows(tmp_path, "autoplan")) == 3
        assert read_summary(tmp_path, "autoplan")["revision"] == 3

    def test_cap_override_then_unbounded_records_none(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        payload = autoplan_begin(
            capsys,
            tmp_path,
            step_id="f1",
            kind="framework",
            revision=0,
            plan=MASTER_REL,
            extra=("--max-passes", "1"),
        )
        assert payload["state"]["max_passes"] == 1
        expand_master(tmp_path)
        finish(capsys, tmp_path, "autoplan", revision=1)
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 2),
            "--step-id",
            "d1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "detail",
            "--target",
            DETAIL_REL,
        )
        assert "cap reached" in err
        admitted = autoplan_begin(
            capsys,
            tmp_path,
            step_id="d1",
            kind="detail",
            revision=2,
            target=DETAIL_REL,
            extra=("--max-passes", "3"),
        )
        assert admitted["state"]["pass"] == 2
        assert admitted["state"]["max_passes"] == 3
        write(tmp_path / DETAIL_REL, DETAIL_DOC)
        finish(capsys, tmp_path, "autoplan", revision=3)
        review = autoplan_begin(
            capsys,
            tmp_path,
            step_id="r1",
            kind="review",
            revision=4,
            extra=("--unbounded",),
        )
        assert review["state"]["pass"] == 3
        assert review["state"]["max_passes"] is None
        paused = cli_ok(capsys, "pause", *mut_args("autoplan", tmp_path, OWNER_A, 5), "--reason", "hold")
        assert paused["state"]["step"]["status"] == "paused"
        resumed = cli_ok(capsys, "resume", *mut_args("autoplan", tmp_path, OWNER_A, 6))
        assert resumed["state"]["step"]["status"] == "active"
        done = finish(
            capsys,
            tmp_path,
            "autoplan",
            revision=7,
            extra=("--review-mode", "main", "--review-note", "checked"),
        )
        assert done["state"]["lifecycle"] == "ready"
        assert done["state"]["pass"] == 3

    @pytest.mark.parametrize("bad", ["0", "-1"])
    def test_nonpositive_cap_rejected_before_commit(self, tmp_path, capsys, bad):
        seed_autoplan(tmp_path)
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 0),
            "--step-id",
            "f1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "framework",
            "--plan",
            MASTER_REL,
            "--max-passes",
            bad,
        )
        assert ">= 1" in err or "positive" in err.lower()
        assert not summary_path(tmp_path, "autoplan").exists()

    def test_close_revision_increments_and_retry_stable(self, tmp_path, capsys):
        ready = autoplan_to_ready(capsys, tmp_path)
        assert ready["state"]["lifecycle"] == "ready"
        closed = cli_ok(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 6))
        assert closed["status"] == "closed"
        assert closed["state"]["revision"] == 7
        marker = read_summary(tmp_path, "autoplan")
        assert marker["lifecycle"] == "closed"
        assert marker["revision"] == 7
        again = cli_ok(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 6))
        assert again["status"] == "already_closed"
        assert again["revision"] == 7
        assert again["state"]["closed_at"] == closed["state"]["closed_at"]
        archive_rel = closed["archive"]
        assert (tmp_path / archive_rel).is_file()

    def test_step_id_reused_in_new_epoch_after_close(self, tmp_path, capsys):
        ready = autoplan_to_ready(capsys, tmp_path)
        old_chain_id = ready["state"]["chain_id"]
        cli_ok(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 6))
        seed_autoplan_docs = tmp_path / MASTER_REL
        assert seed_autoplan_docs.is_file()
        payload = autoplan_begin(
            capsys,
            tmp_path,
            step_id="f1",
            kind="framework",
            revision=0,
            plan=MASTER_REL,
            chain_id="next-chain",
        )
        assert payload["state"]["revision"] == 1
        assert payload["state"]["chain_id"] != old_chain_id
        rows = journal_rows(tmp_path, "autoplan")
        assert rows[-1]["state"]["revision"] == 1
        assert rows[-1]["state"]["chain_id"] == payload["state"]["chain_id"]


class TestJournalFaults:
    def test_fsync_failure_reports_uncertain_retry_commits_once(self, tmp_path, capsys, monkeypatch):
        seed_autoplan(tmp_path)
        real_fsync = os.fsync
        calls = {"count": 0}

        def flaky_fsync(fd):
            calls["count"] += 1
            if calls["count"] == 1:
                raise OSError("injected fsync failure")
            return real_fsync(fd)

        monkeypatch.setattr(os, "fsync", flaky_fsync)
        argv = [
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 0),
            "--step-id",
            "f1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "framework",
            "--plan",
            MASTER_REL,
        ]
        err = cli_err(capsys, *argv)
        assert "uncertain" in err
        assert len(journal_rows(tmp_path, "autoplan")) == 1
        payload = cli_ok(capsys, *argv)
        assert payload["duplicate"] is True
        assert len(journal_rows(tmp_path, "autoplan")) == 1
        assert read_summary(tmp_path, "autoplan")["revision"] == 1

    def test_new_epoch_begin_recovers_over_closed_summary(self, tmp_path, capsys, monkeypatch):
        autoplan_to_ready(capsys, tmp_path)
        cli_ok(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 6))
        marker = read_summary(tmp_path, "autoplan")
        assert marker["lifecycle"] == "closed"
        real_atomic = chain_session._atomic_write
        calls = {"count": 0}

        def flaky_atomic(path, text):
            calls["count"] += 1
            if calls["count"] == 1:
                raise OSError("injected summary failure")
            return real_atomic(path, text)

        monkeypatch.setattr(chain_session, "_atomic_write", flaky_atomic)
        argv = [
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_B, 0, "next-chain"),
            "--step-id",
            "f1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "framework",
            "--plan",
            MASTER_REL,
        ]
        err = cli_err(capsys, *argv)
        assert "summary write failed" in err
        status = cli_ok(capsys, "status", "--chain", "autoplan", "--root", str(tmp_path), "--format", "json")
        assert status["started"] is True
        assert status["revision"] == 1
        assert read_summary(tmp_path, "autoplan")["lifecycle"] == "closed"
        payload = cli_ok(capsys, *argv)
        assert payload["duplicate"] is True
        assert payload["revision"] == 1
        recovered = read_summary(tmp_path, "autoplan")
        assert recovered["revision"] == 1
        assert recovered["lifecycle"] == "active"

    def test_interior_malformed_journal_row_refuses(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        state2 = current_state_dict("autoplan", revision=2, updated_at="2026-10-20T00:01:00Z")
        append_journal(tmp_path, "autoplan", "{corrupt interior line\n")
        append_journal(tmp_path, "autoplan", journal_row(state2, "cd" * 32))
        before = tree_bytes(tmp_path)
        status = cli_ok(capsys, "status", "--chain", "autoplan", "--root", str(tmp_path), "--format", "json")
        assert "line 2" in status["diverged"]
        err = cli_err(capsys, "pause", *mut_args("autoplan", tmp_path, OWNER_A, 1), "--reason", "x")
        assert "diverged" in err
        assert tree_bytes(tmp_path) == before

    def test_torn_tail_then_mutation_leaves_valid_journal(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        torn = b'{"event": "current_transition", "stat'
        with journal_path(tmp_path, "autoplan").open("ab") as handle:
            handle.write(torn)
        original = journal_path(tmp_path, "autoplan").read_bytes()
        before = tree_bytes(tmp_path)
        status = cli_ok(capsys, "status", "--chain", "autoplan", "--root", str(tmp_path), "--format", "json")
        assert status["revision"] == 1
        assert tree_bytes(tmp_path) == before
        paused = cli_ok(capsys, "pause", *mut_args("autoplan", tmp_path, OWNER_A, 1), "--reason", "hold")
        assert paused["state"]["revision"] == 2
        rows = journal_rows(tmp_path, "autoplan")
        assert len(rows) == 2
        assert rows[-1]["state"]["revision"] == 2
        canonical = journal_path(tmp_path, "autoplan").read_bytes()
        assert canonical.startswith(original[: -len(torn)])
        runtime_dir = support.chain_runtime_dir(tmp_path, "autoplan")
        backup = runtime_dir / f"spawns.torn-{hashlib.sha256(original).hexdigest()}.jsonl"
        assert backup.is_file()
        assert backup.read_bytes() == original
        status = cli_ok(capsys, "status", "--chain", "autoplan", "--root", str(tmp_path), "--format", "json")
        assert status["revision"] == 2

    def test_valid_json_nonobject_tail_is_not_torn_and_refuses(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        with journal_path(tmp_path, "autoplan").open("a", encoding="utf-8") as handle:
            handle.write("[]\n")
        status = cli_ok(capsys, "status", "--chain", "autoplan", "--root", str(tmp_path), "--format", "json")
        assert status["diverged"]
        err = cli_err(capsys, "pause", *mut_args("autoplan", tmp_path, OWNER_A, 1), "--reason", "hold")
        assert "diverged" in err

    def test_closed_current_then_legacy_tail_stays_legacy(self, tmp_path, capsys):
        autoplan_to_ready(capsys, tmp_path)
        cli_ok(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 6))
        write_summary(tmp_path, "autoplan", {"lifecycle": "active", "pass": 2, "host": "codex"})
        append_journal(
            tmp_path,
            "autoplan",
            {
                "round": 1,
                "host": "codex",
                "host_source": "flag",
                "plan_docs": [PLAN_DOC_REL],
                "max_rounds": 5,
                "feature_fingerprint": "ab" * 32,
                "permission_mode": "default",
                "permission_effective": "default",
                "model": None,
                "model_injection": None,
                "spawned_at": "2026-10-20T00:00:00Z",
                "shell_command": "",
                "prev_tty": None,
            },
        )
        assert chain_session.has_current_state(tmp_path, "autoplan") is False
        chain_session.refuse_current_spawn(tmp_path, "autoplan")

    def test_active_current_summary_over_old_closure_diverges(self, tmp_path, capsys):
        autoplan_to_ready(capsys, tmp_path)
        cli_ok(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 6))
        foreign = current_state_dict("autoplan", chain_id="cid-other", revision=1)
        write_summary(tmp_path, "autoplan", foreign)
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 0),
            "--step-id",
            "f1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "framework",
            "--plan",
            MASTER_REL,
        )
        assert "diverged" in err


class TestFailClosedMarkers:
    @pytest.mark.parametrize(
        "summary",
        [
            {"schema_version": 99, "execution_mode": "current", "lifecycle": "active"},
            {
                "schema_version": 2,
                "execution_mode": "current",
                "chain": "autorun",
                "chain_id": "c",
                "revision": 1,
                "host": "devin",
                "client_surface": "desktop",
                "plan_docs": [PLAN_DOC_REL],
                "round": 1,
                "completed_step_ids": [],
                "lifecycle": "active",
                "updated_at": "t",
                "model_control": "host",
                "permission_effective": "host",
            },
            {
                "schema_version": 2,
                "execution_mode": "current",
                "chain": "autorun",
                "chain_id": "c",
                "revision": 1,
                "host": "devin",
                "client_surface": "desktop",
                "plan_docs": [{"bad": "type"}],
                "round": 1,
                "completed_step_ids": [],
                "lifecycle": "active",
                "step": {"id": "s", "kind": "package", "status": "active", "outcome": None},
                "updated_at": "t",
                "model_control": "host",
                "permission_effective": "host",
            },
            {
                "schema_version": 2,
                "execution_mode": "current",
                "chain": "autorun",
                "chain_id": "c",
                "revision": True,
                "host": "devin",
                "client_surface": "desktop",
                "plan_docs": [PLAN_DOC_REL],
                "round": 1,
                "completed_step_ids": [],
                "lifecycle": "active",
                "step": {"id": "s", "kind": "package", "status": "active", "outcome": None},
                "updated_at": "t",
                "model_control": "host",
                "permission_effective": "host",
            },
            {
                "schema_version": 2,
                "execution_mode": "current",
                "lifecycle": "closed",
                "chain": "autorun",
                "chain_id": "c",
                "revision": "bad",
                "archive": ".spec/autorun/history/" + "a" * 64 + ".json",
                "closed_at": "2026-10-20T00:00:00Z",
                "previous_digest": "a" * 64,
            },
            {
                "schema_version": 2,
                "execution_mode": "current",
                "lifecycle": "closed",
                "chain": "autorun",
                "revision": 7,
                "archive": ".spec/autorun/history/" + "b" * 64 + ".json",
                "closed_at": "2026-10-20T00:00:00Z",
                "previous_digest": "b" * 64,
            },
        ],
    )
    def test_foreign_or_invalid_markers_fail_closed(self, tmp_path, capsys, summary):
        git_init(tmp_path)
        write_summary(tmp_path, "autorun", summary)
        before = tree_bytes(tmp_path)
        code, payload, err = run_cli(
            capsys, "status", "--chain", "autorun", "--root", str(tmp_path), "--format", "json"
        )
        assert "Traceback" not in err
        if code == 0:
            assert payload.get("started") is not True or payload.get("diverged")
        with pytest.raises(ChainSpawnError):
            chain_session.refuse_current_spawn(tmp_path, "autorun")
        assert tree_bytes(tmp_path) == before

    @pytest.mark.parametrize(
        "summary",
        [
            {"lifecycle": "closed", "chain": "autorun", "revision": "x"},
            {"lifecycle": "closed", "chain": "autorun"},
        ],
    )
    def test_invalid_closed_markers_stay_legacy_domain(self, tmp_path, capsys, summary):
        git_init(tmp_path)
        write_summary(tmp_path, "autorun", summary)
        before = tree_bytes(tmp_path)
        code, payload, err = run_cli(
            capsys, "status", "--chain", "autorun", "--root", str(tmp_path), "--format", "json"
        )
        assert "Traceback" not in err
        if code == 0:
            assert payload.get("started") is not True
        chain_session.refuse_current_spawn(tmp_path, "autorun")
        assert tree_bytes(tmp_path) == before

    def test_spawn_refuses_foreign_marker_before_backend(self, tmp_path, capsys, monkeypatch):
        import autorun_spawn

        git_init(tmp_path)
        write(tmp_path / PLAN_DOC_REL, PLAN_DOC)
        write_summary(tmp_path, "autorun", {"schema_version": 99, "execution_mode": "current", "lifecycle": "active"})
        called = {"backend": False}

        def fail_backend(*a, **k):
            called["backend"] = True
            raise AssertionError("backend must not run for a foreign current marker")

        monkeypatch.setattr(support, "resolve_terminal_backend", fail_backend)
        code = autorun_spawn.main(["spawn", "--root", str(tmp_path), "--host", "codex"])
        assert code == 1
        assert called["backend"] is False

    def test_legacy_audit_malformed_interior_keeps_dry_run(self, tmp_path, capsys, monkeypatch):
        import autorun_spawn

        git_init(tmp_path)
        write(tmp_path / PLAN_DOC_REL, PLAN_DOC)
        make_active_package(tmp_path, "2026-10-01_fix-one", "spec/2026-10-01_fix-one")
        write_summary(tmp_path, "autorun", {"lifecycle": "active", "round": 1, "host": "pi"})
        summary = summary_path(tmp_path, "autorun")
        audit = support.chain_runtime_dir(tmp_path, "autorun") / support.CHAIN_SPAWNS_FILE
        audit.parent.mkdir(parents=True, exist_ok=True)
        audit.write_text(
            "{malformed interior\n"
            + json.dumps(
                {
                    "round": 1,
                    "host": "pi",
                    "host_source": "flag",
                    "plan_docs": [PLAN_DOC_REL],
                    "max_rounds": 5,
                    "feature_fingerprint": "ab" * 32,
                    "permission_mode": "default",
                    "permission_effective": "default",
                    "model": None,
                    "model_injection": None,
                    "spawned_at": "2026-10-20T00:00:00Z",
                    "shell_command": "",
                    "prev_tty": None,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        summary_bytes = summary.read_bytes()
        audit_bytes = audit.read_bytes()
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr("autorun_spawn.session_tty", lambda: "/dev/ttys012")

        def fail_launch(*a, **k):
            raise AssertionError("dry-run must not launch a real session")

        monkeypatch.setattr(support, "spawn_tmux_payload", fail_launch)
        monkeypatch.setattr(autorun_spawn, "run_terminal_open", fail_launch)
        code = autorun_spawn.main(["spawn", "--root", str(tmp_path), "--host", "pi", "--dry-run", "--format", "json"])
        assert code == 0, capsys.readouterr().err
        assert summary.read_bytes() == summary_bytes
        assert audit.read_bytes() == audit_bytes

    def test_dry_run_on_diverged_state_writes_nothing(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        append_journal(tmp_path, "autoplan", "{corrupt interior\n")
        append_journal(tmp_path, "autoplan", journal_row(current_state_dict("autoplan", revision=2)))
        before = tree_bytes(tmp_path)
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 1),
            "--step-id",
            "f2",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "framework",
            "--dry-run",
        )
        assert "diverged" in err
        assert tree_bytes(tmp_path) == before


class TestPathSafetyGuards:
    def test_symlinked_summary_and_journal_refused(self, tmp_path, capsys):
        git_init(tmp_path)
        seed_autoplan(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        sentinel = write(outside / "sentinel.json", "{}")
        chain_dir = tmp_path / ".spec" / "autoplan"
        chain_dir.mkdir(parents=True)
        (chain_dir / "chain.json").symlink_to(sentinel)
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 0),
            "--step-id",
            "f1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "framework",
            "--plan",
            MASTER_REL,
        )
        assert "symlink" in err
        assert sentinel.read_text() == "{}"
        (chain_dir / "chain.json").unlink()
        runtime = tmp_path / ".git" / "spec-chain"
        runtime.mkdir(parents=True)
        chain_runtime = runtime / "autoplan"
        chain_runtime.symlink_to(outside)
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 0),
            "--step-id",
            "f1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "framework",
            "--plan",
            MASTER_REL,
        )
        assert "symlink" in err
        assert not (outside / support.CHAIN_SPAWNS_FILE).exists()

    def test_symlinked_tmp_and_dotdot_plan_paths_refused(self, tmp_path, capsys):
        git_init(tmp_path)
        link_dir = tmp_path / "link"
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        write(real_dir / "master.md", MASTER_SEED)
        link_dir.symlink_to(real_dir)
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 0),
            "--step-id",
            "f1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "framework",
            "--plan",
            "link/../master.md",
        )
        assert "symlink" in err
        assert not summary_path(tmp_path, "autoplan").exists()

    @pytest.mark.parametrize(
        "target,status_refuses",
        [
            ("summary", True),
            ("runtime", True),
            ("journal", True),
            ("lock", False),
            ("tmp", False),
            ("detail", True),
        ],
    )
    def test_symlinked_current_paths_refused(self, tmp_path, capsys, target, status_refuses):
        autoplan_to_ready(capsys, tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        sentinel = write(outside / "sentinel.dat", "sentinel")
        runtime_dir = support.chain_runtime_dir(tmp_path, "autoplan")
        if target == "summary":
            link = summary_path(tmp_path, "autoplan")
            link.unlink()
            link.symlink_to(sentinel)
        elif target == "runtime":
            real = tmp_path / ".git" / "spec-chain"
            real.rename(outside / "spec-chain")
            real.symlink_to(outside / "spec-chain", target_is_directory=True)
        elif target == "journal":
            link = journal_path(tmp_path, "autoplan")
            link.unlink()
            link.symlink_to(sentinel)
        elif target == "lock":
            link = runtime_dir / support.CHAIN_LOCK_FILE
            if link.exists() or link.is_symlink():
                link.unlink()
            link.symlink_to(sentinel)
        elif target == "tmp":
            link = summary_path(tmp_path, "autoplan").with_name(".chain.json.tmp")
            link.symlink_to(sentinel)
        else:
            link = tmp_path / DETAIL_REL
            link.unlink()
            link.symlink_to(sentinel)
        before = tree_bytes(outside)
        code, payload, err = run_cli(
            capsys, "status", "--chain", "autoplan", "--root", str(tmp_path), "--format", "json"
        )
        assert "Traceback" not in err
        if status_refuses:
            assert (
                code == 1
                or payload.get("diverged")
                or payload.get("next_action") == "blocked"
                or payload.get("ready_valid") is False
            )
        dry = cli_err(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 6), "--dry-run")
        assert "symlink" in dry
        mutation = cli_err(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 6))
        assert "symlink" in mutation
        assert sentinel.read_text() == "sentinel"
        assert tree_bytes(outside) == before

    def test_legal_dotdot_master_link_stays_in_root(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        shared = ".spec/plans/shared-detail.md"
        write(tmp_path / MASTER_REL, MASTER_EXPANDED + "- [shared](../shared-detail.md)\n")
        expand = (tmp_path / MASTER_REL).read_text()
        assert "../shared-detail.md" in expand
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        finish(capsys, tmp_path, "autoplan", revision=1)
        write(tmp_path / shared, DETAIL_DOC)
        payload = autoplan_begin(capsys, tmp_path, step_id="d1", kind="detail", revision=2, target=shared)
        assert payload["state"]["step"]["target"] == shared

    def test_no_raw_owner_in_tracked_or_runtime_files(self, tmp_path, capsys):
        autoplan_to_ready(capsys, tmp_path)
        cli_ok(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 6))
        blob = runtime_blob(tmp_path, "autoplan")
        assert OWNER_A.encode("utf-8") not in blob
        assert OWNER_HASH_A.encode("utf-8") in blob
        lock = support.chain_runtime_dir(tmp_path, "autoplan") / support.CHAIN_LOCK_FILE
        assert OWNER_A.encode("utf-8") not in (lock.read_bytes() if lock.exists() else b"")


class TestWorkspaceAndTakeover:
    @requires_git
    def test_subdirectory_root_and_bare_repo_refused(self, tmp_path, capsys):
        git_init(tmp_path)
        nested = tmp_path / "nested"
        nested.mkdir()
        write(nested / MASTER_REL, MASTER_SEED)
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autoplan", nested, OWNER_A, 0),
            "--step-id",
            "f1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "framework",
            "--plan",
            MASTER_REL,
        )
        assert "worktree root" in err
        bare = tmp_path / "bare.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autoplan", bare, OWNER_A, 0),
            "--step-id",
            "f1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "framework",
        )
        assert "work tree" in err or "worktree" in err
        assert not (nested / ".spec" / "autoplan" / "chain.json").exists()

    def test_summary_only_ready_takeover_then_close(self, tmp_path, capsys):
        autoplan_to_ready(capsys, tmp_path)
        journal_path(tmp_path, "autoplan").unlink()
        err = cli_err(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_B, 6))
        assert "--takeover" in err or "owner" in err
        resumed = cli_ok(capsys, "resume", *mut_args("autoplan", tmp_path, OWNER_B, 6), "--takeover")
        assert resumed["state"]["lifecycle"] == "ready"
        assert resumed["state"]["pass"] == 3
        closed = cli_ok(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_B, 7))
        assert closed["status"] == "closed"

    def test_summary_only_complete_step_takeover_then_begin(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        expand_master(tmp_path)
        finish(capsys, tmp_path, "autoplan", revision=1)
        journal_path(tmp_path, "autoplan").unlink()
        resumed = cli_ok(capsys, "resume", *mut_args("autoplan", tmp_path, OWNER_B, 2), "--takeover")
        assert resumed["state"]["step"]["status"] == "complete"
        payload = autoplan_begin(
            capsys, tmp_path, step_id="d1", kind="detail", revision=3, owner=OWNER_B, target=DETAIL_REL
        )
        assert payload["state"]["pass"] == 2

    def test_status_reports_takeover_for_foreign_owner(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        own = cli_ok(
            capsys, "status", "--chain", "autoplan", "--root", str(tmp_path), "--owner", OWNER_A, "--format", "json"
        )
        assert own["owner_match"] is True
        assert own["next_action"] == "continue"
        other = cli_ok(
            capsys, "status", "--chain", "autoplan", "--root", str(tmp_path), "--owner", OWNER_B, "--format", "json"
        )
        assert other["owner_match"] is False
        assert other["next_action"] == "takeover"


class TestAutorunProofOrdering:
    def test_next_begin_refuses_after_plan_drift(self, tmp_path, capsys):
        git_init(tmp_path)
        write(tmp_path / PLAN_DOC_REL, PLAN_DOC)
        slug_a = "2026-10-01_fix-one"
        slug_b = "2026-10-02_fix-two"
        make_active_package(tmp_path, slug_a, f"spec/{slug_a}")
        git_commit_all(tmp_path, "seed")
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "-b", f"spec/{slug_a}"], check=True)
        autorun_begin(capsys, tmp_path, slug_a, step_id=slug_a, revision=0)
        archive_package(tmp_path, slug_a)
        finish(capsys, tmp_path, "autorun", revision=1)
        write(tmp_path / PLAN_DOC_REL, PLAN_DOC.replace("First package", "First package renamed"))
        make_active_package(tmp_path, slug_b, f"spec/{slug_b}")
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "main"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "-b", f"spec/{slug_b}"], check=True)
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autorun", tmp_path, OWNER_A, 2),
            "--step-id",
            slug_b,
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--slug",
            slug_b,
        )
        assert "planning features changed" in err
        assert read_summary(tmp_path, "autorun")["round"] == 1

    def test_wrong_branch_resume_refused(self, tmp_path, capsys):
        git_init(tmp_path)
        write(tmp_path / PLAN_DOC_REL, PLAN_DOC)
        slug = "2026-10-01_fix-one"
        make_active_package(tmp_path, slug, f"spec/{slug}")
        git_commit_all(tmp_path, "seed")
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "-b", f"spec/{slug}"], check=True)
        autorun_begin(capsys, tmp_path, slug, step_id=slug, revision=0)
        cli_ok(capsys, "pause", *mut_args("autorun", tmp_path, OWNER_A, 1), "--reason", "hold")
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "main"], check=True)
        err = cli_err(capsys, "resume", *mut_args("autorun", tmp_path, OWNER_A, 2))
        assert "branch" in err.lower()
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", f"spec/{slug}"], check=True)
        resumed = cli_ok(capsys, "resume", *mut_args("autorun", tmp_path, OWNER_A, 2))
        assert resumed["state"]["step"]["status"] == "active"

    def test_close_refuses_unfinished_step_despite_archive(self, tmp_path, capsys):
        git_init(tmp_path)
        write(tmp_path / PLAN_DOC_REL, "- [ ] Only feature\n  - boundary: x\n  - verify: check\n")
        slug = "2026-10-01_fix-one"
        make_active_package(tmp_path, slug, f"spec/{slug}")
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "-b", f"spec/{slug}"], check=True)
        autorun_begin(capsys, tmp_path, slug, step_id=slug, revision=0)
        archive_package(tmp_path, slug)
        write(tmp_path / PLAN_DOC_REL, "- [x] Only feature\n  - boundary: x\n  - verify: check\n")
        before = tree_bytes(tmp_path)
        err = cli_err(capsys, "close", *mut_args("autorun", tmp_path, OWNER_A, 1))
        assert "completed passed package step" in err
        assert tree_bytes(tmp_path) == before

    def test_ready_claim_revalidated_on_duplicate_receipt(self, tmp_path, capsys):
        autoplan_to_ready(capsys, tmp_path)
        write(tmp_path / MASTER_REL, MASTER_EXPANDED.replace("Feature alpha", "Feature alpha renamed"))
        err = cli_err(
            capsys,
            "finish",
            *mut_args("autoplan", tmp_path, OWNER_A, 5),
            "--review-mode",
            "main",
            "--review-note",
            "structure and consistency reviewed",
        )
        assert "no longer verifies" in err

    def test_complete_lifecycle_blocked_by_new_active_package(self, tmp_path, capsys):
        git_init(tmp_path)
        write(tmp_path / PLAN_DOC_REL, "- [ ] Only feature\n  - boundary: x\n  - verify: check\n")
        slug = "2026-10-01_fix-one"
        make_active_package(tmp_path, slug, f"spec/{slug}")
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "-b", f"spec/{slug}"], check=True)
        autorun_begin(capsys, tmp_path, slug, step_id=slug, revision=0)
        archive_package(tmp_path, slug)
        write(tmp_path / PLAN_DOC_REL, "- [x] Only feature\n  - boundary: x\n  - verify: check\n")
        done = finish(capsys, tmp_path, "autorun", revision=1)
        assert done["state"]["lifecycle"] == "complete"
        stray = tmp_path / ".spec" / "specs" / "2026-10-05_stray-pkg"
        stray.mkdir(parents=True)
        status = cli_ok(capsys, "status", "--chain", "autorun", "--root", str(tmp_path), "--format", "json")
        assert status["next_action"] == "blocked"
        err = cli_err(capsys, "close", *mut_args("autorun", tmp_path, OWNER_A, 2))
        assert "active package" in err


class TestPlanScopeSemantics:
    def test_master_link_removed_during_detail_refuses(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        expand_master(tmp_path)
        finish(capsys, tmp_path, "autoplan", revision=1)
        autoplan_begin(capsys, tmp_path, step_id="d1", kind="detail", revision=2, target=DETAIL_REL)
        write(tmp_path / DETAIL_REL, DETAIL_DOC)
        write(tmp_path / MASTER_REL, MASTER_EXPANDED.replace("\n- [alpha detail](01-alpha.md)\n", "\n"))
        err = cli_err(capsys, "finish", *mut_args("autoplan", tmp_path, OWNER_A, 3))
        assert "not linked" in err
        assert read_summary(tmp_path, "autoplan")["step"]["status"] == "active"

    def test_detail_heading_removed_after_review_begin_refuses(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        expand_master(tmp_path)
        finish(capsys, tmp_path, "autoplan", revision=1)
        autoplan_begin(capsys, tmp_path, step_id="d1", kind="detail", revision=2, target=DETAIL_REL)
        write(tmp_path / DETAIL_REL, DETAIL_DOC)
        finish(capsys, tmp_path, "autoplan", revision=3)
        autoplan_begin(capsys, tmp_path, step_id="r1", kind="review", revision=4)
        write(tmp_path / DETAIL_REL, "# Alpha detail\n\nonly prose now\n")
        err = cli_err(
            capsys,
            "finish",
            *mut_args("autoplan", tmp_path, OWNER_A, 5),
            "--review-mode",
            "main",
            "--review-note",
            "checked",
        )
        assert "five-section" in err

    def test_multi_master_second_incomplete_detail_blocks(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        other_rel = ".spec/plans/2026-01-03_other/master.md"
        other_detail = ".spec/plans/2026-01-03_other/01-beta.md"
        write(
            tmp_path / other_rel,
            "# Other master\n\n## Goal\n\n- goal b\n\n"
            "- [ ] Feature beta\n  - boundary: b\n  - verify: check\n\n"
            "- [beta detail](01-beta.md)\n",
        )
        plan_arg = f"{MASTER_REL},{other_rel}"
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=plan_arg)
        expand_master(tmp_path)
        finish(capsys, tmp_path, "autoplan", revision=1)
        autoplan_begin(capsys, tmp_path, step_id="d1", kind="detail", revision=2, target=DETAIL_REL)
        write(tmp_path / DETAIL_REL, DETAIL_DOC)
        finish(capsys, tmp_path, "autoplan", revision=3)
        autoplan_begin(capsys, tmp_path, step_id="d2", kind="detail", revision=4, target=other_detail)
        write(tmp_path / other_detail, DETAIL_DOC)
        finish(capsys, tmp_path, "autoplan", revision=5)
        autoplan_begin(capsys, tmp_path, step_id="r1", kind="review", revision=6)
        write(tmp_path / other_detail, "# Beta detail\n\n## Business Logic\n- logic\n")
        err = cli_err(
            capsys,
            "finish",
            *mut_args("autoplan", tmp_path, OWNER_A, 7),
            "--review-mode",
            "main",
            "--review-note",
            "checked",
        )
        assert "five-section" in err or "incomplete" in err.lower()

    def test_archive_backlink_is_not_a_live_detail(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        archived = tmp_path / ".spec" / "specs" / "archive" / "2026-09-01_old"
        write(archived / "spec.md", "# old\n")
        write(
            tmp_path / MASTER_REL,
            "# Demo master\n\n## Goal\n\n- build the demo\n\n"
            "- [ ] Feature alpha\n  - boundary: alpha\n  - verify: check\n\n"
            "- [delivered](../../specs/archive/2026-09-01_old/spec.md)\n",
        )
        payload = autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        assert payload["state"]["revision"] == 1
        finish(capsys, tmp_path, "autoplan", revision=1)
        review = autoplan_begin(capsys, tmp_path, step_id="r1", kind="review", revision=2)
        assert review["state"]["step"]["kind"] == "review"

    def test_standalone_master_reaches_ready(self, tmp_path, capsys):
        git_init(tmp_path)
        write(
            tmp_path / MASTER_REL,
            "# Demo master\n\n## Goal\n\n- build the demo\n\n"
            "- [ ] Feature alpha\n  - boundary: alpha\n  - verify: check\n",
        )
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        finish(capsys, tmp_path, "autoplan", revision=1)
        autoplan_begin(capsys, tmp_path, step_id="r1", kind="review", revision=2)
        ready = finish(
            capsys,
            tmp_path,
            "autoplan",
            revision=3,
            extra=("--review-mode", "main", "--review-note", "checked"),
        )
        assert ready["state"]["lifecycle"] == "ready"

    @pytest.mark.parametrize(
        "plan_arg",
        [
            f"{MASTER_REL},{MASTER_REL}",
            f".spec/plans/2026-01-02_demo/./master.md,{MASTER_REL}",
        ],
    )
    def test_duplicate_selected_plan_docs_rejected(self, tmp_path, capsys, plan_arg):
        seed_autoplan(tmp_path)
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 0),
            "--step-id",
            "f1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "framework",
            "--plan",
            plan_arg,
        )
        assert "more than once" in err
        assert not summary_path(tmp_path, "autoplan").exists()

    def test_autorun_selected_plan_without_checkboxes_refused(self, tmp_path, capsys):
        git_init(tmp_path)
        write(tmp_path / PLAN_DOC_REL, PLAN_DOC)
        write(tmp_path / ".spec" / "plans" / "notes.md", "# prose only, no checkboxes\n")
        slug = "2026-10-01_fix-one"
        make_active_package(tmp_path, slug, f"spec/{slug}")
        subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "-b", f"spec/{slug}"], check=True)
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autorun", tmp_path, OWNER_A, 0),
            "--step-id",
            "s1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--slug",
            slug,
            "--plan",
            f"{PLAN_DOC_REL},.spec/plans/notes.md",
        )
        assert "no feature checkboxes" in err
        assert not summary_path(tmp_path, "autorun").exists()
        assert not journal_path(tmp_path, "autorun").exists()


def real_v2_archived_repo(capsys, parent: Path, slug: str) -> Path:
    import smoke_test_spec_skill as smoke

    repo = parent / slug
    repo.mkdir()
    git_init(repo)
    write(repo / "src" / "module.py", "VALUE = 1\n")
    git_commit_all(repo, "init")
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "init_spec_package.py"),
            "--root",
            str(repo),
            "--slug",
            slug,
            "--title",
            "V2 package",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    package = repo / ".spec" / "specs" / slug
    smoke.populate_spec(package / "spec.md")
    smoke.populate_tasks(package / "tasks.md", slug)
    smoke.mark_all_tasks_done(package / "tasks.md")
    smoke.mark_success_criteria_passed(package / "spec.md")
    write(repo / PLAN_DOC_REL, "- [ ] Only feature\n  - boundary: x\n  - verify: check\n")
    autorun_begin(capsys, repo, slug, step_id=slug, revision=0, plan=PLAN_DOC_REL)
    git_commit_all(repo, "seed state")
    smoke.record_runs(slug, repo)
    archived = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "complete_spec_package.py"),
            "--root",
            str(repo),
            "--slug",
            slug,
            "--archive",
        ],
        capture_output=True,
        text=True,
    )
    assert archived.returncode == 0, archived.stderr + archived.stdout
    return repo


class TestRealV2Package:
    @requires_git
    def test_finish_accepts_real_v2_archive_and_stale_runs_refuse(self, tmp_path, capsys):
        import smoke_test_spec_skill as smoke

        slug = "2026-10-01_fix-one"
        git_init(tmp_path)
        write(tmp_path / "src" / "module.py", "VALUE = 1\n")
        git_commit_all(tmp_path, "init")
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "init_spec_package.py"),
                "--root",
                str(tmp_path),
                "--slug",
                slug,
                "--title",
                "V2 package",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        package = tmp_path / ".spec" / "specs" / slug
        smoke.populate_spec(package / "spec.md")
        smoke.populate_tasks(package / "tasks.md", slug)
        smoke.mark_all_tasks_done(package / "tasks.md")
        smoke.mark_success_criteria_passed(package / "spec.md")
        plan_rel = ".spec/plans/2026-02-03_build/master.md"
        write(tmp_path / plan_rel, "- [ ] Only feature\n  - boundary: x\n  - verify: check\n")
        autorun_begin(capsys, tmp_path, slug, step_id=slug, revision=0, plan=plan_rel)
        git_commit_all(tmp_path, "seed state")
        smoke.record_runs(slug, tmp_path)
        archived = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "complete_spec_package.py"),
                "--root",
                str(tmp_path),
                "--slug",
                slug,
                "--archive",
            ],
            capture_output=True,
            text=True,
        )
        assert archived.returncode == 0, archived.stderr + archived.stdout
        write(tmp_path / plan_rel, "- [x] Only feature\n  - boundary: x\n  - verify: check\n")
        done = finish(capsys, tmp_path, "autorun", revision=1)
        assert done["state"]["lifecycle"] == "complete"
        assert done["state"]["step"]["proof"]["archive_hashes"]

    @requires_git
    @pytest.mark.parametrize("tamper", ["stale", "failed", "missing"])
    def test_finish_rejects_bad_run_evidence(self, tmp_path, capsys, tamper):
        slug = f"2026-10-02_{tamper}-runs"
        repo = real_v2_archived_repo(capsys, tmp_path, slug)
        runs = repo / ".spec" / "specs" / "archive" / slug / "runs.jsonl"
        if tamper == "stale":
            write(repo / "src" / "module.py", "VALUE = 2\n")
            git_commit_all(repo, "stale the runs")
        elif tamper == "failed":
            latest = json.loads(runs.read_text(encoding="utf-8").splitlines()[-1])
            latest["exit"] = 1
            with runs.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(latest) + "\n")
        else:
            runs.unlink()
        write(repo / PLAN_DOC_REL, "- [x] Only feature\n  - boundary: x\n  - verify: check\n")
        err = cli_err(capsys, "finish", *mut_args("autorun", repo, OWNER_A, 1))
        assert "acceptance gates" in err


class TestExportedHelperIsolation:
    @requires_bash
    @requires_git
    def test_exported_full_helper_leaves_source_runtime_untouched(self, tmp_path):
        snapshot = {}
        for base in (
            ROOT / ".spec" / "autoplan",
            ROOT / ".spec" / "autorun",
            ROOT / ".git" / "spec-chain",
        ):
            if base.exists():
                for path in sorted(base.rglob("*")):
                    if path.is_file() and not path.is_symlink():
                        snapshot[str(path)] = path.read_bytes()

        home = tmp_path / "home"
        home.mkdir()
        result = run_installer(home, {"INSTALL_HOSTS": "devin", "INSTALL_PROFILE": "full"})
        assert result.returncode == 0, result.stderr
        helper = home / ".config" / "devin" / "skills" / "spec" / "scripts" / "chain_session.py"
        assert helper.is_file()

        target = tmp_path / "project"
        target.mkdir()
        git_init(target)
        write(target / MASTER_REL, MASTER_EXPANDED)
        begin = subprocess.run(
            [
                sys.executable,
                str(helper),
                "begin",
                "--chain",
                "autoplan",
                "--root",
                str(target),
                "--owner",
                OWNER_A,
                "--expected-revision",
                "0",
                "--chain-id",
                "test-chain",
                "--step-id",
                "f1",
                "--client",
                "devin",
                "--surface",
                "desktop",
                "--kind",
                "framework",
                "--plan",
                MASTER_REL,
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            env={**os.environ, **GIT_COMMIT_ENV},
        )
        assert begin.returncode == 0, begin.stderr
        status = subprocess.run(
            [
                sys.executable,
                str(helper),
                "status",
                "--chain",
                "autoplan",
                "--root",
                str(target),
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
        )
        assert status.returncode == 0, status.stderr
        assert json.loads(status.stdout)["started"] is True

        for base in (
            ROOT / ".spec" / "autoplan",
            ROOT / ".spec" / "autorun",
            ROOT / ".git" / "spec-chain",
        ):
            if base.exists():
                for path in sorted(base.rglob("*")):
                    if path.is_file() and not path.is_symlink():
                        assert snapshot.pop(str(path), None) == path.read_bytes()
        assert snapshot == {}
