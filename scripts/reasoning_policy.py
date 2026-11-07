"""Mandatory launch-time requests, independent of permissions or provider effects.

Codex 0.167.0 recognizes all three config keys in strict startup validation.
Its app-server config/read reports CLI overrides as sessionFlags over user low
values. Exec startup reports the top-level override over profile-v2 low values;
parity for plan/subagent settings is unverified, as is each worker's effective
configuration. Legacy default profiles are rejected by the CLI. Native subagent
model selection is host-owned, so its inherited effort request is unverified.
Claude's Environment variables / Variables documents effort precedence and
how MAX_THINKING_TOKENS=0 and disabled adaptive reasoning can lower thinking:
https://code.claude.com/docs/en/env-vars
"""

from __future__ import annotations

import functools
import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass

POLICY_VERSION = "ultrathink-v1"
REASONING_FLAGS = {
    "pi": (("--thinking",), "{}"),
    "claude": (("--effort",), "{}"),
    "codex": (("-c",), "model_reasoning_effort={}"),
}
CODEX_LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
CODEX_REASONING_KEYS = (
    "model_reasoning_effort",
    "plan_mode_reasoning_effort",
    "agents.default_subagent_reasoning_effort",
)
CLAUDE_ENV_PREFIX = (
    "env",
    "-u",
    "MAX_THINKING_TOKENS",
    "-u",
    "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING",
    "CLAUDE_CODE_EFFORT_LEVEL=max",
)


class ReasoningPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class CodexCatalog:
    binary: str
    version: str
    models: dict[str, tuple[str, ...]]


def codex_model_levels(payload: object) -> dict[str, tuple[str, ...]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ReasoningPolicyError("Codex bundled model catalog is malformed")
    models: dict[str, tuple[str, ...]] = {}
    seen: set[str] = set()
    for entry in payload["models"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("slug"), str) or not entry["slug"].strip():
            raise ReasoningPolicyError("Codex bundled model catalog has an invalid model")
        slug = entry["slug"]
        if slug in seen:
            raise ReasoningPolicyError(f"Codex catalog has ambiguous reasoning capabilities for {slug}")
        seen.add(slug)
        advertised = entry.get("supported_reasoning_levels")
        if not isinstance(advertised, list) or not advertised:
            continue
        levels = [item.get("effort") if isinstance(item, dict) else None for item in advertised]
        if any(level not in CODEX_LEVELS for level in levels) or len(set(levels)) != len(levels):
            raise ReasoningPolicyError(f"Codex catalog advertises unknown reasoning levels for {slug}")
        if levels != sorted(levels, key=CODEX_LEVELS.index):
            raise ReasoningPolicyError(f"Codex catalog has ambiguous reasoning capabilities for {slug}")
        if levels[-1] in ("none", "minimal", "low", "medium"):
            continue
        models[slug] = tuple(levels)
    if not models:
        raise ReasoningPolicyError("Codex catalog has no verifiable deep-reasoning models")
    return models


@functools.lru_cache(maxsize=8)
def _codex_catalog(binary: str) -> CodexCatalog:
    try:
        result = subprocess.run(
            [binary, "debug", "models", "--bundled"], capture_output=True, text=True, timeout=15, check=False
        )
        if result.returncode != 0:
            raise ReasoningPolicyError("Codex bundled catalog unavailable; update the CLI, never lower reasoning")
        models = codex_model_levels(json.loads(result.stdout))
        version = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=15, check=False)
        if version.returncode != 0 or not version.stdout.strip().startswith("codex-cli "):
            raise ReasoningPolicyError("Cannot verify Codex CLI version for the bundled catalog")
        return CodexCatalog(binary, version.stdout.strip(), models)
    except (OSError, subprocess.SubprocessError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReasoningPolicyError(f"Cannot verify Codex model capabilities: {type(exc).__name__}") from exc


def load_codex_catalog() -> CodexCatalog:
    binary = shutil.which("codex")
    if binary is None:
        raise ReasoningPolicyError("Codex binary not found; cannot verify maximum reasoning")
    return _codex_catalog(binary)


def resolve_reasoning(
    host: str,
    model: str | None = None,
    requested: str | None = None,
    *,
    previous_maximum: str | None = None,
) -> str:
    if host not in REASONING_FLAGS:
        raise ReasoningPolicyError(f"Unsupported reasoning host: {host!r}")
    if model is not None and (
        not isinstance(model, str)
        or not model.strip()
        or model.startswith("-")
        or any(ord(char) < 32 or ord(char) == 127 for char in model)
    ):
        raise ReasoningPolicyError("--model must be a non-empty model id, not an option or control sequence")
    if host == "codex":
        catalog = load_codex_catalog().models
        if model not in catalog:
            choices = ", ".join(f"{name}={levels[-1]}" for name, levels in sorted(catalog.items()))
            raise ReasoningPolicyError(
                f"Codex ultrathink requires an explicit catalog model (--model <id>); "
                f"unknown/default model {model!r}. Available: {choices}. "
                "Re-run the same command with --model <id>; no automatic model selection."
            )
        levels = catalog[model]
        maximum = levels[-1]
        if previous_maximum is not None and previous_maximum not in levels:
            raise ReasoningPolicyError(
                f"Codex ultrathink locked maximum {previous_maximum!r} is no longer advertised for {model!r}; "
                "refusing a downgrade or removed level. Explicitly close/restart this chain."
            )
    else:
        maximum = "max"
    if requested is not None and (not isinstance(requested, str) or requested.strip().lower() != maximum):
        raise ReasoningPolicyError(
            f"ultrathink requires {host} reasoning {maximum!r}; requested {requested!r}. "
            "Lower/unknown levels are refused; an incompatible locked chain must be explicitly restarted."
        )
    return maximum


def reasoning_flags(host: str, model: str | None = None, requested: str | None = None) -> list[str]:
    level = resolve_reasoning(host, model, requested)
    if host == "codex":
        return [token for key in CODEX_REASONING_KEYS for token in ("-c", f"{key}={level}")]
    prefix, template = REASONING_FLAGS[host]
    flags = [*prefix, template.format(level)]
    if host == "claude":
        flags.extend(["--settings", '{"alwaysThinkingEnabled":true}'])
    return flags


def reasoning_record(host: str, model: str | None = None, requested: str | None = None) -> dict[str, object]:
    level = resolve_reasoning(host, model, requested)
    record: dict[str, object] = {
        "reasoning_policy": POLICY_VERSION,
        "reasoning_control": "enforced",
        "reasoning_request": level,
        "reasoning_source": f"{host} CLI maximum request",
        "provider_effect": "unverified",
        "reasoning_cap": "unknown",
    }
    if host == "codex":
        catalog = load_codex_catalog()
        record["reasoning_source"] = {
            "command": "codex debug models --bundled",
            "binary": catalog.binary,
            "version": catalog.version,
            "advertised_levels": list(catalog.models[model]),
        }
        record["subagent_reasoning_control"] = "requested_unverified"
        record["config_precedence"] = "unverified"
    return record


def reasoning_cache_identity(host: str, model: str | None = None) -> str:
    source = json.dumps([POLICY_VERSION, host, model, resolve_reasoning(host, model)], sort_keys=True)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def worker_launch_argv(command: list[str]) -> list[str]:
    return [*CLAUDE_ENV_PREFIX, *command] if command and command[0] == "claude" else list(command)
