"""Hermes forwarding shim owned by spec-harness."""

import json
import subprocess


def _forward(event, payload):
    result = subprocess.run(
        ["__SPEC_EXECUTABLE__", "__SPEC_DISPATCHER__", "--host", "hermes", event],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else {}


def pre_llm_call(payload):
    return _forward("prompt", payload)


def pre_tool_call(payload):
    return _forward("pre_tool", payload)
