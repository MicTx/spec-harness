"""Fingerprint framing, bounded reads and explicit symlink behavior."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import spec_verify as verify  # noqa: E402

from tests.test_spec_verify import init_repo  # noqa: E402


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)


def independent_digest(root: Path, package_dir: Path | None = None) -> str:
    """Recreate the byte framing without using the implementation helpers."""
    excluded = ""
    if package_dir is not None and package_dir.relative_to(root).parts[0] != ".spec":
        excluded = package_dir.relative_to(root).parent.as_posix()
    paths = [":(exclude).spec"] + ([f":(exclude,literal){excluded}"] if excluded else [])
    head = git(root, "rev-parse", "HEAD")
    head_bytes = head.stdout.strip() if head.returncode == 0 else b""
    diff = git(root, "diff", "--binary", *(["HEAD"] if head.returncode == 0 else []), "--", ".", *paths)
    assert diff.returncode == 0
    digest = hashlib.sha256(head_bytes + b"\0" + diff.stdout + b"\0")
    listed = git(root, "ls-files", "--others", "--exclude-standard", "-z")
    assert listed.returncode == 0
    for raw in sorted(part for part in listed.stdout.split(b"\0") if part):
        path = os.fsdecode(raw)
        if path == ".spec" or path.startswith(".spec/"):
            continue
        if excluded and (path == excluded or path.startswith(excluded + "/")):
            continue
        if Path(path).name == "runs.jsonl":
            continue
        file = root / path
        assert file.is_file() and not file.is_symlink()
        digest.update(raw + b"\0" + file.read_bytes() + b"\0")
    return digest.hexdigest()


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("custom", [False, True])
def test_ordinary_tree_matches_independent_digest(tmp_path, committed, custom):
    if committed:
        init_repo(tmp_path)
        (tmp_path / "README.md").write_bytes(b"changed tracked\0binary\xff\n")
    else:
        assert git(tmp_path, "init", "-q").returncode == 0
    package = tmp_path / ("custom/specs/pkg" if custom else ".spec/specs/pkg")
    package.mkdir(parents=True)
    (package / "spec.md").write_text("excluded package state\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored/large.bin").write_bytes(b"ignored" * 100)
    (tmp_path / "z.bin").write_bytes(bytes(range(256)) * 401)
    (tmp_path / "a\n中文.txt").write_text("filename ordering and newlines\n", encoding="utf-8")
    if sys.platform.startswith("linux"):
        (tmp_path / os.fsdecode(b"invalid-\xff")).write_bytes(b"surrogateescape")
    (tmp_path / "runs.jsonl").write_text("self-excluded\n", encoding="utf-8")
    assert verify.compute_tree_fingerprint(tmp_path, package) == independent_digest(tmp_path, package)


class GuardedReader:
    def __init__(self, stream, reads):
        self.stream = stream
        self.reads = reads

    def read(self, size):
        assert 0 < size <= verify.FINGERPRINT_CHUNK_BYTES
        self.reads.append(size)
        return self.stream.read(size)

    def close(self):
        self.stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_large_file_and_binary_diff_use_bounded_reads(tmp_path, monkeypatch):
    init_repo(tmp_path)
    tracked = tmp_path / "tracked.bin"
    tracked.write_bytes(bytes(range(256)) * 18000)
    assert git(tmp_path, "add", "tracked.bin").returncode == 0
    assert git(tmp_path, "commit", "-qm", "binary fixture").returncode == 0
    tracked.write_bytes(b"changed\0" + bytes(range(256)) * 18000)
    untracked = tmp_path / "large.bin"
    untracked.write_bytes(bytes(range(256)) * 36000)
    expected = independent_digest(tmp_path)
    real_open = Path.open
    file_reads = []

    def guarded_open(path, *args, **kwargs):
        stream = real_open(path, *args, **kwargs)
        return GuardedReader(stream, file_reads) if path == untracked else stream

    monkeypatch.setattr(Path, "open", guarded_open)
    real_popen = subprocess.Popen
    pipe_reads = []

    def guarded_popen(args, *positional, **kwargs):
        process = real_popen(args, *positional, **kwargs)
        if isinstance(args, list) and args[:3] == ["git", "-C", str(tmp_path)] and "diff" in args:
            process.stdout = GuardedReader(process.stdout, pipe_reads)
        return process

    monkeypatch.setattr(verify.subprocess, "Popen", guarded_popen)
    assert verify.compute_tree_fingerprint(tmp_path) == expected
    assert len(file_reads) > 100
    assert len(pipe_reads) >= 2


def test_untracked_symlink_hashes_link_target_not_target_contents(tmp_path):
    init_repo(tmp_path)
    target = tmp_path / ".git" / "link-target"
    target.write_text("first", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(".git/link-target")
    first = verify.compute_tree_fingerprint(tmp_path)
    target.write_text("second", encoding="utf-8")
    assert verify.compute_tree_fingerprint(tmp_path) == first
    link.unlink()
    link.symlink_to("missing-target")
    second = verify.compute_tree_fingerprint(tmp_path)
    assert second is not None and second != first


def test_listed_fifo_is_not_read_or_treated_as_a_regular_file(tmp_path, monkeypatch):
    init_repo(tmp_path)
    os.mkfifo(tmp_path / "fifo")
    real_git = verify.gitx.git

    def include_fifo(root, *args, **kwargs):
        result = real_git(root, *args, **kwargs)
        if args[0] == "ls-files" and b"fifo\0" not in result.stdout:
            return subprocess.CompletedProcess(result.args, result.returncode, result.stdout + b"fifo\0", result.stderr)
        return result

    monkeypatch.setattr(verify.gitx, "git", include_fifo)
    assert verify.compute_tree_fingerprint(tmp_path) is None


def test_non_git_tree_is_unverifiable(tmp_path):
    assert verify.compute_tree_fingerprint(tmp_path) is None
