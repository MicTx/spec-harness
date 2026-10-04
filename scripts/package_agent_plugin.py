#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Package this repository's runtime skill as a distributable agent plugin zip.

Two manifest formats are supported (spike-verified 2026-09-02, see
.spec/docs/2026-09-02_add-open-task-package-standard_agent-plugins-spike.md):

- agent-plugins (default): Agent Plugins 1.0, root ``plugin.json`` with a
  closed top-level schema and skills discovered from ``skills/``.
- claude-code: ``.claude-plugin/plugin.json`` manifest with kebab-case name
  and the same ``skills/<name>/SKILL.md`` payload layout.

The skills payload mirrors the layout produced by ``export_skill_package.py``
(runtime skill files + agents/hooks/references/server/slots/scripts trees)
placed under ``skills/<skill-name>/`` so both formats discover it unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from path_safety import UnsafePathError, validate_source_tree  # noqa: E402
from payload_contract import COPY_DIRS, IGNORE_PATTERNS, SCRIPT_BLACKLIST  # noqa: E402

FORMATS = ("agent-plugins", "claude-code")
DEFAULT_FORMAT = "agent-plugins"

TEMPLATE_DIR = "agent-plugin"
TEMPLATE_FILES = {
    "agent-plugins": "plugin.json.template",
    "claude-code": "claude-plugin.json.template",
}
# 清单在插件内的落点（相对插件根）
MANIFEST_RELATIVE_PATH = {
    "agent-plugins": Path("plugin.json"),
    "claude-code": Path(".claude-plugin") / "plugin.json",
}

# Agent Plugins 1.0 清单：顶层字段封闭（规范 §5.2），$schema 必须精确等于该值
AGENT_PLUGINS_SCHEMA_URL = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
AGENT_PLUGINS_TOP_LEVEL_FIELDS = frozenset(
    {
        "$schema",
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "extensions",
    }
)
# 官方机器 schema 的 name 正则（禁 -- 与 ..，仅小写字母数字.-，首尾字母数字）
AGENT_PLUGINS_NAME_PATTERN = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
AGENT_PLUGINS_AUTHOR_FIELDS = frozenset({"name", "email", "url"})
# Claude Code plugin.json：name 为 kebab-case（无空格/控制字符/双向格式字符）
CLAUDE_CODE_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# 与 export_skill_package.py 保持一致的载荷布局（LICENSE 另放插件根，对齐官方示例布局）
SKILL_ROOT_FILES = ("SKILL.md", "install.sh", "pyproject.toml")
PLUGIN_ROOT_FILES = ("LICENSE",)
SKILL_COPY_DIRS = COPY_DIRS
COPY_IGNORE = shutil.ignore_patterns(*IGNORE_PATTERNS)

DEFAULT_NAME = "spec-harness"
DEFAULT_DISPLAY_NAME = "Spec Harness"
PUBLIC_REPOSITORY_URL = "https://github.com/MicTx/spec-harness"
DEFAULT_LICENSE = "AGPL-3.0-or-later"
DEFAULT_KEYWORDS = ["spec", "task-package", "workflow", "agent-skills"]
DEFAULT_OUTPUT_DIR = "dist"

PLACEHOLDER_PATTERN = re.compile(r"\{\{[A-Z_]+\}\}")
# 固定时间戳保证产物可复现（zip 元数据不随构建时刻漂移）
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

PYPROJECT_VERSION_PATTERN = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)
FRONTMATTER_NAME_PATTERN = re.compile(r"^name:\s*(\S+)\s*$", re.MULTILINE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Package this repository's runtime skill as a distributable agent plugin zip.",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root to package from (default: current directory)",
    )
    parser.add_argument(
        "--format",
        choices=FORMATS,
        default=DEFAULT_FORMAT,
        help="Manifest format for the plugin (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        help=f"Output zip path, or a directory to place the zip in (default: <root>/{DEFAULT_OUTPUT_DIR}/)",
    )
    parser.add_argument("--name", help=f"Plugin name (default: {DEFAULT_NAME})")
    parser.add_argument("--version", help="Plugin version (default: repository pyproject.toml version)")
    parser.add_argument("--description", help="Plugin description (default: pyproject.toml description)")
    parser.add_argument("--homepage", default=PUBLIC_REPOSITORY_URL, help="Homepage URL")
    parser.add_argument("--repository", default=PUBLIC_REPOSITORY_URL, help="Source repository URL")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the destination zip if it already exists",
    )
    parser.add_argument(
        "--skip-signing",
        action="store_true",
        help="Package without signing. Tests and keyless checkouts must opt in; release paths must not.",
    )
    return parser.parse_args()


def load_project_metadata(root: Path) -> tuple[str, str]:
    """Read (version, description) from pyproject.toml; empty strings when absent."""
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return "", ""
    match = PYPROJECT_VERSION_PATTERN.search(pyproject.read_text(encoding="utf-8"))
    version = match.group(1) if match else ""
    desc_match = re.search(r'^description\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.MULTILINE)
    description = desc_match.group(1) if desc_match else ""
    return version, description


def read_skill_name(root: Path) -> str:
    """Extract the skill name from SKILL.md frontmatter (payload directory name)."""
    skill_file = root / "SKILL.md"
    if not skill_file.is_file():
        raise FileNotFoundError(f"missing required skill file: {skill_file}")
    frontmatter = skill_file.read_text(encoding="utf-8").split("---", 2)
    if len(frontmatter) < 3:
        raise ValueError(f"SKILL.md has no frontmatter block: {skill_file}")
    match = FRONTMATTER_NAME_PATTERN.search(frontmatter[1])
    if not match:
        raise ValueError(f"SKILL.md frontmatter has no name field: {skill_file}")
    return match.group(1)


def render_template(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    leftover = PLACEHOLDER_PATTERN.search(rendered)
    if leftover:
        raise ValueError(f"unresolved template placeholder: {leftover.group(0)}")
    return rendered


def strip_empty_fields(manifest: dict) -> dict:
    """Drop optional top-level fields explicitly left empty by the caller."""
    return {key: value for key, value in manifest.items() if value != ""}


def validate_agent_plugins_manifest(manifest: object) -> list[str]:
    """Validate against the closed Agent Plugins 1.0 manifest schema (spec §5)."""
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest must be a JSON object"]

    unknown = sorted(set(manifest) - AGENT_PLUGINS_TOP_LEVEL_FIELDS)
    if unknown:
        errors.append(f"unknown top-level fields not permitted by the closed schema: {', '.join(unknown)}")

    schema = manifest.get("$schema")
    if schema != AGENT_PLUGINS_SCHEMA_URL:
        errors.append(f"$schema must be exactly {AGENT_PLUGINS_SCHEMA_URL}, got: {schema!r}")

    name = manifest.get("name")
    if not isinstance(name, str) or not name:
        errors.append("required field 'name' must be a non-empty string")
    elif not 1 <= len(name) <= 64:
        errors.append(f"'name' must be 1-64 characters, got {len(name)}")
    elif not AGENT_PLUGINS_NAME_PATTERN.match(name):
        errors.append(f"'name' violates Agent Plugins naming constraints: {name!r}")

    string_fields = ("version", "description", "homepage", "repository", "license")
    for field in string_fields:
        value = manifest.get(field)
        if field in manifest and not isinstance(value, str):
            errors.append(f"'{field}' must be a string, got {type(value).__name__}")

    author = manifest.get("author")
    if "author" in manifest:
        if not isinstance(author, dict):
            errors.append(f"'author' must be an object, got {type(author).__name__}")
        else:
            bad_keys = sorted(set(author) - AGENT_PLUGINS_AUTHOR_FIELDS)
            if bad_keys:
                errors.append(f"'author' allows only name/email/url, got: {', '.join(bad_keys)}")
            for key, value in author.items():
                if not isinstance(value, str):
                    errors.append(f"'author.{key}' must be a string, got {type(value).__name__}")

    keywords = manifest.get("keywords")
    if "keywords" in manifest:
        if not isinstance(keywords, list) or not all(isinstance(item, str) for item in keywords):
            errors.append("'keywords' must be an array of strings")

    extensions = manifest.get("extensions")
    if "extensions" in manifest:
        if not isinstance(extensions, dict) or not all(isinstance(value, dict) for value in extensions.values()):
            errors.append("'extensions' must be an object of namespace objects")

    return errors


def validate_claude_code_manifest(manifest: object) -> list[str]:
    """Validate the Claude Code .claude-plugin/plugin.json manifest fields we emit."""
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest must be a JSON object"]

    name = manifest.get("name")
    if not isinstance(name, str) or not name:
        errors.append("required field 'name' must be a non-empty string")
    elif not CLAUDE_CODE_NAME_PATTERN.match(name):
        errors.append(f"'name' must be kebab-case without spaces or control characters: {name!r}")

    for field in ("version", "description", "homepage", "repository", "license", "displayName"):
        value = manifest.get(field)
        if field in manifest and not isinstance(value, str):
            errors.append(f"'{field}' must be a string, got {type(value).__name__}")

    keywords = manifest.get("keywords")
    if "keywords" in manifest and (
        not isinstance(keywords, list) or not all(isinstance(item, str) for item in keywords)
    ):
        errors.append("'keywords' must be an array of strings")

    author = manifest.get("author")
    if "author" in manifest and not isinstance(author, dict):
        errors.append(f"'author' must be an object, got {type(author).__name__}")

    return errors


VALIDATORS = {
    "agent-plugins": validate_agent_plugins_manifest,
    "claude-code": validate_claude_code_manifest,
}


def collect_script_files(root: Path) -> tuple[str, ...]:
    scripts_dir = root / "scripts"
    if not scripts_dir.is_dir():
        raise FileNotFoundError(f"missing required directory: {scripts_dir}")
    return tuple(
        path.name
        for path in sorted(scripts_dir.glob("*.py"))
        if path.name not in SCRIPT_BLACKLIST and not path.name.startswith("_")
    )


def copy_skill_payload(root: Path, skill_dir: Path, script_files: tuple[str, ...]) -> None:
    """Materialize the runtime skill package under skills/<skill-name>/."""
    for name in SKILL_ROOT_FILES:
        source = root / name
        validate_source_tree(source)
        if not source.is_file():
            raise FileNotFoundError(f"missing required file: {source}")
        skill_dir.joinpath(name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, skill_dir / name)

    for name in SKILL_COPY_DIRS:
        source = root / name
        if not source.is_dir():
            continue
        validate_source_tree(source)
        shutil.copytree(source, skill_dir / name, ignore=COPY_IGNORE)

    scripts_output = skill_dir / "scripts"
    scripts_output.mkdir(parents=True, exist_ok=True)
    for name in script_files:
        validate_source_tree(root / "scripts" / name)
        shutil.copy2(root / "scripts" / name, scripts_output / name)


def generate_plugin_readme(name: str, version: str, description: str, fmt: str, skill_name: str) -> str:
    if fmt == "agent-plugins":
        install_lines = (
            "Install from a plugin marketplace (e.g. the Awesome Copilot marketplace in\n"
            "VS Code: search `@agentPlugins` in the Extensions view) or point a compatible\n"
            "client at this unzipped directory / its Git repository.\n"
            "Reference: https://agent-plugins.org/"
        )
    else:
        install_lines = (
            "Install with Claude Code:\n"
            "  claude plugin marketplace add <this-repo-or-zip>\n"
            "  /plugin install " + name + "\n"
            "or load locally: claude --plugin-dir ."
        )
    return (
        f"# {name}\n\n"
        f"{description}\n\n"
        f"- Version: {version}\n"
        f"- Format: {fmt}\n"
        f"- Bundled skill: `skills/{skill_name}/SKILL.md`\n\n"
        "## Install\n\n"
        f"{install_lines}\n\n"
        "## Contents\n\n"
        f"- `skills/{skill_name}/` — the runtime skill (SKILL.md, scripts,\n"
        "  references, hooks, agents, server, slots)\n"
        "- `LICENSE`\n"
    )


def build_manifest(root: Path, args: argparse.Namespace) -> tuple[dict, str, str]:
    """Render the format's manifest template and validate it; return (manifest, name, version)."""
    template_path = root / TEMPLATE_DIR / TEMPLATE_FILES[args.format]
    if not template_path.is_file():
        raise FileNotFoundError(f"missing manifest template: {template_path}")

    project_version, project_description = load_project_metadata(root)
    version = args.version or project_version
    if not version:
        raise ValueError("no --version given and pyproject.toml has no version")
    description = args.description or project_description
    if not description:
        raise ValueError("no --description given and pyproject.toml has no description")

    values = {
        "NAME": args.name or DEFAULT_NAME,
        "VERSION": version,
        "DESCRIPTION": description,
        "DISPLAY_NAME": DEFAULT_DISPLAY_NAME,
        "HOMEPAGE": args.homepage,
        "REPOSITORY": args.repository,
        "LICENSE": DEFAULT_LICENSE,
        "KEYWORDS": json.dumps(DEFAULT_KEYWORDS),
    }
    rendered = render_template(template_path.read_text(encoding="utf-8"), values)
    try:
        manifest = json.loads(rendered)
    except json.JSONDecodeError as exc:
        raise ValueError(f"rendered manifest is not valid JSON: {template_path}: {exc}") from exc
    manifest = strip_empty_fields(manifest)

    errors = VALIDATORS[args.format](manifest)
    if errors:
        raise ValueError("invalid manifest:\n  - " + "\n  - ".join(errors))
    return manifest, manifest["name"], version


def build_plugin_dir(root: Path, args: argparse.Namespace, staging: Path) -> tuple[Path, str, str]:
    """Assemble the plugin inside staging; return (plugin_dir, name, version)."""
    manifest, name, version = build_manifest(root, args)
    skill_name = read_skill_name(root)
    plugin_dir = staging / name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = plugin_dir / MANIFEST_RELATIVE_PATH[args.format]
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    for name in PLUGIN_ROOT_FILES:
        source = root / name
        if source.is_file():
            shutil.copy2(source, plugin_dir / name)

    skill_dir = plugin_dir / "skills" / skill_name
    copy_skill_payload(root, skill_dir, collect_script_files(root))
    if not args.skip_signing:
        identity = Path(os.environ.get("SPEC_SIGNING_IDENTITY", root / ".watermark-identity.json"))
        key = Path(os.environ.get("SPEC_SIGNING_KEY", root / ".watermark-key"))
        if not identity.is_file() or not key.is_file():
            raise ValueError("signing identity and key are required unless --skip-signing is set")
        from skill_watermark import load_identity, load_key, stamp_tree

        stamp_tree(skill_dir, ("SKILL.md",), load_identity(identity), load_key(key))

    readme = generate_plugin_readme(name, version, manifest.get("description", ""), args.format, skill_name)
    (plugin_dir / "README.md").write_text(readme, encoding="utf-8")

    return plugin_dir, name, version


def resolve_output(root: Path, args: argparse.Namespace, name: str, version: str) -> Path:
    if args.output:
        output = Path(args.output)
        if output.suffix == ".zip":
            return output
        return output / f"{name}-{version}-{args.format}.zip"
    return root / DEFAULT_OUTPUT_DIR / f"{name}-{version}-{args.format}.zip"


def write_zip(plugin_dir: Path, dest_zip: Path) -> None:
    """Write the plugin directory as a zip with the plugin at the archive root.

    Timestamps are fixed so identical inputs produce byte-comparable archives.
    """
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(plugin_dir.rglob("*")):
            if path.is_dir():
                continue
            relative = path.relative_to(plugin_dir.parent)
            info = zipfile.ZipInfo(str(relative), date_time=ZIP_TIMESTAMP)
            info.external_attr = (0o755 if path.stat().st_mode & 0o111 else 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()

    try:
        with tempfile.TemporaryDirectory(prefix="agent-plugin-") as staging:
            plugin_dir, name, version = build_plugin_dir(root, args, Path(staging))
            dest_zip = resolve_output(root, args, name, version)
            if dest_zip.exists() and not args.force:
                raise FileExistsError(f"output already exists: {dest_zip} (use --force to overwrite)")
            write_zip(plugin_dir, dest_zip)
    except (OSError, ValueError, UnsafePathError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"format: {args.format}")
    print(f"packaged: {dest_zip}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
