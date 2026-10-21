"""Shared runtime payload contract for the exporter and agent-plugin packager."""

from __future__ import annotations

ROOT_FILES = ("SKILL.md", "install.sh", "pyproject.toml", "LICENSE")
COPY_DIRS = ("agents", "hooks", "references", "server", "slots")
IGNORE_PATTERNS = ("__pycache__", "*.pyc", ".DS_Store")
SCRIPT_BLACKLIST = frozenset(
    {
        "export_skill_package.py",
        "lint_workflows.py",
        "export_public_repo.py",
        "build_release.py",
        "package_agent_plugin.py",
        "skill_watermark.py",
        "path_safety.py",
        "payload_contract.py",
        "verify_install_parity.py",
    }
)

#: Install profiles (planning F16 sub-step 1, AUD-18): ``core`` ships the
#: kernel surface — the runtime tree minus extension components — while
#: ``full`` restores the complete component set byte-for-byte. Stage-command
#: filtering is the installer's own concern (USER_STAGES live in install.sh);
#: this contract covers the payload tree only.
PROFILE_VALUES = ("core", "full")

#: Component directories excluded from the runtime tree under ``core``
#: (planning F16 sub-step 2, AUD-18): ``agents``/``slots`` ride the extension
#: surface, and ``server/`` is the optional always-on companion service.
CORE_EXCLUDED_DIRS = ("agents", "server", "slots")

#: Chain-family scripts that exist only to spawn/recycle fresh agent sessions
#: (autorun/autoplan recursion, chain state, window sweeps). They belong to
#: the extension surface: the kernel gates never import or subprocess them,
#: and a core install must not ship them.
CORE_EXCLUDED_SCRIPTS = frozenset(
    {
        "autorun_spawn.py",
        "autoplan_spawn.py",
        "autoplan_gate.py",
        "chain_spawn_support.py",
        "chain_recovery.py",
        "chain_lifecycle.py",
        "chain_session.py",
        "sweep_chain_windows.py",
    }
)


def copy_dirs_for_profile(profile: str) -> tuple[str, ...]:
    """The COPY_DIRS subset a given install profile ships."""
    if profile == "core":
        return tuple(name for name in COPY_DIRS if name not in CORE_EXCLUDED_DIRS)
    if profile == "full":
        return COPY_DIRS
    raise ValueError(f"unknown install profile: {profile} (expected {' or '.join(PROFILE_VALUES)})")


def excluded_scripts_for_profile(profile: str) -> frozenset[str]:
    """Scripts/*.py names the given profile leaves out (core: chain family)."""
    if profile == "core":
        return CORE_EXCLUDED_SCRIPTS
    if profile == "full":
        return frozenset()
    raise ValueError(f"unknown install profile: {profile} (expected {' or '.join(PROFILE_VALUES)})")
