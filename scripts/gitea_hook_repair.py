#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Repair broken server-side Git hooks on a remote Gitea instance.

Runs the three official maintenance actions documented in the Gitea FAQ
(Push Hook / Webhook / Actions aren't running) through the site admin API:

- ``sync_repo_branches``  : sync missed branches from git data to database
- ``sync_repo_tags``      : sync tags from git data to database
- ``resync_all_hooks``    : resynchronize pre-receive/update/post-receive
                            hooks of all repositories

Endpoint: ``POST {base_url}/api/v1/admin/cron/{task}`` (requires a site
admin token). See Gitea PR #20029 for the API contract.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

# Official cron task names, in FAQ-documented order.
GITEA_HOOK_REPAIR_TASKS = (
    "sync_repo_branches",
    "sync_repo_tags",
    "resync_all_hooks",
)

DEFAULT_TIMEOUT_SECONDS = 300.0


class GiteaHookRepairError(RuntimeError):
    """Raised when the repair sequence cannot be completed."""


class GiteaCredentialsMissing(GiteaHookRepairError):
    """Raised when no admin token is available for the repair call."""


def _normalize_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise GiteaHookRepairError("Gitea base URL is empty")
    if not normalized.startswith(("http://", "https://")):
        raise GiteaHookRepairError(f"Gitea base URL must start with http:// or https://: {base_url}")
    return normalized


def _post_cron_task(
    base_url: str,
    token: str,
    task: str,
    timeout: float,
) -> None:
    url = f"{base_url}/api/v1/admin/cron/{task}"
    request = urllib.request.Request(
        url,
        data=b"",
        method="POST",
        headers={
            "Authorization": f"token {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
    except urllib.error.HTTPError as exc:
        raise GiteaHookRepairError(f"Gitea repair task {task} failed with HTTP {exc.code}: {exc.reason}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise GiteaHookRepairError(f"Gitea repair task {task} could not reach {base_url}: {exc}") from exc
    if not 200 <= status < 300:
        raise GiteaHookRepairError(f"Gitea repair task {task} returned unexpected status {status}")


def repair_gitea_hooks(
    base_url: str,
    token: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    tasks: tuple[str, ...] = GITEA_HOOK_REPAIR_TASKS,
) -> list[str]:
    """Run every repair task in order; return executed task names.

    Raises :class:`GiteaCredentialsMissing` when the token is empty, and
    :class:`GiteaHookRepairError` for any HTTP or network failure. The
    first failure aborts the remaining tasks (fail fast, no partial
    success masking).
    """
    normalized_url = _normalize_base_url(base_url)
    if not token or not token.strip():
        raise GiteaCredentialsMissing("Gitea admin token is missing; cannot run hook repair tasks")
    executed: list[str] = []
    for task in tasks:
        _post_cron_task(normalized_url, token.strip(), task, timeout)
        executed.append(task)
    return executed


def repair_tasks_payload(executed: list[str]) -> str:
    """Render a compact JSON summary of executed repair tasks."""
    return json.dumps({"tasks": executed}, ensure_ascii=False)
