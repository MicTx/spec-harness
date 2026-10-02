from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterator

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server" / "server.py"
INSTALLER = ROOT / "server" / "install.sh"


VALID_SUMMARY = """# Archived - 完成总结
## 交付结论
- 完成
## 假设回顾
- verified
## 交付范围
- delivered
## 简化决策
- simple
## 变更边界
- server
## 验证证据
- pytest
## 门禁证据
- passed
## 问题处置
```json
{"version":1,"issues":[]}
```
"""


def write_valid_archive(root: Path, slug: str, *, summary: str = VALID_SUMMARY) -> Path:
    archived = root / ".spec" / "specs" / "archive" / slug
    archived.mkdir(parents=True)
    (archived / "spec.md").write_text(
        """# Server Archive - 项目范围
## 1. 问题定义
- **项目目标**：验证 server archive
- **目标用户**：调用者
- **核心价值**：状态可信
## 2. 假设与待确认
### 2.1 已确认事实
- archived
### 2.2 关键假设
- checker available
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- active execution
## 4. 最小实现路径
- archive
- check
- project
""",
        encoding="utf-8",
    )
    (archived / "tasks.md").write_text("- [x] Archive\n  - boundary: fixture\n  - verify: checker\n", encoding="utf-8")
    (archived / "checklist.md").write_text(
        "- [x] Verified\n## 验收证据\n- 脚本验证：pytest\n**验收结果**：通过\n",
        encoding="utf-8",
    )
    (archived / "completion-summary.md").write_text(summary, encoding="utf-8")
    return archived


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "config", "user.email", "server-test@example.com")
    git(path, "config", "user.name", "Spec server test")
    (path / "README.md").write_text("# server test\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-qm", "initial")
    git(path, "branch", "-M", "main")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(
    url: str,
    *,
    method: str = "GET",
    body: object | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    data = None
    request_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def request_raw(
    url: str,
    *,
    body: bytes,
    content_type: str = "application/json",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    request_headers = {"Content-Type": content_type, **(headers or {})}
    request = urllib.request.Request(
        url,
        data=body,
        headers=request_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@contextmanager
def running_server(
    root: Path,
    *,
    prefix: str = "/v1",
    token: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> Iterator[str]:
    port = free_port()
    extra = extra_env or {}
    environment = {
        **os.environ,
        "SPEC_SKILL_DIR": str(ROOT),
        "ALLOWED_ROOTS": str(root),
        "HOST": "127.0.0.1",
        "PORT": str(port),
        "ROOT_PREFIX": prefix,
        **extra,
    }
    if token is not None:
        environment["SPEC_SERVER_TOKEN"] = token
    elif "SPEC_SERVER_TOKEN" not in extra:
        # A developer shell exporting SPEC_SERVER_TOKEN must not silently
        # turn the no-token servers in these tests into 401 servers.
        environment.pop("SPEC_SERVER_TOKEN", None)
    process = subprocess.Popen(
        [sys.executable, str(SERVER)],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base = f"http://127.0.0.1:{port}{prefix}"
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stderr = process.stderr.read() if process.stderr else ""
                raise AssertionError(f"server exited before health check: {stderr}")
            try:
                status, payload = request_json(f"{base}/health")
                if status == 200 and payload.get("status") == "healthy":
                    break
            except (OSError, urllib.error.URLError):
                time.sleep(0.05)
        else:
            raise AssertionError("server did not become healthy")
        yield base
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_health_and_start_result_contract(tmp_path: Path):
    repo = tmp_path / "project"
    init_repo(repo)

    with running_server(tmp_path) as base:
        status, health = request_json(f"{base}/health")
        assert status == 200
        assert health == {"code": 0, "status": "healthy"}

        started_status, started = request_json(
            f"{base}/start",
            method="POST",
            body={"root": str(repo), "goal": "server probe"},
        )
        assert started_status == 200
        assert started["task_id"].startswith(f"{date.today().isoformat()}_build-server-probe-")
        slug = started["task_id"]
        assert started["slug"] == slug
        assert (repo / ".spec" / "specs" / slug).is_dir()

        query = urllib.parse.urlencode({"task_id": slug, "root": str(repo)})
        result_status, result = request_json(f"{base}/result?{query}")
        assert result_status == 200
        assert result["task_id"] == slug
        assert result["workflow_status"] == "running"
        assert result["stage"] == "status"
        assert result["status_detail"].startswith("# server probe\n")
        assert "## 项目进展" in result["status_detail"]
        assert "固化问题定义、关键假设与非目标：进行" in result["status_detail"]
        assert "## 流水线" not in result["status_detail"]


def test_archived_package_is_completed(tmp_path: Path):
    slug = "2026-08-17_add-server-mode"
    write_valid_archive(tmp_path, slug)

    with running_server(tmp_path) as base:
        query = urllib.parse.urlencode({"task_id": slug, "root": str(tmp_path)})
        status, result = request_json(f"{base}/result?{query}")

    assert status == 200
    assert result["workflow_status"] == "completed"
    assert result["stage"] == "done"
    assert result["decision"] == "closed"
    assert result["status_detail"] == VALID_SUMMARY.strip()
    assert result["closure"] == {"valid": True, "version": 1, "issue_count": 0}


def test_invalid_archive_is_not_reported_completed(tmp_path: Path):
    slug = "2026-08-17_fix-invalid-archive"
    archived = write_valid_archive(tmp_path, slug)
    (archived / "completion-summary.md").unlink()

    with running_server(tmp_path) as base:
        query = urllib.parse.urlencode({"task_id": slug, "root": str(tmp_path)})
        status, result = request_json(f"{base}/result?{query}")

    assert status == 200
    assert result["workflow_status"] == "failed"
    assert result["decision"] == "invalid-archive"
    assert result["closure"]["valid"] is False


def test_v1_archive_projects_closure_metadata_without_truncating_status(tmp_path: Path):
    slug = "2026-08-17_fix-v1-archive"
    padding = "verified " * 300
    summary = f"""# Closed - 完成总结
## 交付结论
- 完成
## 假设回顾
- verified
## 交付范围
- delivered
## 简化决策
- simple
## 变更边界
- server
## 验证证据
- {padding}
## 门禁证据
- passed
## 问题处置
```json
{{"version":1,"issues":[]}}
```
"""
    write_valid_archive(tmp_path, slug, summary=summary)

    with running_server(tmp_path) as base:
        query = urllib.parse.urlencode({"task_id": slug, "root": str(tmp_path)})
        status, result = request_json(f"{base}/result?{query}")

    assert status == 200
    assert result["workflow_status"] == "completed"
    assert result["closure"] == {"valid": True, "version": 1, "issue_count": 0}
    assert len(result["status_detail"]) > 1000


def test_removed_remote_control_routes_return_404(tmp_path: Path):
    with running_server(tmp_path) as base:
        page_status, page = request_json(f"{base}/remote")
        list_status, listed = request_json(f"{base}/remote/agents")
        post_status, posted = request_json(
            f"{base}/remote/agents",
            method="POST",
            body={"name": "gone", "cwd": str(tmp_path)},
        )

    assert page_status == 404
    assert page["error"] == "not found"
    assert list_status == 404
    assert listed["error"] == "not found"
    assert post_status == 404
    assert posted["error"] == "not found"


def test_unknown_and_invalid_result_requests_are_stable(tmp_path: Path):
    with running_server(tmp_path) as base:
        unknown_query = urllib.parse.urlencode({"task_id": "2026-08-17_unknown", "root": str(tmp_path)})
        unknown_status, unknown = request_json(f"{base}/result?{unknown_query}")
        missing_status, missing = request_json(f"{base}/result?root={urllib.parse.quote(str(tmp_path))}")
        invalid_status, invalid = request_json(
            f"{base}/start",
            method="POST",
            body={"root": str(tmp_path), "goal": "x", "slug": "bad+slug"},
        )

    assert unknown_status == 404
    assert unknown["workflow_status"] == "failed"
    assert missing_status == 400
    assert "required" in missing["error"]
    assert invalid_status == 400
    assert invalid["error"] == "invalid slug"


def test_non_object_and_malformed_json_return_400(tmp_path: Path):
    with running_server(tmp_path) as base:
        list_status, list_payload = request_raw(f"{base}/start", body=b"[]")
        null_status, null_payload = request_raw(f"{base}/start", body=b"null")
        malformed_status, malformed = request_raw(f"{base}/start", body=b"{")

    assert list_status == 400
    assert list_payload["error"] == "JSON object required"
    assert null_status == 400
    assert null_payload["error"] == "JSON object required"
    assert malformed_status == 400
    assert malformed["error"] == "invalid json"


def test_allowlist_resolves_symlinks_and_rejects_escape(tmp_path: Path):
    outside = Path(tempfile_dir := tempfile.mkdtemp(prefix="spec-server-outside-", dir=tmp_path.parent))
    link = tmp_path / "outside-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
        with running_server(tmp_path) as base:
            status, payload = request_json(
                f"{base}/start",
                method="POST",
                body={"root": str(link), "goal": "escape"},
            )
        assert status == 400
        assert "ALLOWED_ROOTS" in payload["error"] or "not in" in payload["error"]
    finally:
        link.unlink(missing_ok=True)
        shutil.rmtree(tempfile_dir, ignore_errors=True)


def test_custom_root_prefix_does_not_match_similar_paths(tmp_path: Path):
    with running_server(tmp_path, prefix="/api") as base:
        good_status, good = request_json(f"{base}/health")
        bad_status, bad = request_json(f"{base.replace('/api', '/v1')}/health")

    assert good_status == 200
    assert good["status"] == "healthy"
    assert bad_status == 404
    assert bad["error"] == "not found"


def test_summary_closure_parses_nested_issue_objects():
    sys.path.insert(0, str(ROOT))
    from server import server as adapter

    summary = """## 问题处置
```json
{"version":1,"issues":[{"id":"risk","disposition":"accepted_risk","meta":{"owner":"maintainer"}}]}
```
"""
    assert adapter._summary_closure(summary) == {"version": 1, "issue_count": 1}


def test_goal_slug_is_always_spec_compatible():
    sys.path.insert(0, str(ROOT))
    from server import server as adapter

    for goal in ("C++ API", "用户登录与注册", "!!!", "a" * 200):
        slug = adapter._slug_from_goal(goal)
        assert adapter._validate_slug(slug)
        assert "+" not in slug
        assert len(slug.split("_build-", 1)[-1]) <= 40


def test_auto_slugs_are_unique_per_call():
    sys.path.insert(0, str(ROOT))
    from server import server as adapter

    first = adapter._slug_from_goal("same goal")
    second = adapter._slug_from_goal("same goal")
    assert first != second
    assert adapter._validate_slug(first)
    assert adapter._validate_slug(second)


def test_token_auth_gates_start_and_result(tmp_path: Path):
    repo = tmp_path / "project"
    init_repo(repo)
    token = "sekret-test-token"

    with running_server(tmp_path, token=token) as base:
        no_auth_status, no_auth = request_json(
            f"{base}/start",
            method="POST",
            body={"root": str(repo), "goal": "auth probe"},
        )
        wrong_status, wrong = request_json(
            f"{base}/start",
            method="POST",
            body={"root": str(repo), "goal": "auth probe"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        ok_status, ok = request_json(
            f"{base}/start",
            method="POST",
            body={"root": str(repo), "goal": "auth probe"},
            headers={"Authorization": f"Bearer {token}"},
        )
        health_status, health = request_json(f"{base}/health")

        query = urllib.parse.urlencode({"task_id": ok["task_id"], "root": str(repo)})
        result_no_auth_status, _ = request_json(f"{base}/result?{query}")
        result_auth_status, result_auth = request_json(
            f"{base}/result?{query}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert no_auth_status == 401
    assert no_auth["error"] == "unauthorized"
    assert wrong_status == 401
    assert ok_status == 200
    assert ok["task_id"]
    assert health_status == 200
    assert health["status"] == "healthy"
    assert result_no_auth_status == 401
    assert result_auth_status == 200
    assert result_auth["task_id"] == ok["task_id"]


def test_non_loopback_host_without_token_refuses_to_start():
    port = free_port()
    environment = {
        **os.environ,
        "SPEC_SKILL_DIR": str(ROOT),
        "HOST": "0.0.0.0",
        "PORT": str(port),
    }
    environment.pop("SPEC_SERVER_TOKEN", None)
    proc = subprocess.run(
        [sys.executable, str(SERVER)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 1
    assert "SPEC_SERVER_TOKEN" in proc.stderr


def test_invalid_port_refuses_to_start_without_traceback():
    for port in ("abc", "0", "65536"):
        environment = {
            **os.environ,
            "SPEC_SKILL_DIR": str(ROOT),
            "HOST": "127.0.0.1",
            "PORT": port,
        }
        environment.pop("SPEC_SERVER_TOKEN", None)
        proc = subprocess.run(
            [sys.executable, str(SERVER)],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert proc.returncode == 1, (port, proc.stderr)
        assert "Traceback" not in proc.stderr, (port, proc.stderr)
        assert "PORT" in proc.stderr, (port, proc.stderr)


def test_concurrency_limit_returns_429(tmp_path: Path):
    repo = tmp_path / "project"
    init_repo(repo)

    with running_server(tmp_path, extra_env={"MAX_CONCURRENT_WORK": "0"}) as base:
        start_status, start = request_json(
            f"{base}/start",
            method="POST",
            body={"root": str(repo), "goal": "busy probe"},
        )
        query = urllib.parse.urlencode({"task_id": "2026-08-20_probe", "root": str(repo)})
        result_status, result = request_json(f"{base}/result?{query}")
        health_status, health = request_json(f"{base}/health")

    assert start_status == 429
    assert start["workflow_status"] == "failed"
    assert result_status == 429
    assert health_status == 200


def test_start_retry_distinguishes_package_exists_from_branch_conflict(tmp_path: Path):
    repo = tmp_path / "project"
    init_repo(repo)
    slug = "2026-08-21_retry-probe"

    with running_server(tmp_path) as base:
        # A pre-existing integration branch without a package is a hard init
        # failure; it must not be reported as idempotent "already exists".
        git(repo, "branch", f"spec/{slug}")
        conflict_status, conflict = request_json(
            f"{base}/start",
            method="POST",
            body={"root": str(repo), "goal": "retry probe", "slug": slug},
        )
        assert conflict_status == 500
        assert conflict["error"] == "task initialization failed"
        assert not (repo / ".spec" / "specs" / slug).exists()

        # A genuine retry of an existing package keeps idempotent semantics.
        ok_status, ok = request_json(
            f"{base}/start",
            method="POST",
            body={"root": str(repo), "goal": "retry probe"},
        )
        retry_status, retry = request_json(
            f"{base}/start",
            method="POST",
            body={"root": str(repo), "goal": "retry probe", "slug": ok["task_id"]},
        )

    assert ok_status == 200
    assert retry_status == 200
    assert retry["task_id"] == ok["task_id"]
    assert retry.get("note") == "exists"


def test_same_goal_on_distinct_roots_yields_distinct_task_ids(tmp_path: Path):
    first_repo = tmp_path / "project-a"
    second_repo = tmp_path / "project-b"
    init_repo(first_repo)
    init_repo(second_repo)

    with running_server(tmp_path) as base:
        _, first = request_json(
            f"{base}/start",
            method="POST",
            body={"root": str(first_repo), "goal": "duplicate goal"},
        )
        _, second = request_json(
            f"{base}/start",
            method="POST",
            body={"root": str(second_repo), "goal": "duplicate goal"},
        )

    assert first["task_id"] != second["task_id"]
    assert (first_repo / ".spec" / "specs" / first["task_id"]).is_dir()
    assert (second_repo / ".spec" / "specs" / second["task_id"]).is_dir()


def test_derived_symlink_escape_is_rejected_without_side_effects(tmp_path: Path):
    outside = Path(tempfile.mkdtemp(prefix="spec-server-sym-", dir=tmp_path.parent))
    root = tmp_path / "project"
    root.mkdir()
    spec_dir = root / ".spec"
    slug = "2026-08-20_sym-probe"
    try:
        spec_dir.symlink_to(outside, target_is_directory=True)
        with running_server(tmp_path) as base:
            query = urllib.parse.urlencode({"task_id": slug, "root": str(root)})
            result_status, result = request_json(f"{base}/result?{query}")
            start_status, start = request_json(
                f"{base}/start",
                method="POST",
                body={"root": str(root), "goal": "symlink probe", "slug": slug},
            )

        assert result_status == 400
        assert "symlink" in result["error"] or "escape" in result["error"]
        assert start_status == 400
        assert "symlink" in start["error"] or "escape" in start["error"]
        assert list(outside.iterdir()) == []
    finally:
        spec_dir.unlink(missing_ok=True)
        shutil.rmtree(outside, ignore_errors=True)


def test_server_rejects_oversized_title_before_initialization(tmp_path: Path):
    sys.path.insert(0, str(ROOT))
    from server import server as adapter

    status, payload = adapter.do_start(
        {
            "root": str(tmp_path),
            "goal": "bounded input",
            "title": "x" * (adapter.MAX_TITLE_BYTES + 1),
        }
    )

    assert status == 400
    assert payload["error"] == "title is too large"
    assert not (tmp_path / ".spec").exists()


def test_server_init_disables_repository_hooks(tmp_path: Path):
    repo = tmp_path / "project"
    init_repo(repo)
    marker = tmp_path / "hook-ran"
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\nprintf '%s' touched > '{marker}'\n", encoding="utf-8")
    hook.chmod(0o755)

    sys.path.insert(0, str(ROOT))
    from server import server as adapter

    status, payload = adapter.do_start(
        {"root": str(repo), "goal": "hook isolation", "slug": "2026-08-24_fix-hook-isolation"}
    )

    assert status == 200, payload
    assert not marker.exists()


def test_server_subprocess_output_is_bounded(tmp_path: Path):
    sys.path.insert(0, str(ROOT))
    from server import server as adapter

    code = "import sys; sys.stdout.write('o' * 200000); sys.stderr.write('e' * 200000)"
    status, stdout, stderr = adapter._run(["-c", code], str(tmp_path))

    assert status == 0
    assert len(stdout) == adapter.MAX_SUBPROCESS_OUTPUT_BYTES
    assert len(stderr) == adapter.MAX_SUBPROCESS_OUTPUT_BYTES


def test_run_timeout_is_bounded_even_when_grandchild_holds_pipes(tmp_path: Path):
    """A grandchild inheriting stdout/stderr must not hang _run forever.

    Regression for the drain loop: after the leader is killed on timeout the
    loop used to reset its deadline on every expiry and keep killing the dead
    leader, so the request thread held a work slot indefinitely.
    """
    sys.path.insert(0, str(ROOT))
    from server import server as adapter

    code = (
        "import subprocess, sys, time\n"
        "# Grandchild outlives the leader and keeps both pipes open.\n"
        "grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'])\n"
        "print('leader output', flush=True)\n"
        "grandchild.wait()\n"
    )
    started = time.monotonic()
    status, stdout, stderr = adapter._run(["-c", code], str(tmp_path), timeout=1)
    duration = time.monotonic() - started

    assert duration < 15, f"_run did not return within the post-kill grace window ({duration:.1f}s)"
    assert status != 0, "timed-out leader must report failure (SIGKILL on POSIX, exit 1 on Windows)"
    assert "subprocess timed out" in stderr
    assert "leader output" in stdout


def test_init_failure_does_not_leak_subprocess_output(tmp_path: Path):
    from server import server as adapter

    original_run = adapter._run
    adapter._run = lambda args, cwd: (1, "", "SECRET_LEAK /etc/shadow trace")
    try:
        status, payload = adapter.do_start({"root": str(tmp_path), "goal": "leak probe"})
    finally:
        adapter._run = original_run

    assert status == 500
    assert payload["error"] == "task initialization failed"
    assert "SECRET_LEAK" not in json.dumps(payload)
    assert "/etc/shadow" not in json.dumps(payload)


def run_installer(env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(INSTALLER)],
        cwd=ROOT,
        env={**os.environ, **env_overrides},
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_installer_rejects_invalid_inputs_before_side_effects():
    bad = "evil.example.com\nExecStart=/bin/sh -c 'pwned'"
    rejected = [
        ({"DOMAIN": ""}, "DOMAIN"),
        ({"DOMAIN": "UPPER_case.example.com"}, "DOMAIN"),
        ({"DOMAIN": "no-dots"}, "DOMAIN"),
        ({"DOMAIN": bad}, "DOMAIN"),
        ({"DOMAIN": "spec.example.com", "PORT": "abc"}, "PORT"),
        ({"DOMAIN": "spec.example.com", "PORT": "0"}, "PORT"),
        ({"DOMAIN": "spec.example.com", "PORT": "70000"}, "PORT"),
        ({"DOMAIN": "spec.example.com", "PORT": "8787", "HOST": "$(reboot)"}, "HOST"),
        ({"DOMAIN": "spec.example.com", "PORT": "8787", "HOST": "a\nb"}, "HOST"),
        (
            {"DOMAIN": "spec.example.com", "PORT": "8787", "HOST": "127.0.0.1", "PROJECTS_DIR": "relative/path"},
            "PROJECTS_DIR",
        ),
        (
            {"DOMAIN": "spec.example.com", "PORT": "8787", "HOST": "127.0.0.1", "PROJECTS_DIR": "/srv/../etc"},
            "PROJECTS_DIR",
        ),
        (
            {"DOMAIN": "spec.example.com", "PORT": "8787", "HOST": "127.0.0.1", "PROJECTS_DIR": "/"},
            "PROJECTS_DIR",
        ),
        (
            {"DOMAIN": "spec.example.com", "PORT": "8787", "HOST": "127.0.0.1", "PROJECTS_DIR": "/."},
            "PROJECTS_DIR",
        ),
        (
            {"DOMAIN": "spec.example.com", "PORT": "8787", "HOST": "127.0.0.1", "PROJECTS_DIR": "/srv/./projects"},
            "PROJECTS_DIR",
        ),
    ]
    for overrides, label in rejected:
        base_env = {
            "DOMAIN": "spec.example.com",
            "PORT": "8787",
            "HOST": "127.0.0.1",
            "PROJECTS_DIR": "/srv/spec-projects",
        }
        base_env.update(overrides)
        proc = run_installer(base_env)
        assert proc.returncode != 0, f"expected rejection for {overrides}"
        assert label in proc.stderr, f"expected {label} error for {overrides}: {proc.stderr}"


@pytest.mark.skipif(os.geteuid() == 0, reason="root passes the installer guard")
def test_installer_requires_root(tmp_path: Path):
    proc = run_installer(
        {
            "DOMAIN": "spec.example.com",
            "PORT": "8787",
            "HOST": "127.0.0.1",
            "PROJECTS_DIR": str(tmp_path / "projects"),
        }
    )
    assert proc.returncode != 0
    assert "root" in proc.stderr.lower()


def test_non_ascii_authorization_header_returns_401(tmp_path: Path):
    repo = tmp_path / "project"
    init_repo(repo)

    with running_server(tmp_path, token="sekret") as base:
        status, payload = request_json(
            f"{base}/result?{urllib.parse.urlencode({'task_id': '2026-08-20_nope', 'root': str(repo)})}",
            headers={"Authorization": "Bearer \xff\xfe"},
        )

    assert status == 401
    assert payload["error"] == "unauthorized"


def test_work_slot_is_released_after_request(tmp_path: Path):
    repo = tmp_path / "project"
    init_repo(repo)
    slug = "2026-08-20_slot-probe"
    specs = repo / ".spec" / "specs"
    (specs / slug).mkdir(parents=True)

    with running_server(tmp_path, extra_env={"MAX_CONCURRENT_WORK": "1"}) as base:
        query = urllib.parse.urlencode({"task_id": slug, "root": str(repo)})
        for _ in range(6):
            status, result = request_json(f"{base}/result?{query}")
            assert status == 200, result
            assert result["task_id"] == slug


def test_internal_symlink_archived_package_is_invalid(tmp_path: Path):
    slug = "2026-08-20_inner-link"
    real_root = tmp_path / "real-root"
    real = write_valid_archive(real_root, slug, summary="# inner archive\n")

    archive_dir = tmp_path / ".spec" / "specs" / "archive"
    archive_dir.mkdir(parents=True)
    link = archive_dir / slug
    link.symlink_to(real, target_is_directory=True)

    with running_server(tmp_path) as base:
        query = urllib.parse.urlencode({"task_id": slug, "root": str(tmp_path)})
        status, result = request_json(f"{base}/result?{query}")

    assert status == 200
    assert result["workflow_status"] == "failed"
    assert result["decision"] == "invalid-archive"
    assert result["closure"]["valid"] is False
