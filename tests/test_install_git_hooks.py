import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "install_git_hooks.py"
HOOK_SOURCES = {"pre-commit": ROOT / "hooks" / "pre-commit", "pre-push": ROOT / "hooks" / "pre-push"}
CHECKER_PLACEHOLDER = "__SPEC_CHECK_ALL_SCRIPT_PLACEHOLDER__"
PYTHON_PLACEHOLDER = "__SPEC_PYTHON_PLACEHOLDER__"
ZERO_SHA = "0000000000000000000000000000000000000000"

# Stub checker: appends its own resolved path (as invoked) to the file named by
# SPEC_STUB_MARKER and always passes. The "SPEC stub-slug" line feeds pre-push
# scope parsing so the hook reaches its final checker invocation.
STUB_CHECKER = """import os
import sys
from pathlib import Path

marker = Path(os.environ["SPEC_STUB_MARKER"])
with marker.open("a", encoding="utf-8") as handle:
    handle.write(str(Path(sys.argv[0]).resolve()) + "\\n")
print("SPEC stub-slug")
"""


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    # Containers ship no global git identity: a bare `git merge` there exits
    # 128 ("unable to auto-detect email address"). Give every fixture repo a
    # local identity so commits and merges never depend on ambient config.
    subprocess.run(
        ["git", "config", "user.email", "spec@example.com"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Spec Test"],
        cwd=root,
        check=True,
        capture_output=True,
    )


def run_install(root: Path, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        env=env,
    )


def make_initial_commit(repo: Path, name: str = "payload.txt", message: str = "initial") -> None:
    (repo / name).write_text(name + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", name], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=spec@example.com", "-c", "user.name=Spec Test", "commit", "-m", message],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def write_stub_checker(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(STUB_CHECKER, encoding="utf-8")
    return path.resolve()


def render_hook_template(hook_name: str, target: Path, checker_path) -> None:
    """Copy a source hook template; optionally render the checker placeholder."""
    content = HOOK_SOURCES[hook_name].read_text(encoding="utf-8")
    if checker_path is not None:
        content = content.replace(CHECKER_PLACEHOLDER, str(checker_path))
    content = content.replace(PYTHON_PLACEHOLDER, sys.executable)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    target.chmod(0o755)


def run_hook(hook: Path, repo: Path, marker: Path, extra_env=None, push_input=None):
    env = dict(os.environ)
    env.pop("SPEC_CHECK_ALL_SCRIPT", None)
    env["SPEC_STUB_MARKER"] = str(marker)
    for key, value in (extra_env or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        ["sh", str(hook)],
        cwd=repo,
        input="" if push_input is None else push_input,
        capture_output=True,
        text=True,
        env=env,
    )


def selected_checkers(marker: Path) -> set:
    return {line for line in marker.read_text(encoding="utf-8").splitlines() if line}


def head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def push_refs_input(repo: Path) -> str:
    """One branch-creation ref line so pre-push reaches its checker calls."""
    return f"refs/heads/main {head_sha(repo)} refs/heads/main {ZERO_SHA}\n"


# Resolution is two-level and discriminable: level 1 lives outside the
# repo in SPEC_CHECK_ALL_SCRIPT (echoed to stderr when in effect), level 2
# is the rendered placeholder value. In-repo content — an in-project copy
# at scripts/ or a bootstrap sibling next to .git — is never a resolver;
# it only proves the attack negative (repo-side stubs stay unexecuted) and
# an unrendered template fails closed (see the fail-closed tests below).
RESOLUTION_CASES = {
    "env_overrides_rendered": {
        "env": True,
        "in_project": True,
        "rendered": True,
        "bootstrap": True,
        "expected": "env",
    },
    "rendered_beats_in_project_and_bootstrap": {
        "env": False,
        "in_project": True,
        "rendered": True,
        "bootstrap": True,
        "expected": "rendered",
    },
    "env_without_rendered": {
        "env": True,
        "in_project": True,
        "rendered": False,
        "bootstrap": True,
        "expected": "env",
    },
}


@pytest.mark.parametrize("repo_dirname", ["plain-repo", "repo with space"])
@pytest.mark.parametrize("hook_name", ["pre-commit", "pre-push"])
@pytest.mark.parametrize("case_name", sorted(RESOLUTION_CASES))
def test_hook_checker_resolution_order(tmp_path, repo_dirname, hook_name, case_name):
    case = RESOLUTION_CASES[case_name]
    repo = tmp_path / repo_dirname
    init_repo(repo)
    make_initial_commit(repo)

    hook = repo / ".git" / "hooks" / hook_name
    candidates = {
        "env": write_stub_checker(tmp_path / "env override" / "check_all_spec_packages.py") if case["env"] else None,
        "in_project": write_stub_checker(repo / "scripts" / "check_all_spec_packages.py")
        if case["in_project"]
        else None,
        "rendered": write_stub_checker(tmp_path / "skill copy" / "scripts" / "check_all_spec_packages.py")
        if case["rendered"]
        else None,
        "bootstrap": write_stub_checker(hook.parent.parent / "scripts" / "check_all_spec_packages.py")
        if case["bootstrap"]
        else None,
    }
    render_hook_template(hook_name, hook, candidates["rendered"])
    expected = candidates[case["expected"]]

    marker = tmp_path / "marker.log"
    result = run_hook(
        hook,
        repo,
        marker,
        extra_env={"SPEC_CHECK_ALL_SCRIPT": str(candidates["env"]) if candidates["env"] else None},
        push_input=push_refs_input(repo) if hook_name == "pre-push" else None,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert selected_checkers(marker) == {str(expected)}
    if case["env"]:
        # The override must announce itself instead of swapping silently.
        assert "SPEC_CHECK_ALL_SCRIPT override" in result.stderr
    else:
        assert "SPEC_CHECK_ALL_SCRIPT override" not in result.stderr


@pytest.mark.parametrize("hook_name", ["pre-commit", "pre-push"])
def test_hook_fails_closed_when_no_checker_resolves(tmp_path, hook_name):
    """An unrendered template fails closed even when repo-side look-alikes
    exist: the in-project scripts/ copy and the bootstrap sibling next to
    .git are both ignored — repository content cannot rescue or replace
    the rendered checker."""
    repo = tmp_path / "repo"
    init_repo(repo)
    make_initial_commit(repo)
    hook = repo / ".git" / "hooks" / hook_name
    write_stub_checker(repo / "scripts" / "check_all_spec_packages.py")
    write_stub_checker(hook.parent.parent / "scripts" / "check_all_spec_packages.py")
    render_hook_template(hook_name, hook, None)

    marker = tmp_path / "marker.log"
    result = run_hook(hook, repo, marker, push_input=push_refs_input(repo) if hook_name == "pre-push" else None)

    assert result.returncode != 0
    assert "checker not found" in result.stdout + result.stderr
    # Neither repo-side stub executed.
    assert not marker.exists()


def test_hook_fails_closed_when_env_override_points_nowhere(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    hook = repo / ".git" / "hooks" / "pre-commit"
    rendered = write_stub_checker(tmp_path / "skill" / "scripts" / "check_all_spec_packages.py")
    render_hook_template("pre-commit", hook, rendered)

    marker = tmp_path / "marker.log"
    result = run_hook(hook, repo, marker, extra_env={"SPEC_CHECK_ALL_SCRIPT": str(tmp_path / "missing" / "checker.py")})

    # A broken explicit override must fail closed instead of falling through.
    assert result.returncode != 0
    assert "checker not found" in result.stdout + result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("repo_dirname", ["plain-repo", "repo with space"])
@pytest.mark.parametrize("hook_name", ["pre-commit", "pre-push"])
def test_installed_hook_uses_rendered_path_without_in_project_copy(tmp_path, repo_dirname, hook_name):
    """End-to-end install rendering: the installer-rendered absolute path is
    selected at runtime when the target repository has no in-project copy."""
    skill = tmp_path / "skill pkg"
    hooks_src = skill / "hooks"
    scripts_src = skill / "scripts"
    hooks_src.mkdir(parents=True)
    scripts_src.mkdir(parents=True)
    for name in ("pre-commit", "pre-push"):
        (hooks_src / name).write_text(HOOK_SOURCES[name].read_text(encoding="utf-8"), encoding="utf-8")
    (scripts_src / "install_git_hooks.py").write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    rendered_checker = write_stub_checker(scripts_src / "check_all_spec_packages.py")

    repo = tmp_path / repo_dirname
    init_repo(repo)
    make_initial_commit(repo)

    result = subprocess.run(
        [sys.executable, str(scripts_src / "install_git_hooks.py"), "--root", str(repo)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    hook = repo / ".git" / "hooks" / hook_name
    assert hook.is_file()
    assert CHECKER_PLACEHOLDER not in hook.read_text(encoding="utf-8")
    assert not (repo / "scripts" / "check_all_spec_packages.py").exists()

    marker = tmp_path / "marker.log"
    run_result = run_hook(hook, repo, marker, push_input=push_refs_input(repo) if hook_name == "pre-push" else None)

    assert run_result.returncode == 0, run_result.stdout + run_result.stderr
    assert selected_checkers(marker) == {str(rendered_checker)}


def test_installs_git_hooks_and_is_idempotent(tmp_path):
    init_repo(tmp_path)

    first = run_install(tmp_path)
    second = run_install(tmp_path)

    assert first.returncode == 0
    assert second.returncode == 0
    for name in ("pre-commit", "pre-push"):
        hook = tmp_path / ".git" / "hooks" / name
        assert hook.is_file()
        assert hook.stat().st_mode & 0o111


def test_installs_into_linked_worktree_git_path(tmp_path):
    main = tmp_path / "main"
    init_repo(main)
    (main / "README.md").write_text("main\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=main, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Spec Test",
            "commit",
            "-m",
            "initial",
        ],
        cwd=main,
        check=True,
        capture_output=True,
    )
    worktree = tmp_path / "linked"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(worktree)],
        cwd=main,
        check=True,
        capture_output=True,
    )

    result = run_install(worktree)
    hooks_path = subprocess.run(
        ["git", "rev-parse", "--git-path", "hooks"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    hooks = Path(hooks_path)
    if not hooks.is_absolute():
        hooks = worktree / hooks

    assert result.returncode == 0
    assert (hooks / "pre-commit").is_file()
    assert (hooks / "pre-push").is_file()


def test_ignores_injected_git_repository_environment(tmp_path):
    target = tmp_path / "target"
    other = tmp_path / "other"
    init_repo(target)
    init_repo(other)
    env = {
        **os.environ,
        "GIT_DIR": str(other / ".git"),
        "GIT_WORK_TREE": str(other),
    }

    result = run_install(target, env=env)

    assert result.returncode == 0
    assert (target / ".git" / "hooks" / "pre-commit").is_file()
    assert not (other / ".git" / "hooks" / "pre-commit").exists()


def test_refuses_hooks_path_outside_repository(tmp_path):
    repo = tmp_path / "repo"
    outside = tmp_path / "outside-hooks"
    init_repo(repo)
    subprocess.run(
        ["git", "config", "core.hooksPath", str(outside)],
        cwd=repo,
        check=True,
    )

    result = run_install(repo)

    assert result.returncode != 0
    assert "outside repository or Git common dir" in result.stderr
    assert not outside.exists()


def test_refuses_to_overwrite_project_owned_hook(tmp_path):
    init_repo(tmp_path)
    target = tmp_path / ".git" / "hooks" / "pre-commit"
    target.write_text("#!/bin/sh\necho project-owned\n", encoding="utf-8")

    result = run_install(tmp_path)

    assert result.returncode != 0
    assert "refusing to overwrite project-owned hook" in result.stderr
    assert target.read_text(encoding="utf-8") == "#!/bin/sh\necho project-owned\n"


# --- Branch-aware gate routing (AUD-07): WIP lint channel vs full gate ---

# Records the full argv tail of each checker invocation so tests can assert
# which channel (--lint-only vs --legacy-baseline/--index) actually ran.
ARGS_CHECKER = """import os
import sys
from pathlib import Path

marker = Path(os.environ["SPEC_STUB_MARKER"])
with marker.open("a", encoding="utf-8") as handle:
    handle.write(" ".join(sys.argv[1:]) + "\\n")
print("SPEC stub-slug")
"""


def write_args_checker(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ARGS_CHECKER, encoding="utf-8")
    return path.resolve()


def gate_invocations(marker: Path) -> list:
    if not marker.exists():
        return []
    return [line for line in marker.read_text(encoding="utf-8").splitlines() if line]


def _setup_repo_on_main(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    init_repo(repo)
    make_initial_commit(repo)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, check=True, capture_output=True)
    return repo


def _stage(repo: Path, name: str, content: str = "staged\n") -> None:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "--", name], cwd=repo, check=True, capture_output=True)


def test_pre_commit_lint_only_on_spec_branch_wip(tmp_path):
    repo = _setup_repo_on_main(tmp_path)
    subprocess.run(["git", "checkout", "-q", "-b", "spec/wip-probe"], cwd=repo, check=True, capture_output=True)
    hook = repo / ".git" / "hooks" / "pre-commit"
    rendered = write_args_checker(tmp_path / "skill" / "scripts" / "check_all_spec_packages.py")
    render_hook_template("pre-commit", hook, rendered)
    _stage(repo, "wip.txt")

    marker = tmp_path / "marker.log"
    result = run_hook(hook, repo, marker)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "WIP commit on integration branch" in result.stderr
    assert "structural lint only" in result.stderr
    invocations = gate_invocations(marker)
    assert len(invocations) == 1
    assert "--lint-only" in invocations[0]
    assert "--legacy-baseline" not in invocations[0]
    assert "--index" not in invocations[0]


def test_pre_commit_full_gate_on_main_branch(tmp_path):
    repo = _setup_repo_on_main(tmp_path)
    hook = repo / ".git" / "hooks" / "pre-commit"
    rendered = write_args_checker(tmp_path / "skill" / "scripts" / "check_all_spec_packages.py")
    render_hook_template("pre-commit", hook, rendered)
    _stage(repo, "mainline.txt")

    marker = tmp_path / "marker.log"
    result = run_hook(hook, repo, marker)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "WIP commit on integration branch" not in result.stderr
    invocations = gate_invocations(marker)
    # Full gate: worktree + index dual run, both with the legacy baseline.
    assert len(invocations) == 2
    assert all("--legacy-baseline" in line for line in invocations)
    assert all("--lint-only" not in line for line in invocations)


def test_pre_commit_full_gate_on_non_spec_branch(tmp_path):
    repo = _setup_repo_on_main(tmp_path)
    subprocess.run(["git", "checkout", "-q", "-b", "feature/other"], cwd=repo, check=True, capture_output=True)
    hook = repo / ".git" / "hooks" / "pre-commit"
    rendered = write_args_checker(tmp_path / "skill" / "scripts" / "check_all_spec_packages.py")
    render_hook_template("pre-commit", hook, rendered)
    _stage(repo, "feature.txt")

    marker = tmp_path / "marker.log"
    result = run_hook(hook, repo, marker)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "WIP commit on integration branch" not in result.stderr
    assert all("--lint-only" not in line for line in gate_invocations(marker))


def test_pre_commit_archive_paths_keep_full_gate_on_spec_branch(tmp_path):
    repo = _setup_repo_on_main(tmp_path)
    subprocess.run(["git", "checkout", "-q", "-b", "spec/wip-probe"], cwd=repo, check=True, capture_output=True)
    hook = repo / ".git" / "hooks" / "pre-commit"
    rendered = write_args_checker(tmp_path / "skill" / "scripts" / "check_all_spec_packages.py")
    render_hook_template("pre-commit", hook, rendered)
    _stage(repo, ".spec/specs/archive/2026-01-01_probe/spec.md", "# archived\n")

    marker = tmp_path / "marker.log"
    result = run_hook(hook, repo, marker)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "WIP commit on integration branch" not in result.stderr
    invocations = gate_invocations(marker)
    assert len(invocations) == 4
    assert all("--lint-only" in line for line in invocations[:2])
    assert "--index" not in invocations[0] and "--index" in invocations[1]
    assert all("--lint-only" not in line for line in invocations[2:])
    assert all("--legacy-baseline" in line for line in invocations[2:])
    assert all("--slug 2026-01-01_probe" in line for line in invocations[2:])
    assert "--index" not in invocations[2] and "--index" in invocations[3]


def test_pre_push_releases_spec_branch_handoff(tmp_path):
    repo = _setup_repo_on_main(tmp_path)
    hook = repo / ".git" / "hooks" / "pre-push"
    rendered = write_args_checker(tmp_path / "skill" / "scripts" / "check_all_spec_packages.py")
    render_hook_template("pre-push", hook, rendered)

    marker = tmp_path / "marker.log"
    push_input = f"refs/heads/spec/wip-exp05 {head_sha(repo)} refs/heads/spec/wip-exp05 {ZERO_SHA}\n"
    result = run_hook(hook, repo, marker, push_input=push_input)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "releasing in-flight push" in result.stderr
    assert "refs/heads/spec/wip-exp05" in result.stderr
    assert gate_invocations(marker) == []


def test_pre_push_full_gate_for_main_push(tmp_path):
    repo = _setup_repo_on_main(tmp_path)
    hook = repo / ".git" / "hooks" / "pre-push"
    rendered = write_args_checker(tmp_path / "skill" / "scripts" / "check_all_spec_packages.py")
    render_hook_template("pre-push", hook, rendered)

    marker = tmp_path / "marker.log"
    result = run_hook(hook, repo, marker, push_input=push_refs_input(repo))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "releasing in-flight push" not in result.stderr
    assert gate_invocations(marker)


def test_pre_push_full_gate_for_non_spec_branch_push(tmp_path):
    repo = _setup_repo_on_main(tmp_path)
    hook = repo / ".git" / "hooks" / "pre-push"
    rendered = write_args_checker(tmp_path / "skill" / "scripts" / "check_all_spec_packages.py")
    render_hook_template("pre-push", hook, rendered)

    marker = tmp_path / "marker.log"
    push_input = f"refs/heads/feature/other {head_sha(repo)} refs/heads/feature/other {ZERO_SHA}\n"
    result = run_hook(hook, repo, marker, push_input=push_input)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "releasing in-flight push" not in result.stderr
    assert gate_invocations(marker)


# --- AUD-07 sandbox replays: EXP-05 (WIP commit + in-flight push + main
# gate intact) against the real checker, not a stub ---


def _install_real_hooks(repo: Path) -> None:
    real_checker = ROOT / "scripts" / "check_all_spec_packages.py"
    for name in ("pre-commit", "pre-push"):
        render_hook_template(name, repo / ".git" / "hooks" / name, real_checker)


def _make_active_package(repo: Path, slug: str) -> None:
    package = repo / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(
        "# WIP 探针包 - 项目范围\n\n## 1. 问题定义\n- **项目目标**：探针\n", encoding="utf-8"
    )
    (package / "tasks.md").write_text(
        "- [ ] 探针任务\n  - boundary: probe\n  - verify: manual: probe\n", encoding="utf-8"
    )
    (package / "checklist.md").write_text("## 验收证据\n- 脚本验证：pending\n", encoding="utf-8")


def _git_commit(repo: Path, message: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.email=spec@example.com",
            "-c",
            "user.name=Spec Test",
            "commit",
            *extra,
            "-m",
            message,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def test_replay_exp05_wip_commit_and_inflight_push(tmp_path):
    repo = _setup_repo_on_main(tmp_path)
    _install_real_hooks(repo)
    _make_active_package(repo, "2026-10-12_wip-probe")

    subprocess.run(["git", "checkout", "-q", "-b", "spec/wip-exp05"], cwd=repo, check=True, capture_output=True)
    _stage(repo, "wip.txt")
    committed = _git_commit(repo, "wip handoff\n\nSpec-WIP: 2026-10-12_wip-probe")

    assert committed.returncode == 0, committed.stdout + committed.stderr
    assert "structural lint only" in committed.stderr
    log = subprocess.run(
        ["git", "log", "-1", "--format=%B"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout
    assert "Spec-WIP: 2026-10-12_wip-probe" in log

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=remote, check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repo, check=True, capture_output=True)
    pushed = subprocess.run(["git", "push", "origin", "spec/wip-exp05"], cwd=repo, capture_output=True, text=True)

    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    assert "releasing in-flight push" in pushed.stderr
    listed = subprocess.run(
        ["git", "ls-remote", "--heads", str(remote)], cwd=repo, check=True, capture_output=True, text=True
    ).stdout
    assert "refs/heads/spec/wip-exp05" in listed


def test_replay_exp05_main_gate_not_weakened(tmp_path):
    repo = _setup_repo_on_main(tmp_path)
    _install_real_hooks(repo)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=remote, check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repo, check=True, capture_output=True)
    # Setup push before any package exists (--no-verify is setup, not the
    # behavior under test).
    subprocess.run(["git", "push", "--no-verify", "-q", "origin", "main"], cwd=repo, check=True, capture_output=True)

    _make_active_package(repo, "2026-10-12_wip-probe")

    # A main-branch commit with an unresolved active package stays blocked
    # by the full pre-commit gate.
    _stage(repo, "mainline.txt")
    blocked_commit = _git_commit(repo, "main change\n\nSpec: 2026-10-12_wip-probe")
    assert blocked_commit.returncode != 0
    assert "check gates not passed" in blocked_commit.stdout + blocked_commit.stderr

    # And a main-branch push stays blocked by the full pre-push gate: the
    # in-flight package reaches the main tree via --no-verify setup commits
    # (WIP docs + a sneaky code change attributed to its slug), and the push
    # gate still refuses to ship an unarchived active package.
    subprocess.run(["git", "add", ".spec"], cwd=repo, check=True, capture_output=True)
    assert _git_commit(repo, "wip docs\n\nSpec-WIP: 2026-10-12_wip-probe", "--no-verify").returncode == 0
    _stage(repo, "sneaky.py", "import os  # unreviewed payload\n")
    sneaked = _git_commit(repo, "main change\n\nSpec: 2026-10-12_wip-probe", "--no-verify")
    assert sneaked.returncode == 0
    blocked_push = subprocess.run(["git", "push", "origin", "main"], cwd=repo, capture_output=True, text=True)
    assert blocked_push.returncode != 0
    combined = blocked_push.stdout + blocked_push.stderr
    assert "active package must be archived" in combined


# --- F9 (AUD-06): footer-attribution interval warnings (observation period) ---

# Records each invocation's argv and stays SILENT on the scope call, so the
# hook enters the footer channel exactly like a code-only push would.
FOOTER_ARGS_CHECKER = """import os
import sys
from pathlib import Path

marker = Path(os.environ["SPEC_STUB_MARKER"])
with marker.open("a", encoding="utf-8") as handle:
    handle.write(" ".join(sys.argv[1:]) + "\\n")
"""


def write_footer_args_checker(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FOOTER_ARGS_CHECKER, encoding="utf-8")
    return path.resolve()


def _footer_commit(repo: Path, name: str, message: str, slug: str) -> None:
    (repo / name).write_text(name + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", name], cwd=repo, check=True, capture_output=True)
    committed = subprocess.run(
        [
            "git",
            "-c",
            "user.email=spec@example.com",
            "-c",
            "user.name=Spec Test",
            "commit",
            "-m",
            message,
            "-m",
            f"Spec: {slug}",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert committed.returncode == 0, committed.stdout + committed.stderr


def _sha(repo: Path, ref: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", ref], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _write_valid_archive(repo: Path, slug: str, base: str, head: str) -> None:
    package = repo / ".spec" / "specs" / "archive" / slug
    package.mkdir(parents=True)
    (package / "spec.md").write_text(
        "# Footer Fixture - 项目范围\n## 1. 问题定义\n- **项目目标**：footer fixture\n- **目标用户**：维护者\n"
        "- **核心价值**：归属可信\n## 2. 假设与待确认\n### 2.1 已确认事实\n- fixture\n### 2.2 关键假设\n"
        "- standard git\n### 2.3 待确认问题\n- 无\n## 3. 功能范围\n### 3.3 不在范围内\n- production\n"
        "## 4. 最小实现路径\n- archive\n- attribute\n- verify\n",
        encoding="utf-8",
    )
    (package / "tasks.md").write_text(
        "- [x] Footer fixture\n  - boundary: test repository\n  - verify: push gate\n", encoding="utf-8"
    )
    (package / "checklist.md").write_text(
        "- [x] Verified\n## 验收证据\n- 脚本验证：pytest\n**验收结果**：通过\n", encoding="utf-8"
    )
    (package / "completion-summary.md").write_text(
        f"# {slug} - 完成总结\n## 交付结论\n- 完成\n## 假设回顾\n- verified\n## 交付范围\n- delivered\n"
        "## 简化决策\n- simple\n## 变更边界\n- fixture\n## 验证证据\n- pytest\n## 门禁证据\n- passed\n"
        f"## Git 记录\n- 提交：pending local commit\n- 提交区间：{base}..{head}\n- 推送：not-run\n"
        '## 问题处置\n```json\n{"version":1,"issues":[]}\n```\n',
        encoding="utf-8",
    )


def test_pre_push_passes_footer_window_base_in_footer_channel(tmp_path):
    repo = _setup_repo_on_main(tmp_path)
    initial = head_sha(repo)
    _footer_commit(repo, "sneaky.txt", "code-only change", OLD_SLUG := "2026-08-01_legacy-audit-fixture")
    hook = repo / ".git" / "hooks" / "pre-push"
    rendered = write_footer_args_checker(tmp_path / "skill" / "scripts" / "check_all_spec_packages.py")
    render_hook_template("pre-push", hook, rendered)

    marker = tmp_path / "marker.log"
    push_input = f"refs/heads/main {head_sha(repo)} refs/heads/main {initial}\n"
    result = run_hook(hook, repo, marker, push_input=push_input)

    assert result.returncode == 0, result.stdout + result.stderr
    invocations = gate_invocations(marker)
    gate_calls = [line for line in invocations if "--require-archived" in line]
    assert len(gate_calls) == 1
    assert f"--slug {OLD_SLUG}" in gate_calls[0]
    assert f"--footer-window-base {initial}" in gate_calls[0]


def test_pre_push_touched_channel_omits_footer_window_base(tmp_path):
    repo = _setup_repo_on_main(tmp_path)
    initial = head_sha(repo)
    _stage(repo, ".spec/specs/2026-08-01_legacy-audit-fixture/spec.md", "# archived\n")
    _git_commit(repo, "archive touch\n\nSpec: 2026-08-01_legacy-audit-fixture")
    hook = repo / ".git" / "hooks" / "pre-push"
    rendered = write_args_checker(tmp_path / "skill" / "scripts" / "check_all_spec_packages.py")
    render_hook_template("pre-push", hook, rendered)

    marker = tmp_path / "marker.log"
    push_input = f"refs/heads/main {head_sha(repo)} refs/heads/main {initial}\n"
    result = run_hook(hook, repo, marker, push_input=push_input)

    assert result.returncode == 0, result.stdout + result.stderr
    gate_calls = [line for line in gate_invocations(marker) if "--require-archived" in line]
    assert gate_calls
    # The touched-package channel never enters the footer interval judge.
    assert all("--footer-window-base" not in line for line in gate_calls)


def test_pre_push_new_ref_footer_channel_omits_footer_window_base(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    # A brand-new remote ref has no window base to compare against: every
    # in-range commit (no remotes configured) must carry a Spec footer.
    _footer_commit(repo, "payload.txt", "initial", "2026-08-01_legacy-audit-fixture")
    _footer_commit(repo, "followup.txt", "follow-up for old package", "2026-08-01_legacy-audit-fixture")
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, check=True, capture_output=True)
    hook = repo / ".git" / "hooks" / "pre-push"
    rendered = write_footer_args_checker(tmp_path / "skill" / "scripts" / "check_all_spec_packages.py")
    render_hook_template("pre-push", hook, rendered)

    marker = tmp_path / "marker.log"
    push_input = f"refs/heads/main {head_sha(repo)} refs/heads/main {ZERO_SHA}\n"
    result = run_hook(hook, repo, marker, push_input=push_input)

    assert result.returncode == 0, result.stdout + result.stderr
    gate_calls = [line for line in gate_invocations(marker) if "--require-archived" in line]
    assert gate_calls
    assert all("--footer-window-base" not in line for line in gate_calls)


def test_replay_exp11_footer_interval_warning_warn_only(tmp_path):
    # EXP-11 replay against the real checker: local merge to main, then push —
    # a code-only commit citing an old archived slug is NOT blocked, but the
    # pre-push gate names the evidence triple on stderr (observation period).
    repo = _setup_repo_on_main(tmp_path)
    _install_real_hooks(repo)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=remote, check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "push", "--no-verify", "-q", "origin", "main"], cwd=repo, check=True, capture_output=True)
    initial = head_sha(repo)

    slug = "2026-08-01_legacy-audit-fixture"
    _write_valid_archive(repo, slug, initial, initial)
    subprocess.run(["git", "add", ".spec"], cwd=repo, check=True, capture_output=True)
    assert _git_commit(repo, "chore(spec): archive old fixture", "--no-verify").returncode == 0
    archive_head = head_sha(repo)
    subprocess.run(["git", "push", "--no-verify", "-q", "origin", "main"], cwd=repo, check=True, capture_output=True)

    (repo / "sneaky.py").write_text("import os\nos.system('echo pwned')\n", encoding="utf-8")
    subprocess.run(["git", "add", "sneaky.py"], cwd=repo, check=True, capture_output=True)
    assert _git_commit(repo, "feat: innocent change\n\nSpec: " + slug, "--no-verify").returncode == 0

    pushed = subprocess.run(["git", "push", "origin", "main"], cwd=repo, capture_output=True, text=True)
    combined = pushed.stdout + pushed.stderr
    assert pushed.returncode == 0, combined
    assert "spec gate warning: footer attribution cites an archive delivered before this push window" in combined
    assert f"- slug: {slug}" in combined
    assert f"- archive commit range: {initial}..{initial}" in combined
    assert f"- push window base: {archive_head}" in combined
    assert "hard failure" in combined
    listed = subprocess.run(
        ["git", "ls-remote", "--heads", "origin"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout
    assert head_sha(repo) in listed


def test_replay_delivery_push_with_interval_no_warning(tmp_path):
    # A delivery push (archive + code landing together) runs the touched
    # channel: the recorded interval never triggers a footer warning.
    repo = _setup_repo_on_main(tmp_path)
    _install_real_hooks(repo)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=remote, check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "push", "--no-verify", "-q", "origin", "main"], cwd=repo, check=True, capture_output=True)
    initial = head_sha(repo)

    slug = "2026-08-01_delivery-fixture"
    subprocess.run(["git", "checkout", "-qb", "spec/deliver-exp11"], cwd=repo, check=True, capture_output=True)
    _footer_commit(repo, "delivery.txt", "feat: delivery", slug)
    delivery_head = head_sha(repo)
    _write_valid_archive(repo, slug, initial, delivery_head)
    subprocess.run(["git", "add", ".spec"], cwd=repo, check=True, capture_output=True)
    assert _git_commit(repo, "chore(spec): archive delivery fixture", "--no-verify").returncode == 0
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "merge", "--no-ff", "--no-verify", "-q", "spec/deliver-exp11"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    pushed = subprocess.run(["git", "push", "origin", "main"], cwd=repo, capture_output=True, text=True)
    combined = pushed.stdout + pushed.stderr
    assert pushed.returncode == 0, combined
    assert "spec gate warning" not in combined
