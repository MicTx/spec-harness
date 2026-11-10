import json

from tests.v3_support import SLUG, cli, make_package, repo


def test_cli_resolves_root_and_slug_from_nested_directory(tmp_path):
    repo(tmp_path)
    make_package(tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()
    result = cli(nested, "next")
    assert result.returncode == 0
    assert json.loads(result.stdout)["slug"] == SLUG


def test_user_commands_are_only_three():
    from pathlib import Path

    skill = (Path(__file__).parents[1] / "SKILL.md").read_text()
    assert "/spec:new" not in skill
    assert "/spec:push" in skill and "/spec:doctor" in skill
