"""Data-only host adapter matrix and protocol conversion."""

from __future__ import annotations

import os
import shlex
from pathlib import Path

EVENTS = ("start", "prompt", "pre_tool", "post_edit", "pre_compact", "subagent", "stop", "end")

CLAUDE_EVENTS = {
    "start": "SessionStart",
    "prompt": "UserPromptSubmit",
    "pre_tool": "PreToolUse",
    "post_edit": "PostToolUse",
    "pre_compact": "PreCompact",
    "subagent": "SubagentStart",
    "stop": "Stop",
    "end": "SessionEnd",
}
CURSOR_EVENTS = {
    "start": "sessionStart",
    "prompt": "beforeSubmitPrompt",
    "pre_tool": "beforeShellExecution",
    "post_edit": "afterFileEdit",
    "pre_compact": "preCompact",
    "subagent": "subagentStart",
    "stop": "stop",
    "end": "sessionEnd",
}
GEMINI_EVENTS = {
    "start": "SessionStart",
    "prompt": "BeforeAgent",
    "pre_tool": "BeforeTool",
    "post_edit": "AfterTool",
    "pre_compact": "PreCompress",
    "subagent": "BeforeAgent",
    "stop": "AfterAgent",
    "end": "SessionEnd",
}

HOSTS = {
    "claude": {
        "config": "~/.claude/settings.json",
        "style": "claude",
        "events": CLAUDE_EVENTS,
        "capabilities": EVENTS,
    },
    "codex": {
        "config": "~/.codex/hooks.json",
        "style": "claude",
        "events": CLAUDE_EVENTS,
        "capabilities": EVENTS,
    },
    "devin": {
        "config": "~/.config/devin/config.json",
        "style": "claude",
        "events": CLAUDE_EVENTS,
        "capabilities": ("start", "prompt", "pre_tool", "stop", "end"),
    },
    "grok": {
        "config": "~/.grok/hooks/spec.json",
        "style": "claude",
        "events": CLAUDE_EVENTS,
        "capabilities": ("start", "pre_tool", "pre_compact", "end"),
    },
    "zcode": {
        "config": "~/.zcode/cli/config.json",
        "style": "claude",
        "events": CLAUDE_EVENTS,
        "capabilities": ("start", "stop", "end"),
    },
    "dsh": {
        "config": "~/.dsh/hooks.json",
        "style": "claude",
        "events": CLAUDE_EVENTS,
        "capabilities": ("pre_tool", "stop"),
    },
    "cursor": {
        "config": "~/.cursor/hooks.json",
        "style": "cursor",
        "events": CURSOR_EVENTS,
        "capabilities": EVENTS,
    },
    "gemini": {
        "config": "~/.gemini/settings.json",
        "style": "gemini",
        "events": GEMINI_EVENTS,
        "capabilities": EVENTS,
    },
    "opencode": {
        "config": "~/.config/opencode/plugins/spec.ts",
        "style": "shim",
        "events": CLAUDE_EVENTS,
        "capabilities": EVENTS[:-2],
    },
    "pi": {
        "config": "~/.pi/agent/extensions/spec.ts",
        "style": "shim",
        "events": CLAUDE_EVENTS,
        "capabilities": EVENTS,
    },
    "openclaw": {
        "config": "~/.openclaw/plugins/spec.ts",
        "style": "shim",
        "events": CLAUDE_EVENTS,
        "capabilities": ("start", "prompt", "pre_tool", "pre_compact", "stop", "end"),
    },
    "hermes": {
        "config": "~/.hermes/plugins/spec/__init__.py",
        "style": "shim",
        "events": CLAUDE_EVENTS,
        "capabilities": ("prompt", "pre_tool", "post_edit"),
    },
    "claude-desktop": {"config": "", "style": "none", "events": {}, "capabilities": ()},
    "agents": {"config": "", "style": "none", "events": {}, "capabilities": ()},
}

MATCHERS = {
    "pre_tool": "Bash",
    "post_edit": "Edit|Write",
}


def _home(home: Path | None) -> Path:
    return home if home is not None else Path(os.environ.get("HOME", "~")).expanduser()


def config_path(host: str, home: Path | None = None) -> Path:
    if host == "pi":
        root = Path(os.environ.get("PI_DIR", _home(home) / ".pi")).expanduser()
        return root / "agent" / "extensions" / "spec.ts"
    if host == "opencode":
        root = Path(os.environ.get("XDG_CONFIG_HOME", _home(home) / ".config")).expanduser()
        return root / "opencode" / "plugins" / "spec.ts"
    if host == "hermes":
        root = Path(os.environ.get("HERMES_HOME", _home(home) / ".hermes")).expanduser()
        return root / "plugins" / "spec" / "__init__.py"
    if host == "dsh":
        root = Path(os.environ.get("DSH_HOME", _home(home) / ".dsh")).expanduser()
        return root / "hooks.json"
    value = str(HOSTS[host]["config"])
    if value.startswith("~/"):
        return _home(home) / value[2:]
    return Path(value).expanduser()


def decode(host: str, payload: dict) -> dict:
    del host
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    result = dict(payload)
    result["cwd"] = payload.get("cwd") or payload.get("workspaceRoot") or payload.get("workingDirectory")
    result["command"] = payload.get("command") or tool_input.get("command", "")
    result["file_path"] = (
        payload.get("file_path")
        or payload.get("filePath")
        or tool_input.get("file_path")
        or tool_input.get("filePath", "")
    )
    result["source"] = payload.get("source") or payload.get("trigger") or payload.get("reason", "")
    result["continuing"] = bool(payload.get("stop_hook_active") or payload.get("continuing"))
    return result


def encode(host: str, event: str, action) -> dict:
    if action.deny:
        if host in {"claude", "codex", "zcode", "dsh", "grok", "devin"}:
            return {
                "decision": "block",
                "reason": action.reason,
                "hookSpecificOutput": {
                    "hookEventName": HOSTS[host]["events"].get(event, event),
                    "permissionDecision": "deny",
                    "permissionDecisionReason": action.reason,
                },
            }
        if host == "cursor":
            return {"permission": "deny", "user_message": action.reason}
        return {"decision": "deny", "reason": action.reason}
    if action.block:
        if host == "cursor":
            return {"followup_message": action.reason}
        if host == "gemini":
            return {"decision": "block", "reason": action.reason}
        return {"decision": "block", "reason": action.reason}
    if action.context:
        if host == "cursor":
            return {"followup_message": action.context} if event in {"stop", "end"} else {}
        if host in {"opencode", "pi", "openclaw", "hermes"}:
            return {"context": action.context}
        return {
            "hookSpecificOutput": {
                "hookEventName": HOSTS[host]["events"].get(event, event),
                "additionalContext": action.context,
            }
        }
    return {}


def capabilities(host: str) -> tuple[str, ...]:
    return tuple(HOSTS[host]["capabilities"])


def render_config(host: str, hook_path: Path) -> dict:
    command = f"python3 {shlex.quote(str(hook_path))} --host {host}"
    events = HOSTS[host]["events"]
    hooks = {}
    for event in capabilities(host):
        command_line = f"{command} {event}"
        name = events[event]
        if host in {"cursor", "gemini"}:
            hooks[name] = [{"type": "command", "command": command_line}]
        elif HOSTS[host]["style"] == "claude":
            hooks[name] = [
                {
                    "matcher": MATCHERS.get(event, "*"),
                    "hooks": [{"type": "command", "command": command_line}],
                }
            ]
        else:
            hooks[name] = [{"type": "command", "command": command_line}]
    return {"hooks": hooks}
