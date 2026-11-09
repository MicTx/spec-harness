"""Shared runtime payload contract for the exporter and agent-plugin packager."""

from __future__ import annotations

ROOT_FILES = ("SKILL.md", "install.sh", "pyproject.toml", "LICENSE")
COPY_DIRS = ("agents", "hooks", "references", "slots")
IGNORE_PATTERNS = ("__pycache__", "*.pyc", ".DS_Store")
#: Maintainer-side scripts that never ship in the runtime payload. The
#: release/export/signing tools now live under .maintainer/tools/release and
#: are listed here anyway so a leftover copy inside an install still reads as
#: stale upgrade dirt (verify_install_parity). export_skill_package and
#: path_safety stay in scripts/ as the install-time packager; payload_contract
#: must ship because install.sh queries it at install time.
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

#: Install profiles (planning F16 sub-step 1, AUD-18): ``core`` ships the
#: kernel surface — the runtime tree minus extension components — while
#: ``full`` restores the complete component set byte-for-byte. Stage-command
#: filtering is the installer's own concern (USER_STAGES live in install.sh);
#: this contract covers the payload tree only.
PROFILE_VALUES = ("core", "full")

#: Component directories excluded from the runtime tree under ``core``
#: (planning F16 sub-step 2, AUD-18): ``agents``/``slots`` ride the extension
#: surface.
CORE_EXCLUDED_DIRS = ("agents", "slots")

#: Files kept from an otherwise excluded component under ``core``. Host skill
#: discovery reads ``agents/openai.yaml`` (listing text and the implicit-
#: invocation policy), so dropping the whole ``agents/`` directory would also drop
#: the metadata that lets a host offer the skill for work that never names it.
#: The agent contracts next to it (orchestrator, planner, ...) stay extension
#: surface. Keep this in sync with CORE_KEPT_FILES in install.sh.
CORE_KEPT_FILES = ("agents/openai.yaml",)

#: Chain scripts (autoplan/autorun) belong to the full profile; the kernel
#: gates never import them.
CORE_EXCLUDED_SCRIPTS = frozenset({"autoplan_gate.py", "chain_state.py"})


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


def _cli(argv: list[str] | None = None) -> int:
    """Single-source profile lists for install.sh.

    The installer queries this module instead of carrying its own copies of
    the core-exclusion lists, so the profile contract lives in exactly one
    place. Output is one shell-quoted token list per line so bash can read it
    into arrays without a JSON dependency:

      payload_contract.py excluded-dirs --profile core      -> agents slots
      payload_contract.py excluded-scripts --profile core   -> autoplan_gate.py chain_state.py
      payload_contract.py kept-files                        -> agents/openai.yaml
      payload_contract.py copy-dirs --profile full          -> agents hooks references slots
    """
    import argparse

    parser = argparse.ArgumentParser(description="Emit the install profile payload lists for install.sh.")
    parser.add_argument(
        "query",
        choices=("excluded-dirs", "excluded-scripts", "kept-files", "copy-dirs", "profiles"),
    )
    parser.add_argument("--profile", default="full", choices=PROFILE_VALUES)
    args = parser.parse_args(argv)

    if args.query == "profiles":
        print(" ".join(PROFILE_VALUES))
    elif args.query == "excluded-dirs":
        print(" ".join(CORE_EXCLUDED_DIRS) if args.profile == "core" else "")
    elif args.query == "excluded-scripts":
        print(" ".join(sorted(excluded_scripts_for_profile(args.profile))))
    elif args.query == "kept-files":
        print(" ".join(CORE_KEPT_FILES) if args.profile == "core" else "")
    elif args.query == "copy-dirs":
        print(" ".join(copy_dirs_for_profile(args.profile)))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_cli())
