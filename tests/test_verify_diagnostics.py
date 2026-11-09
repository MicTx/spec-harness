"""Failure views are bounded untrusted text; evidence hashes remain raw bytes."""

import hashlib
import shlex
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from spec_verify import DIAGNOSTIC_TAIL_BYTES, STDOUT_TAIL_BYTES, _show_failure_output  # noqa: E402

from tests.test_spec_verify import SLUG, init_repo, make_package, read_records, run_recorder  # noqa: E402


def package_for_command(root: Path, code: str):
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"
    return make_package(
        init_repo(root),
        SLUG,
        runs_protocol=True,
        tasks_text=f"- [ ] command\n  - id: task-diag\n  - boundary: fixture\n  - verify: `{command}`\n",
    )


@pytest.mark.parametrize("exit_code", [0, 1])
def test_failure_only_display_and_unchanged_raw_hash(tmp_path, exit_code):
    stdout = b"stdout assertion\n\x1b[31m\xff\x00\r"
    stderr = b"stderr assertion\n\x1b]0;hostile\x07"
    package = package_for_command(
        tmp_path,
        f"import os; os.write(1, {stdout!r}); os.write(2, {stderr!r}); raise SystemExit({exit_code})",
    )
    result = run_recorder(tmp_path, SLUG, "--task", "task-diag", check=False)
    assert result.returncode == (0 if exit_code == 0 else 1), result.stdout + result.stderr
    record = read_records(package)[0]
    assert record["stdout_tail_sha256"] == hashlib.sha256(stdout[-STDOUT_TAIL_BYTES:]).hexdigest()
    assert "stdout" not in record and "stderr" not in record
    assert list(package.glob("*.log")) == []
    if exit_code:
        assert "stdout assertion" in result.stderr and "stderr assertion" in result.stderr
        assert "untrusted output" in result.stderr and "end stderr" in result.stderr
        assert "\\x1b" in result.stderr and "\\x00" in result.stderr and "\\r" in result.stderr
        assert "\ufffd" in result.stderr
        assert "\x1b" not in result.stderr and "\x00" not in result.stderr and "\x07" not in result.stderr
    else:
        assert "stdout assertion" not in result.stdout + result.stderr
        assert "stderr assertion" not in result.stdout + result.stderr


def test_timeout_displays_both_partial_streams(tmp_path):
    package = package_for_command(
        tmp_path,
        "import os,time; os.write(1,b'partial stdout\\n'); os.write(2,b'partial stderr\\n'); time.sleep(30)",
    )
    result = run_recorder(tmp_path, SLUG, "--task", "task-diag", "--timeout", "1", check=False)
    assert result.returncode == 1
    assert "partial stdout" in result.stderr and "partial stderr" in result.stderr
    assert "exit=timeout" in result.stdout
    assert read_records(package)[0]["exit"] != 0


def test_tail_byte_cap_truncation_and_control_escape(capsys):
    cap = DIAGNOSTIC_TAIL_BYTES
    # Invalid UTF-8 crossing the cut is replaced rather than crashing. Both
    # streams have independent byte budgets, including an all-control stream.
    _show_failure_output("task-cap", b"HIDDEN" + b"x" * cap, b"z" * 17 + b"\x1b" * cap)
    output = capsys.readouterr().err
    assert "HIDDEN" not in output
    assert f"tail {cap}/{cap + 6} bytes; truncated" in output
    assert f"tail {cap}/{cap + 17} bytes; truncated" in output
    assert "x" * cap in output and "\\x1b" * cap in output
    assert len(output.encode("utf-8")) < 6 * cap


def test_empty_and_nonprintable_unicode(capsys):
    _show_failure_output("task-empty", b"", b"")
    assert capsys.readouterr().err == ""
    _show_failure_output("task-utf", b"\xff", "visible\u202e\u009b\n\t".encode())
    output = capsys.readouterr().err
    assert "\ufffd" in output and "\\u202e" in output and "\\x9b" in output
    assert "\u202e" not in output and "\u009b" not in output
