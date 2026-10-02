"""Shared runtime payload contract for the exporter and agent-plugin packager."""

from __future__ import annotations

ROOT_FILES = ("SKILL.md", "install.sh", "pyproject.toml")
COPY_DIRS = ("agents", "hooks", "references", "server", "slots")
IGNORE_PATTERNS = ("__pycache__", "*.pyc", ".DS_Store")
SCRIPT_BLACKLIST = frozenset(
    {
        "export_skill_package.py",
        "export_public_repo.py",
        "build_release.py",
        "package_agent_plugin.py",
        "skill_watermark.py",
        "import_kiro_specs.py",
        "migrate_task_ids.py",
    }
)
