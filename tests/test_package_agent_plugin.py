import argparse
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from package_agent_plugin import (
    AGENT_PLUGINS_SCHEMA_URL,
    DEFAULT_NAME,
    render_template,
    resolve_output,
    strip_empty_fields,
    validate_agent_plugins_manifest,
    validate_claude_code_manifest,
)

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_ROOT / "scripts" / "package_agent_plugin.py"


def _base_agent_plugins_manifest() -> dict:
    return {
        "$schema": AGENT_PLUGINS_SCHEMA_URL,
        "name": "spec-harness",
        "version": "1.2.3",
        "description": "Task-package workflow skill",
        "license": "AGPL-3.0-or-later",
        "keywords": ["spec", "task-package"],
    }


def _base_claude_code_manifest() -> dict:
    return {
        "name": "spec-harness",
        "displayName": "Spec Harness",
        "version": "1.2.3",
        "description": "Task-package workflow skill",
    }


# ---------- 清单字段校验 ----------


def test_agent_plugins_valid_manifest_has_no_errors():
    assert validate_agent_plugins_manifest(_base_agent_plugins_manifest()) == []


def test_agent_plugins_missing_name_is_error():
    manifest = _base_agent_plugins_manifest()
    del manifest["name"]
    errors = validate_agent_plugins_manifest(manifest)
    assert any("'name'" in error for error in errors)


def test_agent_plugins_wrong_schema_url_is_error():
    manifest = _base_agent_plugins_manifest()
    manifest["$schema"] = "https://example.com/other.schema.json"
    errors = validate_agent_plugins_manifest(manifest)
    assert any("$schema" in error for error in errors)


def test_agent_plugins_unknown_top_level_field_is_error():
    manifest = _base_agent_plugins_manifest()
    manifest["hooks"] = "./hooks.json"
    errors = validate_agent_plugins_manifest(manifest)
    assert any("closed schema" in error and "hooks" in error for error in errors)


def test_agent_plugins_bad_names_are_errors():
    bad_names = [
        "My-Plugin",  # 大写
        "-start",  # 前导连字符
        "has--double",  # 连续连字符
        "too..many",  # 连续句点
        "with space",
        "",
        "a" * 65,  # 超长
        "ends-",
    ]
    for bad in bad_names:
        manifest = _base_agent_plugins_manifest()
        manifest["name"] = bad
        assert validate_agent_plugins_manifest(manifest), f"expected error for name: {bad!r}"


def test_agent_plugins_name_edge_cases_pass():
    for good in ["a", "my-plugin", "acme.tools", "lint3r", "a" * 64]:
        manifest = _base_agent_plugins_manifest()
        manifest["name"] = good
        assert validate_agent_plugins_manifest(manifest) == [], f"expected pass for name: {good!r}"


def test_agent_plugins_bad_author_and_keywords_are_errors():
    manifest = _base_agent_plugins_manifest()
    manifest["author"] = {"name": "Dev", "phone": "123"}
    manifest["keywords"] = "spec"
    manifest["extensions"] = {"com.example.client": "not-an-object"}
    errors = validate_agent_plugins_manifest(manifest)
    assert any("author" in error for error in errors)
    assert any("keywords" in error for error in errors)
    assert any("extensions" in error for error in errors)


def test_claude_code_valid_manifest_has_no_errors():
    assert validate_claude_code_manifest(_base_claude_code_manifest()) == []


def test_claude_code_non_kebab_name_is_error():
    manifest = _base_claude_code_manifest()
    manifest["name"] = "Spec Harness"
    assert validate_claude_code_manifest(manifest)


# ---------- 模板渲染 ----------


def test_render_template_substitutes_and_rejects_leftovers():
    rendered = render_template("{{NAME}}-{{VERSION}}", {"NAME": "spec", "VERSION": "1.0.0"})
    assert rendered == "spec-1.0.0"

    try:
        render_template("{{NAME}} {{UNKNOWN}}", {"NAME": "spec"})
    except ValueError as exc:
        assert "UNKNOWN" in str(exc)
    else:
        raise AssertionError("expected ValueError for unresolved placeholder")


def test_strip_empty_fields_removes_only_empty_strings():
    manifest = {"name": "spec", "homepage": "", "version": "1.0.0"}
    assert strip_empty_fields(manifest) == {"name": "spec", "version": "1.0.0"}


def test_resolve_output_default_and_explicit():
    root = Path("/repo")
    args = argparse.Namespace(format="agent-plugins", output=None, name="spec-harness", version="0.6.0")
    resolved = resolve_output(root, args, "spec-harness", "0.6.0")
    assert resolved == Path("/repo/dist/spec-harness-0.6.0-agent-plugins.zip")

    args.output = "/tmp/out/pkg.zip"
    assert resolve_output(root, args, "spec-harness", "0.6.0") == Path("/tmp/out/pkg.zip")

    args.output = "/tmp/outdir"
    assert resolve_output(root, args, "spec-harness", "0.6.0") == Path(
        "/tmp/outdir/spec-harness-0.6.0-agent-plugins.zip"
    )


# ---------- 端到端构建 ----------


def _run_build(tmpdir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    output = tmpdir / "plugin.zip"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(SKILL_ROOT), "--output", str(output), "--skip-signing", *extra],
        capture_output=True,
        text=True,
    )
    return result


def _project_version() -> str:
    pyproject = (SKILL_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for line in pyproject.splitlines():
        if line.startswith("version"):
            return line.split("=", 1)[1].strip().strip('"')
    raise AssertionError("pyproject.toml has no version")


def test_build_agent_plugins_end_to_end():
    with tempfile.TemporaryDirectory() as tmpname:
        tmpdir = Path(tmpname)
        result = _run_build(tmpdir)
        assert result.returncode == 0, result.stderr

        zip_path = tmpdir / "plugin.zip"
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            # 插件目录在压缩包根，清单在插件根（Agent Plugins 1.0 固定位置）
            manifest_name = f"{DEFAULT_NAME}/plugin.json"
            assert manifest_name in names
            manifest = json.loads(archive.read(manifest_name))
            assert manifest["$schema"] == AGENT_PLUGINS_SCHEMA_URL
            assert manifest["name"] == DEFAULT_NAME
            assert manifest["version"] == _project_version()
            assert validate_agent_plugins_manifest(manifest) == []

            # skills 载荷：skill 目录直接子级含 SKILL.md
            assert f"{DEFAULT_NAME}/skills/spec/SKILL.md" in names
            assert f"{DEFAULT_NAME}/skills/spec/install.sh" in names
            assert f"{DEFAULT_NAME}/skills/spec/pyproject.toml" in names
            assert f"{DEFAULT_NAME}/skills/spec/scripts/check_spec_package.py" in names
            assert f"{DEFAULT_NAME}/skills/spec/references/templates.md" in names
            assert f"{DEFAULT_NAME}/skills/spec/slots/team-loop/manifest.json" in names
            assert f"{DEFAULT_NAME}/skills/spec/slots/workflow-runner/manifest.json" in names
            assert f"{DEFAULT_NAME}/skills/spec/slots/workflow-runner/scripts/workflow_fanout.py" in names
            assert f"{DEFAULT_NAME}/LICENSE" in names
            assert f"{DEFAULT_NAME}/README.md" in names

            # 打包工具与缓存不进载荷
            assert not any("export_skill_package.py" in name for name in names)
            assert not any("package_agent_plugin.py" in name for name in names)
            assert not any("__pycache__" in name for name in names)
            assert not any(name.endswith(".pyc") for name in names)

            # 全部条目都落在插件目录内（包边界）
            assert names
            assert all(name.startswith(f"{DEFAULT_NAME}/") for name in names)


def test_build_claude_code_end_to_end():
    with tempfile.TemporaryDirectory() as tmpname:
        tmpdir = Path(tmpname)
        result = _run_build(tmpdir, "--format", "claude-code")
        assert result.returncode == 0, result.stderr

        with zipfile.ZipFile(tmpdir / "plugin.zip") as archive:
            names = archive.namelist()
            manifest_name = f"{DEFAULT_NAME}/.claude-plugin/plugin.json"
            assert manifest_name in names
            manifest = json.loads(archive.read(manifest_name))
            assert manifest["name"] == DEFAULT_NAME
            assert manifest["displayName"] == "Spec Harness"
            assert validate_claude_code_manifest(manifest) == []

            # 双格式 skills 载荷一致
            assert f"{DEFAULT_NAME}/skills/spec/SKILL.md" in names
            assert f"{DEFAULT_NAME}/skills/spec/slots/team-loop/manifest.json" in names
            assert f"{DEFAULT_NAME}/skills/spec/slots/workflow-runner/manifest.json" in names
            # claude-code 格式不产出 Agent Plugins 清单
            assert f"{DEFAULT_NAME}/plugin.json" not in names


def test_build_is_deterministic():
    with tempfile.TemporaryDirectory() as tmpname:
        tmpdir = Path(tmpname)
        first, second = tmpdir / "a.zip", tmpdir / "b.zip"
        for dest in (first, second):
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(SKILL_ROOT), "--output", str(dest), "--skip-signing"],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr
        assert first.read_bytes() == second.read_bytes()


def test_build_refuses_to_overwrite_without_force():
    with tempfile.TemporaryDirectory() as tmpname:
        tmpdir = Path(tmpname)
        assert _run_build(tmpdir).returncode == 0
        again = _run_build(tmpdir)
        assert again.returncode == 1
        assert "already exists" in again.stderr
        assert _run_build(tmpdir, "--force").returncode == 0


# ---------- 错误路径 ----------


def _scaffold_root(tmpdir: Path, *, with_template: bool = True, with_skill: bool = True) -> Path:
    """最小仓库根：模板目录 + pyproject + SKILL.md + scripts/。"""
    root = tmpdir / "repo"
    root.mkdir()
    (root / "scripts").mkdir()
    (root / "scripts" / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "9.9.9"\ndescription = "Demo skill"\n',
        encoding="utf-8",
    )
    if with_skill:
        (root / "SKILL.md").write_text(
            "---\nname: demo-skill\ndescription: demo\n---\n\n# Demo\n",
            encoding="utf-8",
        )
        (root / "install.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    if with_template:
        target = root / "agent-plugin"
        target.mkdir()
        for name in ("plugin.json.template", "claude-plugin.json.template"):
            source = SKILL_ROOT / "agent-plugin" / name
            target.joinpath(name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return root


def test_missing_template_dir_is_error():
    with tempfile.TemporaryDirectory() as tmpname:
        root = _scaffold_root(Path(tmpname), with_template=False)
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(root),
                "--output",
                str(Path(tmpname) / "x.zip"),
                "--skip-signing",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "missing manifest template" in result.stderr


def test_missing_skill_file_is_error():
    with tempfile.TemporaryDirectory() as tmpname:
        root = _scaffold_root(Path(tmpname), with_skill=False)
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(root),
                "--output",
                str(Path(tmpname) / "x.zip"),
                "--skip-signing",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "missing required skill file" in result.stderr


def test_invalid_plugin_name_is_error_for_both_formats():
    with tempfile.TemporaryDirectory() as tmpname:
        root = _scaffold_root(Path(tmpname))
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(root),
                "--output",
                str(Path(tmpname) / "x.zip"),
                "--skip-signing",
                "--name",
                "Bad--Name",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "naming constraints" in result.stderr

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(root),
                "--output",
                str(Path(tmpname) / "x.zip"),
                "--format",
                "claude-code",
                "--skip-signing",
                "--name",
                "Bad Name",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "kebab-case" in result.stderr


def test_bad_template_placeholder_fails_closed():
    with tempfile.TemporaryDirectory() as tmpname:
        root = _scaffold_root(Path(tmpname))
        (root / "agent-plugin" / "plugin.json.template").write_text(
            '{"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", '
            '"name": "{{NAME}}", "todo": "{{TODO}}"}',
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(root),
                "--output",
                str(Path(tmpname) / "x.zip"),
                "--skip-signing",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "{{TODO}}" in result.stderr


def test_template_unknown_field_fails_validation():
    with tempfile.TemporaryDirectory() as tmpname:
        root = _scaffold_root(Path(tmpname))
        (root / "agent-plugin" / "plugin.json.template").write_text(
            '{"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", '
            '"name": "{{NAME}}", "version": "{{VERSION}}", "hooks": "./hooks.json"}',
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(root),
                "--output",
                str(Path(tmpname) / "x.zip"),
                "--skip-signing",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "closed schema" in result.stderr


def test_scaffolded_root_builds_minimal_plugin():
    with tempfile.TemporaryDirectory() as tmpname:
        root = _scaffold_root(Path(tmpname))
        output = Path(tmpname) / "demo.zip"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root), "--output", str(output), "--skip-signing"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            assert "spec-harness/plugin.json" in names
            assert "spec-harness/skills/demo-skill/SKILL.md" in names
            assert "spec-harness/skills/demo-skill/scripts/helper.py" in names


def test_plugin_skill_payload_matches_runtime_export(tmp_path: Path):
    skill_root = Path(__file__).resolve().parent.parent
    runtime = tmp_path / "runtime"
    export = subprocess.run(
        [
            sys.executable,
            str(skill_root / "scripts" / "export_skill_package.py"),
            "--root",
            str(skill_root),
            "--output",
            str(runtime),
            "--force",
            "--skip-signing",
        ],
        capture_output=True,
        text=True,
    )
    assert export.returncode == 0, export.stderr
    plugin = tmp_path / "plugin.zip"
    packaged = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(skill_root),
            "--output",
            str(plugin),
            "--skip-signing",
        ],
        capture_output=True,
        text=True,
    )
    assert packaged.returncode == 0, packaged.stderr

    runtime_files = {
        path.relative_to(runtime).as_posix(): path.read_bytes() for path in runtime.rglob("*") if path.is_file()
    }
    with zipfile.ZipFile(plugin) as archive:
        prefix = f"{DEFAULT_NAME}/skills/spec/"
        plugin_files = {
            name[len(prefix) :]: archive.read(name)
            for name in archive.namelist()
            if name.startswith(prefix) and not name.endswith("/")
        }
    assert plugin_files == runtime_files
