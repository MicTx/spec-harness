"""Shared execution budgets for Spec Harness and its release tooling."""

from __future__ import annotations

import os

PROJECT_VERIFY_TIMEOUT = 1800


def project_verify_timeout() -> int:
    """Return the wall-clock budget for a project-level verification command.

    The value is operator-overridable for slower CI hosts without changing the
    shipped contract. Invalid or non-positive overrides fall back to the
    built-in budget.
    """
    raw = os.environ.get("SPEC_PROJECT_VERIFY_TIMEOUT", "")
    try:
        value = int(raw)
    except ValueError:
        return PROJECT_VERIFY_TIMEOUT
    return value if value > 0 else PROJECT_VERIFY_TIMEOUT
