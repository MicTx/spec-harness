#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Spec-NonCommercial
"""Tests for scripts/gitea_hook_repair.py against a local HTTP stub."""

from __future__ import annotations

import http.server
import json
import sys
import threading
import unittest
from pathlib import Path
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from gitea_hook_repair import (  # noqa: E402
    GITEA_HOOK_REPAIR_TASKS,
    GiteaCredentialsMissing,
    GiteaHookRepairError,
    repair_gitea_hooks,
)


class _StubHandler(http.server.BaseHTTPRequestHandler):
    """Records requests; behavior configured via class attributes."""

    server_version = "StubGitea/1.0"

    status_by_task: dict[str, int] = {}
    requests: list[tuple[str, str, str]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        auth = self.headers.get("Authorization") or ""
        task = self.path.rsplit("/", 1)[-1]
        type(self).requests.append((self.command, self.path, auth))
        status = type(self).status_by_task.get(task, 204)
        payload = json.dumps({"task": task, "status": status}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        _ = body

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


class RepairGiteaHooksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _StubHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        _StubHandler.status_by_task = {}
        _StubHandler.requests = []

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def test_runs_all_tasks_in_faq_order(self) -> None:
        executed = repair_gitea_hooks(self.base_url, "tok")
        self.assertEqual(executed, list(GITEA_HOOK_REPAIR_TASKS))
        self.assertEqual(
            [path for _, path, _ in _StubHandler.requests],
            [f"/api/v1/admin/cron/{task}" for task in GITEA_HOOK_REPAIR_TASKS],
        )
        self.assertTrue(all(auth == "token tok" for _, _, auth in _StubHandler.requests))

    def test_all_requests_are_post(self) -> None:
        repair_gitea_hooks(self.base_url, "tok")
        self.assertTrue(all(method == "POST" for method, _, _ in _StubHandler.requests))

    def test_missing_token_fails_before_any_request(self) -> None:
        with self.assertRaises(GiteaCredentialsMissing):
            repair_gitea_hooks(self.base_url, "")
        self.assertEqual(_StubHandler.requests, [])

    def test_whitespace_token_fails_before_any_request(self) -> None:
        with self.assertRaises(GiteaCredentialsMissing):
            repair_gitea_hooks(self.base_url, "   ")
        self.assertEqual(_StubHandler.requests, [])

    def test_http_error_status_aborts_remaining_tasks(self) -> None:
        _StubHandler.status_by_task = {"sync_repo_tags": 403}
        with self.assertRaises(GiteaHookRepairError) as ctx:
            repair_gitea_hooks(self.base_url, "tok")
        self.assertIn("403", str(ctx.exception))
        self.assertIn("sync_repo_tags", str(ctx.exception))
        called_tasks = [path.rsplit("/", 1)[-1] for _, path, _ in _StubHandler.requests]
        self.assertEqual(called_tasks, ["sync_repo_branches", "sync_repo_tags"])

    def test_5xx_status_is_error(self) -> None:
        _StubHandler.status_by_task = {"resync_all_hooks": 500}
        with self.assertRaises(GiteaHookRepairError) as ctx:
            repair_gitea_hooks(self.base_url, "tok")
        self.assertIn("500", str(ctx.exception))

    def test_unreachable_server_raises_repair_error(self) -> None:
        # Port 1 on 127.0.0.1 refuses connections; no server side effects.
        with self.assertRaises(GiteaHookRepairError):
            repair_gitea_hooks("http://127.0.0.1:1", "tok", timeout=2.0)

    def test_empty_base_url_is_rejected(self) -> None:
        with self.assertRaises(GiteaHookRepairError):
            repair_gitea_hooks("", "tok")

    def test_non_http_scheme_is_rejected(self) -> None:
        with self.assertRaises(GiteaHookRepairError):
            repair_gitea_hooks("ftp://example.invalid", "tok")


if __name__ == "__main__":
    unittest.main()
_ = URLError  # keep import for clarity of exception hierarchy intent
