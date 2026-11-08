"""Independent legacy-byte oracle plus bounded-read and hostile-file probes."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import spec_verify as verify  # noqa: E402

from tests.test_spec_verify import init_repo  # noqa: E402


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)


def legacy_digest(root: Path, package_dir: Path | None = None) -> str:
    """Frozen pre-change framing, not the new helpers or their constants."""
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
        path = raw.decode("utf-8", "surrogateescape")
        if path == ".spec" or path.startswith(".spec/"):
            continue
        if excluded and (path == excluded or path.startswith(excluded + "/")):
            continue
        if path == "runs.jsonl" or path.endswith("/runs.jsonl"):
            continue
        file = root / path
        if not file.is_file():
            continue
        digest.update(raw + b"\0" + file.read_bytes() + b"\0")
    return digest.hexdigest()


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("custom", [False, True])
def test_static_ordinary_tree_matches_independent_legacy_bytes(tmp_path, committed, custom):
    if committed:
        init_repo(tmp_path)
        (tmp_path / "README.md").write_bytes(b"changed tracked\0binary\xff\n")
    else:
        assert git(tmp_path, "init", "-q").returncode == 0
    package = tmp_path / ("custom/specs/pkg" if custom else ".spec/specs/pkg")
    package.mkdir(parents=True)
    (package / "spec.md").write_text("excluded package state\n")
    (tmp_path / ".gitignore").write_text("ignored/\n")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored/large.bin").write_bytes(b"ignored" * 100)
    (tmp_path / "z.bin").write_bytes(bytes(range(256)) * 401)
    (tmp_path / "a\n中文.txt").write_text("filename ordering and newlines\n")
    if sys.platform.startswith("linux"):
        # APFS rejects invalid UTF-8 filenames; Linux preserves their bytes.
        (tmp_path / os.fsdecode(b"invalid-\xff")).write_bytes(b"surrogateescape")
    (tmp_path / "runs.jsonl").write_text("self-excluded\n")
    assert verify.compute_tree_fingerprint(tmp_path, package) == legacy_digest(tmp_path, package)


def test_large_file_and_binary_diff_use_only_bounded_reads(tmp_path, monkeypatch):
    init_repo(tmp_path)
    tracked = tmp_path / "tracked.bin"
    tracked.write_bytes(bytes(range(256)) * 18000)
    assert git(tmp_path, "add", "tracked.bin").returncode == 0
    assert git(tmp_path, "commit", "-qm", "binary fixture").returncode == 0
    tracked.write_bytes(b"changed\0" + bytes(range(256)) * 18000)
    untracked = tmp_path / "large.bin"
    untracked.write_bytes(bytes(range(256)) * 36000)
    expected = legacy_digest(tmp_path)
    real_read_bytes = Path.read_bytes

    def refuse_whole_read(path):
        if path in (tracked, untracked):
            pytest.fail("fingerprint must not allocate whole file contents")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", refuse_whole_read)
    real_git = verify._git

    def refuse_diff_capture(root, *args, **kwargs):
        assert args[0] != "diff", "binary diff must use the streaming pipe"
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(verify, "_git", refuse_diff_capture)
    real_read, reads = os.read, []
    inode = untracked.stat().st_ino

    def bounded_read(fd, size):
        if os.fstat(fd).st_ino == inode:
            reads.append(size)
            assert 0 < size <= 65536
        return real_read(fd, size)

    monkeypatch.setattr(verify.os, "read", bounded_read)
    real_stream, pipe_reads = verify._git_stream, []

    @contextmanager
    def bounded_git_stream(*args):
        with real_stream(*args) as stream:

            class Guarded:
                def read(self, size):
                    assert 0 < size <= 65536
                    pipe_reads.append(size)
                    return stream.read(size)

            yield Guarded()

    monkeypatch.setattr(verify, "_git_stream", bounded_git_stream)
    assert verify.compute_tree_fingerprint(tmp_path) == expected
    assert len(reads) > 100 and len(pipe_reads) > 1


@pytest.mark.parametrize("kind", ["symlink", "broken-symlink", "fifo"])
def test_untrusted_untracked_file_fails_closed_without_blocking(tmp_path, kind, monkeypatch):
    init_repo(tmp_path)
    target = tmp_path / "hostile"
    if kind == "fifo":
        os.mkfifo(target)
    elif kind == "broken-symlink":
        target.symlink_to(tmp_path / "missing")
    else:
        target.symlink_to(tmp_path / "README.md")
    real_git = verify._git

    def listed(root, *args, **kwargs):
        result = real_git(root, *args, **kwargs)
        # Some Git versions omit FIFOs. Directly exercise the unsafe listed
        # entry too; safe_open must never block or silently skip it.
        if args[0] == "ls-files" and b"hostile\0" not in result.stdout:
            result.stdout += b"hostile\0"
        return result

    monkeypatch.setattr(verify, "_git", listed)
    assert verify.compute_tree_fingerprint(tmp_path) is None


@pytest.mark.parametrize("change", ["mutate", "replace"])
def test_file_change_during_read_is_not_a_freshness_claim(tmp_path, monkeypatch, change):
    init_repo(tmp_path)
    target = tmp_path / "input.bin"
    target.write_bytes(b"a" * 200000)
    real_read, changed = os.read, False
    inode = target.stat().st_ino

    def changing_read(fd, size):
        nonlocal changed
        chunk = real_read(fd, size)
        if not changed and os.fstat(fd).st_ino == inode:
            changed = True
            if change == "mutate":
                with target.open("ab") as stream:
                    stream.write(b"new")
            else:
                replacement = tmp_path / ".replacement"
                replacement.write_bytes(b"a" * 200000)
                replacement.replace(target)
        return chunk

    monkeypatch.setattr(verify.os, "read", changing_read)
    assert verify.compute_tree_fingerprint(tmp_path) is None
    assert changed


def test_non_git_tree_is_unverifiable(tmp_path):
    assert verify.compute_tree_fingerprint(tmp_path) is None
