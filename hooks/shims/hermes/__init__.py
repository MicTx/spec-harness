"""Hermes forwarding shim owned by spec-harness."""

import json
import subprocess
from pathlib import Path


def pre_llm_call(payload):
    hook = Path(__file__).resolve().with_name("spec_hook.py")
    result = subprocess.run(
        ["python3", str(hook), "--host", "hermes", "prompt"], input=json.dumps(payload), capture_output=True, text=True
    )
    return json.loads(result.stdout) if result.stdout.strip() else {}
