"""Launch requests are enforceable; provider reasoning and current hosts are not."""

import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import chain_spawn_support as support
import reasoning_policy as policy

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def catalog(monkeypatch):
    models = {
        "test-xhigh": ("high", "xhigh"),
        "test-max": ("high", "xhigh", "max"),
        "test-ultra": ("high", "xhigh", "max", "ultra"),
    }
    monkeypatch.setattr(
        policy, "load_codex_catalog", lambda: policy.CodexCatalog("/test/codex", "codex-cli test", models)
    )
    return models


@pytest.mark.parametrize("host", ["pi", "claude", "codex"])
def test_missing_reasoning_requests_maximum_without_changing_permissions(host, tmp_path, catalog):
    identity = {"id": "test-ultra"} if host == "codex" else None
    argv = support.build_worker_command(host, tmp_path, "--reasoning low", identity)
    assert argv[-2:] == ["--", "--reasoning low"]
    assert "--dangerously-skip-permissions" not in argv
    if host == "codex":
        assert "model_reasoning_effort=ultra" in argv
        assert "plan_mode_reasoning_effort=ultra" in argv
        assert "agents.default_subagent_reasoning_effort=ultra" in argv
        assert "sandbox_workspace_write.network_access=true" in argv
    else:
        flag = "--thinking" if host == "pi" else "--effort"
        assert argv[argv.index(flag) + 1] == "max"


@pytest.mark.parametrize("requested", ["off", "low", "medium", "high", "xhigh", "banana", ""])
@pytest.mark.parametrize("host", ["pi", "claude", "codex"])
def test_downgrades_fail_closed(host, requested, tmp_path, catalog):
    with pytest.raises(support.ChainSpawnError, match="ultrathink"):
        support.build_worker_command(host, tmp_path, "work", {"id": "test-ultra", "reasoning": requested})


def test_codex_maximum_is_model_specific(catalog):
    for model, levels in catalog.items():
        level = levels[-1]
        assert policy.resolve_reasoning("codex", model) == level
        assert policy.resolve_reasoning("codex", model, level.upper()) == level
    for unknown in (None, "unknown", "TEST-ULTRA"):
        with pytest.raises(policy.ReasoningPolicyError, match="--model"):
            policy.resolve_reasoning("codex", unknown)


def test_catalog_validation_and_bounded_offline_probe(monkeypatch):
    policy._codex_catalog.cache_clear()
    payload = {"models": [{"slug": "model", "supported_reasoning_levels": [{"effort": "high"}, {"effort": "max"}]}]}
    calls = []

    def probe(argv, **kwargs):
        calls.append((argv, kwargs))
        output = "codex-cli 0.167.0\n" if argv[-1] == "--version" else json.dumps(payload)
        return subprocess.CompletedProcess(argv, 0, stdout=output)

    monkeypatch.setattr(policy.subprocess, "run", probe)
    try:
        expected = policy.CodexCatalog("/test/codex", "codex-cli 0.167.0", {"model": ("high", "max")})
        assert policy._codex_catalog("/test/codex") == expected
        assert policy._codex_catalog("/test/codex") == expected
        assert len(calls) == 2
        assert calls[0][0] == ["/test/codex", "debug", "models", "--bundled"]
        assert calls[0][1]["timeout"] == 15
        assert calls[1][0] == ["/test/codex", "--version"]
        assert calls[1][1]["timeout"] == 15
        for bad in (
            {},
            {"models": []},
            {"models": [{"slug": "x", "supported_reasoning_levels": [{"effort": "future"}]}]},
        ):
            with pytest.raises(policy.ReasoningPolicyError):
                policy.codex_model_levels(bad)
    finally:
        policy._codex_catalog.cache_clear()


def test_claude_shell_environment_overrides_only_child(tmp_path, monkeypatch):
    worker = tmp_path / "claude"
    worker.write_text(
        '#!/bin/sh\nprintf "%s|%s|%s" "$CLAUDE_CODE_EFFORT_LEVEL" '
        '"${MAX_THINKING_TOKENS-unset}" "${CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING-unset}"\n'
    )
    worker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("CLAUDE_CODE_EFFORT_LEVEL", "low")
    monkeypatch.setenv("MAX_THINKING_TOKENS", "0")
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING", "1")
    argv = support.build_worker_command("claude", tmp_path, "work")
    _, terminal = support.build_terminal_command(tmp_path, argv)
    _, tmux, _ = support.build_tmux_command(tmp_path, argv, "autorun")
    for command in (terminal, tmux):
        result = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0
        assert result.stdout == "max|unset|unset"
    assert os.environ["CLAUDE_CODE_EFFORT_LEVEL"] == "low"
    assert os.environ["MAX_THINKING_TOKENS"] == "0"


def test_custom_commands_cannot_bypass_either_terminal(tmp_path):
    with pytest.raises(support.ChainSpawnError, match="--command"):
        support.build_terminal_command(tmp_path, [], "pi --thinking low")
    with pytest.raises(support.ChainSpawnError, match="--command"):
        support.build_tmux_command(tmp_path, [], "autorun", "pi --thinking low")


def test_legacy_missing_reasoning_is_upgraded_but_low_locks_refused():
    original = {"host": "pi", "model": {"id": "some-model"}}
    assert support.resolve_chain_model_identity("pi", None, None, original) == {"id": "some-model", "reasoning": "max"}
    assert original["model"] == {"id": "some-model"}
    for state in ({"model": {"id": "some-model", "reasoning": "high"}}, {"model": None, "reasoning_request": "low"}):
        with pytest.raises(support.ChainSpawnError, match="ultrathink"):
            support.resolve_chain_model_identity("pi", None, None, state)


def test_fanout_uses_same_policy_and_does_not_escalate(tmp_path, catalog):
    spec = importlib.util.spec_from_file_location(
        "policy_fanout", ROOT / "slots/workflow-runner/scripts/workflow_fanout.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for host, model in (("pi", None), ("codex", "test-ultra")):
        argv = module.build_command(host, "--thinking low", tmp_path / "result", model)
        assert argv[-2:] == ["--", "--thinking low"]
        assert "max" in argv if host == "pi" else "model_reasoning_effort=ultra" in argv
    codex = module.build_command("codex", "work", tmp_path / "result", "test-xhigh")
    assert codex[codex.index("--sandbox") + 1] == "read-only"
    assert module._cache_key("codex", "id", "work", model="test-xhigh") != module._cache_key(
        "codex", "id", "work", model="test-ultra"
    )


def test_current_status_does_not_claim_enforcement(tmp_path):
    from chain_session import render_status, status_payload

    payload = status_payload(tmp_path, "autoplan")
    assert payload["model_control"] == "host"
    assert payload["reasoning_control"] == "instruction_only"
    assert payload["provider_effect"] == "unverified"
    assert payload["reasoning_cap"] == "unknown"
    assert "instruction_only" in render_status(payload)
    assert "provider_effect=unverified" in render_status(payload)
    assert "reasoning_cap=unknown" in render_status(payload)
    assert "enforced" not in render_status(payload)


def test_launch_quoting_preserves_prompt(tmp_path):
    prompt = "$(touch nope) ' quote ; --effort low"
    argv = support.build_worker_command("pi", tmp_path, prompt)
    _, command = support.build_terminal_command(tmp_path, argv)
    assert shlex.split(command)[-1] == prompt


def test_codex_config_key_set_and_native_subagent_limitation(catalog):
    assert policy.reasoning_flags("codex", "test-ultra") == [
        "-c",
        "model_reasoning_effort=ultra",
        "-c",
        "plan_mode_reasoning_effort=ultra",
        "-c",
        "agents.default_subagent_reasoning_effort=ultra",
    ]
    record = policy.reasoning_record("codex", "test-ultra")
    assert record["subagent_reasoning_control"] == "requested_unverified"
    assert record["config_precedence"] == "unverified"
    assert record["reasoning_source"] == {
        "command": "codex debug models --bundled",
        "binary": "/test/codex",
        "version": "codex-cli test",
        "advertised_levels": ["high", "xhigh", "max", "ultra"],
    }


@pytest.mark.parametrize("host,model", [("pi", None), ("claude", None), ("codex", "test-max")])
def test_every_enforced_record_discloses_unverified_provider_and_cap(host, model, catalog):
    record = policy.reasoning_record(host, model)
    assert record["reasoning_control"] == "enforced"
    assert record["provider_effect"] == "unverified"
    assert record["reasoning_cap"] == "unknown"
    assert record["reasoning_request"] == "max"


def codex_chain_state(level):
    return {
        "host": "codex",
        "model": {"id": "test-model", "reasoning": level},
        "reasoning_policy": policy.POLICY_VERSION,
        "reasoning_control": "enforced",
        "reasoning_request": level,
    }


def test_codex_catalog_change_upgrades_previous_verified_maximum(monkeypatch):
    models = {"test-model": ("high", "xhigh")}
    monkeypatch.setattr(
        policy, "load_codex_catalog", lambda: policy.CodexCatalog("/test/codex", "codex-cli test", models)
    )
    state = codex_chain_state("xhigh")
    assert support.resolve_chain_model_identity("codex", None, None, state)["reasoning"] == "xhigh"
    models["test-model"] = ("high", "xhigh", "max")
    assert support.resolve_chain_model_identity("codex", None, None, state) == {
        "id": "test-model",
        "reasoning": "max",
    }
    assert state["model"]["reasoning"] == "xhigh"
    with pytest.raises(support.ChainSpawnError, match="requested 'xhigh'"):
        support.resolve_chain_model_identity("codex", None, "xhigh", state)


@pytest.mark.parametrize("new_levels", [("high",), ("high", "max")], ids=["downgrade", "removed"])
def test_codex_catalog_change_refuses_downgrade_and_removed_level(monkeypatch, new_levels):
    models = {"test-model": ("high", "xhigh")}
    monkeypatch.setattr(
        policy, "load_codex_catalog", lambda: policy.CodexCatalog("/test/codex", "codex-cli test", models)
    )
    state = codex_chain_state("xhigh")
    assert support.resolve_chain_model_identity("codex", None, None, state)["reasoning"] == "xhigh"
    models["test-model"] = new_levels
    with pytest.raises(support.ChainSpawnError, match="close/restart"):
        support.resolve_chain_model_identity("codex", None, None, state)


def test_codex_frozen_model_removed_from_catalog_never_falls_back(monkeypatch):
    models = {"test-model": ("high", "xhigh"), "replacement": ("high", "max")}
    monkeypatch.setattr(
        policy, "load_codex_catalog", lambda: policy.CodexCatalog("/test/codex", "codex-cli test", models)
    )
    state = codex_chain_state("xhigh")
    assert support.resolve_chain_model_identity("codex", None, None, state)["id"] == "test-model"
    del models["test-model"]
    with pytest.raises(support.ChainSpawnError, match="unknown/default model 'test-model'"):
        support.resolve_chain_model_identity("codex", None, None, state)
    assert state["model"] == {"id": "test-model", "reasoning": "xhigh"}


def test_codex_legacy_low_lock_is_not_a_verified_previous_maximum(catalog):
    with pytest.raises(support.ChainSpawnError, match="requested 'high'"):
        support.resolve_chain_model_identity("codex", None, None, {"model": {"id": "test-max", "reasoning": "high"}})


@pytest.mark.parametrize("state", [{"host": "codex"}, {"host": "codex", "model": None}])
@pytest.mark.parametrize("incoming_model", [None, "test-max"])
def test_codex_legacy_chain_without_frozen_model_fails_closed(catalog, state, incoming_model):
    with pytest.raises(support.ChainSpawnError, match="(?:without a frozen model|locked model: null)"):
        support.resolve_chain_model_identity("codex", incoming_model, None, state)
