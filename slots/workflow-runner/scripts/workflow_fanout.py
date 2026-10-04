#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""workflow-runner 子进程确定性 fan-out 驱动（pi / codex 双后端）。

仓库自有确定性编排由本脚本承担，pi / codex CLI 仅作为可替换 worker 后端：
输入任务清单 JSON -> 并发派发一次性子进程 -> 收敛为 JSONL 磁盘真源。

设计约束：
- 主会话负责任务拆分与 assignment contract；本脚本只做确定性派发与收敛。
- fail-per-item：单个子进程超时/失败只记该 item 失败，不拖垮整批。
- 永不伪造结构化：子进程输出按文本收敛（stdout 摘要 + 完整输出落盘）。
- 路由/验收/done-push 门禁保留在主会话；本脚本是执行段托管形态之一。

用法：
  python3 workflow_fanout.py --backend pi --plan plan.json --out results.jsonl
  python3 workflow_fanout.py --backend codex --plan plan.json --out results.jsonl \
      --timeout 20 --concurrency 2
  python3 workflow_fanout.py --backend pi --plan plan.json --out results.jsonl --no-cache

结果缓存（断点续传）：
  成功（ok=true）的 item 完整记录原子写入 <out-dir>/cache/<key>.json，
  key = sha256(backend + NUL + id + NUL + prompt)。重跑同键命中直接回填
  （cached: true）不派发；失败项永不入缓存；全命中时不要求后端二进制在场；
  --no-cache 跳过读与写，强制全量派发。

plan.json 结构：
  {
    "items": [
      {"id": "rev-1", "prompt": "<该 item 的完整任务指令>"}
    ]
  }
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import selectors
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

BACKENDS = ("pi", "codex")
STDOUT_SUMMARY_CAP = 2000  # JSONL 内 stdout 摘要上限；完整输出另落文件
MAX_PLAN_ITEMS = 1000
MAX_ID_LENGTH = 128
MAX_PROMPT_LENGTH = 128 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_CONCURRENCY = 64
MAX_TIMEOUT_SECONDS = 24 * 60 * 60


def _validate_item(item: Any, seen: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("each plan item must be an object")
    item_id = item.get("id")
    prompt = item.get("prompt")
    if not isinstance(item_id, str) or not item_id.strip():
        raise ValueError("item id must be a non-empty string")
    if len(item_id) > MAX_ID_LENGTH or item_id in {".", ".."}:
        raise ValueError("item id is too long or reserved")
    if any(ord(char) < 32 or ord(char) == 127 for char in item_id) or "/" in item_id or "\\" in item_id:
        raise ValueError("item id must be a portable path segment")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError(f"item {item_id!r} has an empty prompt")
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise ValueError(f"item {item_id!r} prompt is too long")
    if seen is not None:
        if item_id in seen:
            raise ValueError(f"duplicate item id: {item_id}")
        seen.add(item_id)
    return item


def _safe_child(root: Path, name: str) -> Path:
    """Return a generated file path rooted in *root*, rejecting links."""
    if Path(name).name != name or name in {".", ".."}:
        raise ValueError("generated path is not a single path segment")
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError("generated path contains control characters")
    if root.is_symlink():
        raise ValueError("output directory must not be a symlink")
    root_real = root.resolve()
    candidate = root / name
    if candidate.is_symlink():
        raise ValueError(f"generated path is a symlink: {candidate}")
    if not candidate.resolve(strict=False).is_relative_to(root_real):
        raise ValueError("generated path escapes output directory")
    return candidate


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor) if path.is_absolute() else Path()
    for part in path.parts[1:] if path.is_absolute() else path.parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _kill_process_tree(process: subprocess.Popen) -> None:
    """Kill the worker and any children it spawned.

    Workers run in their own session (``start_new_session=True``), so the
    process group covers grandchildren; a bare ``process.kill()`` would leave
    them running and still holding the pipe write ends.
    """
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        return
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        process.kill()
    except OSError:
        pass


def _run_bounded(cmd: list[str], timeout: float) -> tuple[subprocess.CompletedProcess[str], bool, bool]:
    """Run one worker while bounding captured stdout/stderr in memory."""
    # Keep monkeypatched ``subprocess.run`` compatibility for the unit tests;
    # production always uses the bounded selector loop below.
    if getattr(subprocess.run, "__module__", "subprocess") != "subprocess":
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return completed, False, False
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False, start_new_session=True)
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    stdout = bytearray()
    stderr = bytearray()
    selector.register(process.stdout, selectors.EVENT_READ, stdout)
    selector.register(process.stderr, selectors.EVENT_READ, stderr)
    deadline = time.monotonic() + timeout
    timed_out = False
    output_limited = False
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _kill_process_tree(process)
                break
            for key, _ in selector.select(min(remaining, 0.2)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target = key.data
                # 截断判定必须对 extend 前的剩余空间做：用扩展后长度会把
                # 「整块装入」误判成「丢过字节」，杀伤合法大输出。
                room = MAX_OUTPUT_BYTES - len(target)
                if len(chunk) > room:
                    target.extend(chunk[: max(room, 0)])
                    output_limited = True
                    _kill_process_tree(process)
                    break
                target.extend(chunk)
            if output_limited:
                break
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _kill_process_tree(process)
            process.wait()
    finally:
        for key in list(selector.get_map().values()):
            selector.unregister(key.fileobj)
            key.fileobj.close()
        selector.close()
    return (
        subprocess.CompletedProcess(
            cmd,
            process.returncode,
            bytes(stdout).decode("utf-8", "replace"),
            bytes(stderr).decode("utf-8", "replace"),
        ),
        timed_out,
        output_limited,
    )


def _write_results(path: Path, records: list[dict[str, Any]]) -> None:
    """Write JSONL without following a pre-existing symlink."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_command(backend: str, prompt: str, output_file: Optional[Path]) -> list[str]:
    """按后端构造一次性子进程命令。codex 用 --output-last-message 落盘。

    prompt 之前固定加 ``--``：plan.json 是外部输入面，以 ``-`` 开头的
    prompt 否则会被 worker CLI 的参数解析器吞成选项（pi 与 codex 的
    解析器都支持 ``--`` 终止符）。
    """
    if backend == "pi":
        return ["pi", "-p", "--no-session", "--mode", "text", "--", prompt]
    if backend == "codex":
        cmd = ["codex", "exec", "--sandbox", "read-only", "--", prompt]
        if output_file is not None:
            cmd[4:4] = ["--output-last-message", str(output_file)]
        return cmd
    raise ValueError(f"unknown backend: {backend!r}")


def resolve_binary(backend: str) -> Optional[str]:
    """后端二进制不存在时返回 None（整批 fail-fast，比逐 item 报同样错诚实）。"""
    return shutil.which(backend)


def _cache_key(backend: str, item_id: str, prompt: str) -> str:
    """缓存键 = sha256(backend + NUL + id + NUL + prompt) 十六进制。

    prompt 全文入键：改任务必改 prompt 即必 miss（内容指纹语义）。
    timeout/concurrency 是执行参数，不入键。
    """
    digest = hashlib.sha256(
        backend.encode("utf-8") + b"\x00" + item_id.encode("utf-8") + b"\x00" + prompt.encode("utf-8")
    )
    return digest.hexdigest()


def cache_path(out_dir: Path, key: str) -> Path:
    return out_dir / "cache" / f"{key}.json"


def read_cache(out_dir: Path, key: str) -> Optional[dict[str, Any]]:
    """读缓存：文件不存在/损坏均视为 miss（返回 None），绝不抛错。

    只认 ok=true 的记录（「只缓存成功结果」的读侧不变量）：
    ok=false 的落盘内容（手改/损坏）不回填，重新派发。
    """
    path = cache_path(out_dir, key)
    if path.is_symlink():
        return None
    try:
        if path.stat().st_size > MAX_OUTPUT_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if (isinstance(data, dict) and data.get("ok") is True) else None


def write_cache(out_dir: Path, key: str, record: dict[str, Any]) -> None:
    """原子写缓存：先写独立 tmp 再 rename，读者永远看不到半截文件。

    tmp 名含 uuid4：病态 plan（完全重复的 id+prompt）会同 key 并发写，
    固定 tmp 名会被先完成的线程 rename 移走，后完成线程再 rename 即
    FileNotFoundError 崩溃——独立 tmp 名让同 key 并发写退化为「后写者
    胜出」，两份内容本就是同一 item 的等价收敛，谁赢都不丢语义。
    """
    cache_dir = out_dir / "cache"
    if cache_dir.is_symlink():
        raise OSError("cache directory is a symlink")
    cache_dir.mkdir(parents=True, exist_ok=True)
    final = cache_path(out_dir, key)
    if final.is_symlink():
        raise OSError("cache file is a symlink")
    tmp = final.with_suffix(final.suffix + f".{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    tmp.replace(final)


def run_item(
    backend: str,
    item: dict[str, Any],
    out_dir: Path,
    timeout: float,
    use_cache: bool = True,
) -> dict[str, Any]:
    """派发单个 item 并收敛结果。永不抛错：所有失败记入 result 的 ok=false。

    use_cache=False（--no-cache）时跳过写缓存；缓存读取在 main 派发前完成。
    只有走到最后 ok=True 的记录才落缓存：empty prompt / timeout / spawn
    failed / nonzero exit / empty output 全部提前 return，天然不入缓存。
    缓存写失败（OSError）不抛出：item 结果仍 ok=True，记 cacheWriteError
    提示下次重跑无缓存可用——缓存故障不应改变本次派发的收敛结果。
    """
    item_id = item.get("id") if isinstance(item, dict) else "item"
    item_id = item_id if isinstance(item_id, str) else "item"
    prompt = item.get("prompt") if isinstance(item, dict) else ""
    prompt = prompt.strip() if isinstance(prompt, str) else ""
    started = time.time()
    if not prompt:
        return {
            "id": item_id,
            "backend": backend,
            "ok": False,
            "error": "empty prompt",
            "durationSec": 0.0,
            "outputFile": "",
            "stdoutSummary": "",
        }
    try:
        _validate_item(item)
        full_output = _safe_child(out_dir, f"{item_id}.out")
        dump_output = _safe_child(out_dir, f"{item_id}.stdout")
    except (TypeError, ValueError) as exc:
        return {
            "id": item_id,
            "backend": backend,
            "ok": False,
            "error": str(exc),
            "durationSec": 0.0,
            "outputFile": "",
            "stdoutSummary": "",
        }
    # codex 的 --output-last-message 写这个文件；stdout/stderr 倾倒必须分开，
    # 否则 codex 落盘的最终消息会被覆盖（pi 后端不消费 output_file，不受影响）

    record: dict[str, Any] = {
        "id": item_id,
        "backend": backend,
        "ok": False,
        "error": None,
        "durationSec": 0.0,
        "outputFile": str(full_output),
        "stdoutSummary": "",
    }

    try:
        cmd = build_command(backend, prompt, full_output)
        completed, timed_out, output_limited = _run_bounded(cmd, timeout)
    except subprocess.TimeoutExpired:
        record["error"] = f"timeout after {timeout}s"
        record["durationSec"] = round(time.time() - started, 2)
        return record
    except (OSError, UnicodeError) as exc:
        record["error"] = f"spawn failed: {exc}"
        record["durationSec"] = round(time.time() - started, 2)
        return record

    record["durationSec"] = round(time.time() - started, 2)
    if timed_out:
        record["error"] = f"timeout after {timeout}s"
        return record
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        record["error"] = "subprocess output is not valid text"
        return record
    combined = stdout + ("\n---stderr---\n" + stderr if stderr else "")
    if output_limited or len(combined.encode("utf-8", "replace")) > MAX_OUTPUT_BYTES:
        record["error"] = f"output exceeds {MAX_OUTPUT_BYTES} bytes"
        record["stdoutSummary"] = stdout[:STDOUT_SUMMARY_CAP]
        return record
    try:
        dump_output.write_text(combined, encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        record["error"] = f"output write failed: {exc}"
        return record
    if backend == "pi":
        # pi 没有 codex 式 --output-last-message 落盘，`.out` 永远不会存在；
        # outputFile 必须指向真实产物（完整 stdout/stderr 转储），否则证据链悬空。
        record["outputFile"] = str(dump_output)
    record["stdoutSummary"] = stdout[:STDOUT_SUMMARY_CAP]
    record["returncode"] = completed.returncode

    if completed.returncode != 0:
        record["error"] = f"exit {completed.returncode}: {stderr[:300]}"
        return record

    try:
        if backend == "pi":
            text_target = stdout
        elif full_output.exists():
            text_target = full_output.read_text(encoding="utf-8")
        else:
            text_target = stdout
    except (OSError, UnicodeError) as exc:
        record["error"] = f"output read failed: {exc}"
        return record
    if not text_target.strip():
        record["error"] = "empty output"
        return record

    record["ok"] = True
    if use_cache:
        try:
            write_cache(out_dir, _cache_key(backend, item_id, prompt), record)
        except OSError:
            # 缓存是加速器不是真源：落盘失败只影响下次重跑的速度，绝不
            # 让缓存故障把已成功的 item 记成崩溃（fail-per-item 不变量）。
            record["cacheWriteError"] = "cache write failed"
    return record


def load_plan(plan_path: Path) -> list[dict[str, Any]]:
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid plan: {exc}") from exc
    if not isinstance(plan, dict):
        raise ValueError("plan must be a JSON object")
    items = plan.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("plan must contain a non-empty 'items' array")
    if len(items) > MAX_PLAN_ITEMS:
        raise ValueError(f"plan contains more than {MAX_PLAN_ITEMS} items")
    seen: set[str] = set()
    for item in items:
        _validate_item(item, seen)
    return items


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="workflow_fanout.py", description="子进程确定性 fan-out 驱动")
    parser.add_argument("--backend", required=True, choices=BACKENDS)
    parser.add_argument("--plan", required=True, help="任务清单 JSON（items + prompt）")
    parser.add_argument("--out", required=True, help="结果 JSONL 输出路径")
    parser.add_argument("--out-dir", default=None, help="完整输出目录（默认 <out>.d/）")
    parser.add_argument("--concurrency", type=int, default=3, help="并发上限（默认 3）")
    parser.add_argument("--timeout", type=float, default=300.0, help="单 item 硬超时秒（默认 300）")
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="跳过结果缓存读与写，强制全量派发（逃生门：怀疑缓存污染时用）",
    )
    args = parser.parse_args(argv)

    if args.concurrency < 1 or args.concurrency > MAX_CONCURRENCY:
        print(f"[fanout] invalid concurrency: 1..{MAX_CONCURRENCY}", file=sys.stderr)
        return 2
    if not math.isfinite(args.timeout) or args.timeout <= 0 or args.timeout > MAX_TIMEOUT_SECONDS:
        print(f"[fanout] invalid timeout: 0..{MAX_TIMEOUT_SECONDS}s", file=sys.stderr)
        return 2

    out_path = Path(args.out)
    out_dir = Path(args.out_dir) if args.out_dir else out_path.with_suffix(out_path.suffix + ".d")
    try:
        if _has_symlink_component(out_path) or _has_symlink_component(out_dir):
            raise ValueError("output path must not be a symlink")
        out_dir.mkdir(parents=True, exist_ok=True)
        if not out_dir.is_dir():
            raise ValueError("output directory is not a directory")
        cache_dir = out_dir / "cache"
        if cache_dir.is_symlink():
            raise ValueError("cache directory must not be a symlink")
        if out_path.exists() and not out_path.is_file():
            raise ValueError("result output must be a regular file")
        if args.out_dir is not None and not out_path.resolve(strict=False).is_relative_to(out_dir.resolve()):
            raise ValueError("result output must be inside --out-dir")
        items = load_plan(Path(args.plan))
    except (OSError, ValueError) as exc:
        print(f"[fanout] invalid input: {exc}", file=sys.stderr)
        return 2

    # 派发前查缓存：命中的 item 直接回填该 record（cached: true），不进线程池。
    # key 的 id/prompt 归一化必须与 run_item 完全一致，否则会假 miss。
    records: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for item in items:
        if args.no_cache:
            pending.append(item)
            continue
        item_id = item["id"]
        prompt = item["prompt"].strip()
        cached = read_cache(out_dir, _cache_key(args.backend, item_id, prompt))
        if cached is not None:
            record = dict(cached)
            record["cached"] = True
            records.append(record)
        else:
            pending.append(item)

    binary = resolve_binary(args.backend)
    if binary is None and pending:
        # 整批 fail-fast：二进制缺失时每个未命中项都会同样失败，逐条跑只是浪费。
        # 全命中（pending 为空）时跳过本分支——真断点续传不要求二进制在场。
        records.extend(
            {
                "id": str(it.get("id") or "item"),
                "backend": args.backend,
                "ok": False,
                "error": f"backend binary not found: {args.backend}",
                "durationSec": 0.0,
                "outputFile": "",
                "stdoutSummary": "",
            }
            for it in pending
        )
        # 收敛顺序稳定：按 plan 顺序回填，便于对照
        order = {str(it.get("id") or "item"): i for i, it in enumerate(items)}
        records.sort(key=lambda r: order.get(r["id"], len(order)))
        try:
            _write_results(out_path, records)
        except (OSError, UnicodeError) as exc:
            print(f"[fanout] result write failed: {exc}", file=sys.stderr)
            return 2
        cached_count = sum(1 for r in records if r.get("cached"))
        print(f"[fanout] backend missing: {args.backend}; {len(pending)} items recorded as failed")
        print(
            f"[fanout] backend={args.backend} items={len(records)} "
            f"ok={len(records) - len(pending)} fail={len(pending)} cached={cached_count}"
        )
        print(f"[fanout] results: {out_path}")
        return 1

    if pending:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            futures = {
                pool.submit(run_item, args.backend, item, out_dir, args.timeout, not args.no_cache): item
                for item in pending
            }
            for future in concurrent.futures.as_completed(futures):
                records.append(future.result())

    # 收敛顺序稳定：按 plan 顺序回填，便于对照
    order = {str(it.get("id") or "item"): i for i, it in enumerate(items)}
    records.sort(key=lambda r: order.get(r["id"], len(order)))

    try:
        _write_results(out_path, records)
    except (OSError, UnicodeError) as exc:
        print(f"[fanout] result write failed: {exc}", file=sys.stderr)
        return 2

    ok_count = sum(1 for r in records if r["ok"])
    cached_count = sum(1 for r in records if r.get("cached"))
    print(
        f"[fanout] backend={args.backend} items={len(records)} "
        f"ok={ok_count} fail={len(records) - ok_count} cached={cached_count}"
    )
    print(f"[fanout] results: {out_path}")
    return 0 if ok_count == len(records) else 2


if __name__ == "__main__":
    sys.exit(main())
