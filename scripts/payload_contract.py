"""Single source of truth for the runtime skill payload."""

from __future__ import annotations

ROOT_FILES = ("SKILL.md", "install.sh", "pyproject.toml", "LICENSE")
COPY_DIRS = ("agents", "hooks", "references")
IGNORE_PATTERNS = ("__pycache__", "*.pyc", ".DS_Store")
SCRIPT_BLACKLIST = frozenset(
    {
        "export_skill_package.py",
        "lint_workflows.py",
        "export_public_repo.py",
        "public_secret_scan.py",
        "build_release.py",
        "package_agent_plugin.py",
        "skill_watermark.py",
        "path_safety.py",
        "verify_install_parity.py",
    }
)

# Legacy install-profile values remain accepted by installer and exporter, but
# both names resolve to this exact same payload. There is no core/full split.
PROFILE_VALUES = ("core", "full")


def _cli(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Emit the runtime payload contract.")
    parser.add_argument("query", choices=("copy-dirs", "profiles"))
    parser.add_argument("--profile", default="full", choices=PROFILE_VALUES)
    args = parser.parse_args(argv)
    if args.query == "profiles":
        print(" ".join(PROFILE_VALUES))
    elif args.query == "copy-dirs":
        print(" ".join(COPY_DIRS))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_cli())
