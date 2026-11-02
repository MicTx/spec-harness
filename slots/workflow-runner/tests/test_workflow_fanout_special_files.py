"""Offline, watchdog-protected regressions for special-file output paths."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import workflow_fanout as wf


pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or not hasattr(os, "mkfifo"),
    reason="FIFO and verified process-group cleanup require Linux",
)


def _run_isolated(tmp_path: Path, program: str, *args: str) -> None:
    # A separate process lets the timeout kill the old, blocked FIFO reader.
    # Its only worker is a local Python stub; no model CLI is invoked.
    result = subprocess.run(
        [sys.executable, "-c", program, str(Path(wf.__file__).parent), str(tmp_path), *args],
        capture_output=True,
        text=True,
        timeout=12,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("damaged", ["cache-entry", "cached-artifact"])
def test_fifo_cache_is_miss_and_redispatches(tmp_path, damaged):
    _run_isolated(
        tmp_path,
        r'''
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import workflow_fanout as wf
root, damaged = Path(sys.argv[2]), sys.argv[3]
plan, out = root / "plan.json", root / "result.jsonl"
plan.write_text(json.dumps({"items": [{"id": "a", "prompt": "hello"}]}))
calls = []
wf.resolve_binary = lambda backend: sys.executable
wf._workspace_context = lambda value, binary: "offline-context"
def build(backend, prompt, output):
    calls.append(prompt)
    return [sys.executable, "-c", "print('stub answer')"]
wf.build_command = build
args = ["--backend", "pi", "--plan", str(plan), "--out", str(out), "--cache-context", "offline"]
assert wf.main(args) == 0
first = json.loads(out.read_text())
out_dir = out.with_suffix(".jsonl.d")
key = wf._cache_key("pi", "a", "hello", "offline-context")
victim = wf.cache_path(out_dir, key) if damaged == "cache-entry" else Path(first["outputFile"])
victim.unlink()
os.mkfifo(victim)
assert wf.main(args) == 0
second = json.loads(out.read_text())
assert second["ok"] is True and not second.get("cached")
assert Path(second["outputFile"]).read_text().strip() == "stub answer"
assert wf.main(args) == 0
third = json.loads(out.read_text())
assert third["ok"] is True and third["cached"] is True
assert calls == ["hello", "hello"]
''',
        damaged,
    )


def test_fifo_new_codex_artifact_fails_without_hanging(tmp_path):
    _run_isolated(
        tmp_path,
        r'''
import os, stat, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import workflow_fanout as wf
root = Path(sys.argv[2])
wf.build_command = lambda backend, prompt, output: [
    sys.executable, "-c", "import os,sys; os.mkfifo(sys.argv[1]); print('progress')", str(output)
]
record = wf.run_item("codex", {"id": "a", "prompt": "hello"}, root, 4)
assert not record["ok"] and "output read failed" in record["error"], record
assert stat.S_ISFIFO(os.lstat(record["outputFile"]).st_mode)
''',
    )


def test_fifo_swap_between_stat_and_open_does_not_block(tmp_path):
    _run_isolated(
        tmp_path,
        r'''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import workflow_fanout as wf
root = Path(sys.argv[2])
target = root / "artifact"
target.write_text("valid before the race")
real_stat = os.stat
swapped = False
def swapping_stat(path, *, dir_fd=None, follow_symlinks=True):
    global swapped
    info = real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
    if path == target.name and dir_fd is not None and not swapped:
        target.unlink()
        os.mkfifo(target)
        swapped = True
    return info
os.stat = swapping_stat
try:
    wf._read_artifact(root, target)
except OSError as exc:
    assert "regular file" in str(exc), exc
else:
    raise AssertionError("FIFO replacement was accepted")
assert swapped
''',
    )


def test_regular_read_limit_and_cache_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "MAX_OUTPUT_BYTES", 512)
    artifact = tmp_path / "answer.out"
    artifact.write_bytes(b"a" * 512)
    assert wf._read_artifact(tmp_path, artifact) == b"a" * 512
    artifact.write_bytes(b"a" * 513)
    with pytest.raises(OSError, match="bounded regular"):
        wf._read_artifact(tmp_path, artifact)

    artifact.write_bytes(b"answer")
    record = {"id": "a", "backend": "pi", "ok": True, "outputFile": str(artifact),
              "outputSha256": hashlib.sha256(b"answer").hexdigest()}
    wf.write_cache(tmp_path, "key", record)
    assert wf.read_cache(tmp_path, "key") == record
    cache_file = wf.cache_path(tmp_path, "key")
    cache_file.write_bytes(b" " * 513)
    assert wf.read_cache(tmp_path, "key") is None
    cache_file.write_bytes(b"\xff")
    assert wf.read_cache(tmp_path, "key") is None


def test_cache_growth_after_fstat_still_uses_bounded_read(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "MAX_OUTPUT_BYTES", 512)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_file = cache_dir / "key.json"
    cache_file.write_text("{}")
    original_fdopen = os.fdopen
    read_sizes = []

    class Reader:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def read(self, size):
            read_sizes.append(size)
            return self.handle.read(size)

    def grow_after_fstat(fd, *args, **kwargs):
        with cache_file.open("ab") as handle:
            handle.write(b"x" * 1024)
        return Reader(original_fdopen(fd, *args, **kwargs))

    monkeypatch.setattr(os, "fdopen", grow_after_fstat)
    assert wf.read_cache(tmp_path, "key") is None
    assert read_sizes == [513]


def test_symlink_swap_between_stat_and_open_is_rejected(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    target = out_dir / "artifact"
    target.write_text("original")
    outside = tmp_path / "outside"
    outside.write_text("outside content")
    real_stat = os.stat
    swapped = False

    def swapping_stat(path, *, dir_fd=None, follow_symlinks=True):
        nonlocal swapped
        info = real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        if path == target.name and dir_fd is not None and not swapped:
            target.unlink()
            target.symlink_to(outside)
            swapped = True
        return info

    monkeypatch.setattr(os, "stat", swapping_stat)
    with pytest.raises(OSError):
        wf._read_artifact(out_dir, target)
    assert swapped and outside.read_text() == "outside content"


@pytest.mark.parametrize("kind", ["directory", "socket"])
def test_other_non_regular_artifacts_are_rejected(tmp_path, kind):
    target = tmp_path / "artifact"
    if kind == "directory":
        target.mkdir()
        with pytest.raises(OSError, match="regular file"):
            wf._read_artifact(tmp_path, target)
    else:
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(target))
            with pytest.raises(OSError, match="regular file"):
                wf._read_artifact(tmp_path, target)


def test_symlinked_cache_directory_is_a_miss(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "cache").symlink_to(outside, target_is_directory=True)
    (outside / "key.json").write_text("{}")
    assert wf.read_cache(out_dir, "key") is None
