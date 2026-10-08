"""Tests for scripts/sweep_chain_windows.py (the F18 dead chain-window sweep CLI)."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import chain_spawn_support as support  # noqa: E402  # type: ignore
import sweep_chain_windows  # noqa: E402  # type: ignore


class TestSweepCLI:
    @staticmethod
    def _seeds(tmp_path):
        autorun = tmp_path / ".spec" / "autorun"
        autorun.mkdir(parents=True, exist_ok=True)
        (autorun / "spawns.jsonl").write_text(
            json.dumps({"round": 1, "prev_tty": "/dev/ttys009"}) + "\n", encoding="utf-8"
        )
        return autorun

    def test_dry_run_lists_without_closing(self, tmp_path, capsys, monkeypatch):
        self._seeds(tmp_path)
        closed: list = []
        monkeypatch.setattr(
            support,
            "find_dead_chain_windows",
            lambda recorded, exclude_tty=None: [{"window_id": 42, "tty": "/dev/ttys009"}],
        )
        monkeypatch.setattr(
            support, "close_terminal_windows", lambda ids: closed.append(ids) or {"closed": [], "skipped": []}
        )
        assert sweep_chain_windows.main(["--root", str(tmp_path), "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "recorded chain ttys: 1" in out
        assert "dead chain windows: 1" in out
        assert "- window 42 on /dev/ttys009 (idle, chain-recorded)" in out
        assert "dry-run: nothing closed" in out
        assert closed == []  # nothing touched

    def test_dry_run_json_shape(self, tmp_path, capsys, monkeypatch):
        self._seeds(tmp_path)
        monkeypatch.setattr(
            support,
            "find_dead_chain_windows",
            lambda recorded, exclude_tty=None: [{"window_id": 42, "tty": "/dev/ttys009"}],
        )
        assert sweep_chain_windows.main(["--root", str(tmp_path), "--dry-run", "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload == {
            "dry_run": True,
            "recorded_ttys": 1,
            "dead_windows": [{"window_id": 42, "tty": "/dev/ttys009"}],
        }

    def test_real_run_closes_and_reports(self, tmp_path, capsys, monkeypatch):
        self._seeds(tmp_path)
        monkeypatch.setattr(
            support,
            "find_dead_chain_windows",
            lambda recorded, exclude_tty=None: [
                {"window_id": 42, "tty": "/dev/ttys009"},
                {"window_id": 43, "tty": "/dev/ttys010"},
            ],
        )
        monkeypatch.setattr(support, "close_terminal_windows", lambda ids: {"closed": [42], "skipped": [43]})
        assert sweep_chain_windows.main(["--root", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "found: 2" in out
        assert "closed: 1 (42)" in out
        assert "skipped: 1 (43)" in out

    def test_real_run_json_shape(self, tmp_path, capsys, monkeypatch):
        self._seeds(tmp_path)
        monkeypatch.setattr(support, "find_dead_chain_windows", lambda recorded, exclude_tty=None: [])
        monkeypatch.setattr(support, "close_terminal_windows", lambda ids: {"closed": [], "skipped": []})
        assert sweep_chain_windows.main(["--root", str(tmp_path), "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload == {"dry_run": False, "recorded_ttys": 1, "found": 0, "closed": [], "skipped": []}

    def test_bad_root_refuses(self, tmp_path, capsys):
        assert sweep_chain_windows.main(["--root", str(tmp_path / "nope")]) == 1
        assert "not a directory" in capsys.readouterr().err
