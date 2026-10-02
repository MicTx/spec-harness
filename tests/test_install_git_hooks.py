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


# Every resolution level is discriminable: level 1 lives outside the repo in
# SPEC_CHECK_ALL_SCRIPT, level 2 inside the repo at scripts/, level 3 in the
# rendered placeholder value, and level 4 next to the hook (repo/.git/scripts
# when the hook sits in the standard hooks directory).
RESOLUTION_CASES = {
    "env_overrides_all": {
        "env": True,
        "in_project": True,
        "rendered": True,
        "bootstrap": True,
        "expected": "env",
    },
    "in_project_beats_rendered_and_bootstrap": {
        "env": False,
        "in_project": True,
        "rendered": True,
        "bootstrap": True,
        "expected": "in_project",
    },
    "rendered_beats_bootstrap": {
        "env": False,
        "in_project": False,
        "rendered": True,
        "bootstrap": True,
        "expected": "rendered",
    },
    "bootstrap_when_unrendered": {
        "env": False,
        "in_project": False,
        "rendered": False,
        "bootstrap": True,
        "expected": "bootstrap",
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


@pytest.mark.parametrize("hook_name", ["pre-commit", "pre-push"])
def test_hook_fails_closed_when_no_checker_resolves(tmp_path, hook_name):
    repo = tmp_path / "repo"
    init_repo(repo)
    make_initial_commit(repo)
    hook = repo / ".git" / "hooks" / hook_name
    # Unrendered source template, no in-project copy, no bootstrap sibling.
    render_hook_template(hook_name, hook, None)

    marker = tmp_path / "marker.log"
    result = run_hook(hook, repo, marker, push_input=push_refs_input(repo) if hook_name == "pre-push" else None)

    assert result.returncode != 0
    assert "checker not found" in result.stdout + result.stderr
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
