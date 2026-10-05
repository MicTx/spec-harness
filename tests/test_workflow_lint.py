"""Startup-validity gate tests for GitHub Actions workflow files."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from export_public_repo import PublicExportError, build_public_tree  # noqa: E402
from lint_workflows import lint_workflow_text  # noqa: E402
from lint_workflows import main as lint_main  # noqa: E402

GOOD_WORKFLOW = """\
name: CI

on:
  push:
    branches:
      - main

jobs:
  verify:
    if: github.repository == 'MicTx/spec-harness'
    runs-on: ubuntu-latest
    env:
      SPEC_RELEASE_PARITY: "1"
      GIT_TOKEN: ${{ github.token }}
    steps:
      - name: step env may use runner
        env:
          KEY: ${{ runner.temp }}/key
        run: echo "$KEY ${{ env.KEY }}"
"""


def test_good_workflow_passes():
    assert lint_workflow_text("good.yml", GOOD_WORKFLOW) == []


def test_job_env_runner_context_is_rejected():
    broken = GOOD_WORKFLOW.replace(
        "      GIT_TOKEN: ${{ github.token }}",
        "      SPEC_SIGNING_KEY: ${{ runner.temp }}/smoke-key",
    )
    problems = lint_workflow_text("broken.yml", broken)
    assert any("runner" in problem and "job 级 env" in problem for problem in problems)


def test_workflow_env_rejects_runner_and_allows_secrets():
    text = "env:\n  A: ${{ secrets.X }}\n  B: ${{ runner.temp }}\njobs:\n  j:\n    runs-on: ubuntu-latest\n"
    problems = lint_workflow_text("wf-env.yml", text)
    assert any("workflow 级 env" in problem and "runner" in problem for problem in problems)
    assert not any("上下文: secrets" in problem for problem in problems)


def test_job_if_rejects_runner():
    text = "jobs:\n  j:\n    if: ${{ runner.os }} == 'Linux'\n    runs-on: ubuntu-latest\n"
    problems = lint_workflow_text("job-if.yml", text)
    assert any("job 级 if" in problem and "runner" in problem for problem in problems)


def test_missing_jobs_and_missing_runs_on_are_structural_errors():
    assert lint_workflow_text("nojobs.yml", "name: x\non: push\n")
    problems = lint_workflow_text("norunson.yml", "jobs:\n  j:\n    env:\n      A: 1\n")
    assert any("runs-on" in problem for problem in problems)


def test_repository_workflows_pass_the_gate():
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows, "repository must carry workflow files"
    for path in workflows:
        assert lint_workflow_text(path.as_posix(), path.read_text(encoding="utf-8")) == []


def test_export_refuses_workflow_with_startup_violation(tmp_path: Path):
    source = tmp_path / "source"
    shutil.copytree(
        ROOT,
        source,
        ignore=shutil.ignore_patterns(".git", ".spec", ".maintainer", ".zcode"),
    )
    target = source / ".github" / "workflows" / "ci-public.yml"
    target.write_text(
        "jobs:\n  verify:\n    runs-on: ubuntu-latest\n"
        "    env:\n      SPEC_SIGNING_KEY: ${{ runner.temp }}/smoke-key\n",
        encoding="utf-8",
    )
    try:
        build_public_tree(source, tmp_path / "public")
    except PublicExportError as exc:
        assert "workflow startup validity" in str(exc)
        assert "runner" in str(exc)
    else:
        raise AssertionError("exporter must refuse a workflow that fails startup validity")


def test_cli_reports_failure_and_success(tmp_path: Path, capsys):
    bad = tmp_path / "bad.yml"
    bad.write_text(
        "jobs:\n  j:\n    runs-on: ubuntu-latest\n    env:\n      K: ${{ steps.setup.outputs.k }}\n",
        encoding="utf-8",
    )
    assert lint_main([str(bad)]) == 1
    assert "steps" in capsys.readouterr().err
    good = tmp_path / "good.yml"
    good.write_text(GOOD_WORKFLOW, encoding="utf-8")
    assert lint_main([str(good)]) == 0
