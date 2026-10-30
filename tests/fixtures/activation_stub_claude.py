#!/usr/bin/env python3
"""Stub claude binary for executor contract tests (task-host-executor).

Never talks to a model. Driven by a JSON plan at $ACT_STUB_PLAN:

    {
      "version": "stub-2.1.296",
      "model": "stub-model",
      "hang": false,
      "exit_code": 0,
      "marker": "path-written-when-invoked",
      "sessions": [
        {
          "match": "prompt-prefix",
          "events": [
            {"emit_tool": {"name": "Skill", "input": {"skill": "spec"}}},
            {"emit_tool": {"name": "Bash", "input": {"command": "init_spec_package.py ..."}}},
            {"write": {"app/users.py": "..."}},          # also emits a Write tool_use
            {"emit_tool_result": "impl-1 running"},
            {"sleep": 30}
          ],
          "usage": {"input_tokens": 10, "output_tokens": 2}
        }
      ]
    }

The first session whose ``match`` prefixes the turn prompt wins; unmatched
turns produce no events. ``--version`` answers from ``version``. Works both in
single-prompt mode (``-p PROMPT``) and ``--input-format stream-json`` mode with
``--replay-user-messages``, mirroring the two-turn teammate flow.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def plan() -> dict:
    """Plan lookup: stub-plan.json next to the stub binary, or $ACT_STUB_PLAN."""
    beside = Path(__file__).resolve().parent / "stub-plan.json"
    source = beside if beside.is_file() else Path(os.environ["ACT_STUB_PLAN"])
    data = json.loads(source.read_text(encoding="utf-8"))
    data["_plan_source"] = str(source)
    return data


def bump_spawn_counter(source: Path) -> int:
    """One index per SESSION spawn; --version probes never consume an index.

    Under the OS sandbox only the unit directory is writable, so the counter
    falls back to a CWD-relative location before giving up (index 0, never a
    crash: a stub that dies would fail the unit for harness reasons).
    """
    counter = source.with_suffix(source.suffix + ".spawns")
    try:
        index = int(counter.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = 0
    for target in (counter, Path.cwd().parent / ".stub-spawns"):
        try:
            target.write_text(str(index + 1), encoding="utf-8")
            break
        except OSError:
            continue
    return index


def init_tools(data: dict) -> list:
    """The init event's tool list: plan ``init_tools`` as a list, or a dict keyed by spawn index."""
    tools = data.get("init_tools", [])
    if isinstance(tools, dict):
        tools = tools.get(str(data["spawn_index"]), [])
    return tools if isinstance(tools, list) else []


def emit(line: dict) -> None:
    sys.stdout.write(json.dumps(line, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def session_for(sessions: list, prompt: str, spawn_index: int) -> dict:
    for entry in sessions:
        if "spawn" in entry and entry["spawn"] != spawn_index:
            continue
        if entry.get("match", "\x00") in prompt:
            return entry
    return {"events": [], "usage": {}}


def run_turn(session: dict, prompt: str, replay: bool) -> None:
    if replay:
        emit({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}})
    for event in session.get("events", []):
        if "emit_tool" in event:
            emit(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": event["emit_tool"]["name"],
                                "input": event["emit_tool"].get("input", {}),
                            }
                        ]
                    },
                }
            )
        elif "write" in event:
            emit(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": "Write", "input": {"file_path": "stub"}}]},
                }
            )
            for path, content in event["write"].items():
                target = Path.cwd() / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
        elif "emit_tool_result" in event:
            emit(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "content": [{"type": "text", "text": event["emit_tool_result"]}]}
                        ],
                    },
                }
            )
        elif "emit_tool_result_json" in event:
            emit(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "tool_result", "content": event["emit_tool_result_json"]}],
                    },
                }
            )
        elif "sleep" in event:
            time.sleep(float(event["sleep"]))
    if "exit_before_result" in session:
        # the host dies mid-turn: the turn's result event is never written
        sys.stdout.flush()
        os._exit(int(session["exit_before_result"]))
    usage = session.get("usage", {})
    emit(
        {
            "type": "result",
            "subtype": session.get("result_subtype", "success"),
            "num_turns": 1,
            "is_error": bool(session.get("result_is_error", False)),
            "usage": usage,
        }
    )


def main() -> int:
    args = sys.argv[1:]
    data = plan()
    marker = data.get("marker")
    if marker:
        Path(marker).write_text("invoked", encoding="utf-8")
    if "--version" in args:
        print(data.get("version", "stub"))
        return 0
    data["spawn_index"] = bump_spawn_counter(Path(data.pop("_plan_source")))
    if data.get("stderr"):
        print(data["stderr"], file=sys.stderr, flush=True)
    replay = "--replay-user-messages" in args
    if data.get("hang"):
        time.sleep(600)
        return int(data.get("exit_code", 0))
    stream_input = (
        "--input-format" in args
        and "stream-json" in args[args.index("--input-format") + 1 : args.index("--input-format") + 2]
    )
    if stream_input:
        sys.stdout.reconfigure(line_buffering=True)
        emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "stub-session",
                "model": data.get("model", os.environ.get("ACT_STUB_MODEL", "stub-model")),
                "tools": init_tools(data),
            }
        )
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            content = entry.get("message", {}).get("content")
            if entry.get("type") != "user":
                continue
            if isinstance(content, str):
                run_turn(session_for(data.get("sessions", []), content, data["spawn_index"]), content, replay)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        run_turn(session_for(data.get("sessions", []), text, data["spawn_index"]), text, replay)
        return int(data.get("exit_code", 0))
    if "-p" in args and args.index("-p") + 1 < len(args) and not args[args.index("-p") + 1].startswith("-"):
        prompt = args[args.index("-p") + 1]
        emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "stub-session",
                "model": data.get("model", os.environ.get("ACT_STUB_MODEL", "stub-model")),
                "tools": init_tools(data),
            }
        )
        run_turn(session_for(data.get("sessions", []), prompt, data["spawn_index"]), prompt, replay)
    else:
        # -p as a flag: the prompt is the trailing positional argument. Flags that
        # TAKE A VALUE (e.g. --session-id <uuid>, --max-turns 40) must not have
        # their value mistaken for the prompt.
        takes_value = {
            "--session-id",
            "--max-turns",
            "--output-format",
            "--input-format",
            "--teammate-mode",
            "--permission-mode",
            "--model",
            "--name",
        }
        positional, skip = [], False
        for index, arg in enumerate(args):
            if skip:
                skip = False
                continue
            if arg in takes_value:
                skip = True
                continue
            if not arg.startswith("-"):
                positional.append(arg)
        prompt = positional[-1] if positional else ""
        emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "stub-session",
                "model": data.get("model", os.environ.get("ACT_STUB_MODEL", "stub-model")),
                "tools": init_tools(data),
            }
        )
        run_turn(session_for(data.get("sessions", []), prompt, data["spawn_index"]), prompt, replay)
    return int(data.get("exit_code", 0))


if __name__ == "__main__":
    sys.exit(main())
