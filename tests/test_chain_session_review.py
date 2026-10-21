from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

import chain_session  # noqa: E402
import chain_spawn_support as support  # noqa: E402

from tests.test_chain_session import (  # noqa: E402
    DETAIL_REL,
    MASTER_EXPANDED,
    MASTER_REL,
    OWNER_A,
    append_journal,
    autoplan_begin,
    autoplan_to_ready,
    cli_err,
    cli_ok,
    current_state_dict,
    git_init,
    journal_path,
    journal_row,
    journal_rows,
    mut_args,
    read_summary,
    requires_git,
    seed_autoplan,
    summary_path,
    tree_bytes,
    write,
    write_summary,
)


def begin_args(root, owner, revision, chain_id, *extra):
    return [
        "begin",
        *mut_args("autoplan", root, owner, revision, chain_id),
        "--step-id",
        "f1",
        "--client",
        "devin",
        "--surface",
        "desktop",
        "--kind",
        "framework",
        "--plan",
        MASTER_REL,
        *extra,
    ]


class TestEpochBoundMutations:
    def _two_generations(self, capsys, root):
        ready = autoplan_to_ready(capsys, root)
        old_id = ready["state"]["chain_id"]
        cli_ok(capsys, "close", *mut_args("autoplan", root, OWNER_A, 6, old_id))
        second = autoplan_begin(
            capsys,
            root,
            step_id="f2",
            kind="framework",
            revision=0,
            plan=MASTER_REL,
            chain_id="next-chain",
        )
        assert old_id == "test-chain"
        assert second["state"]["chain_id"] == "next-chain"
        return old_id, second["state"]["chain_id"]

    @pytest.mark.parametrize(
        "argv",
        [
            lambda root, cid: ["finish", *mut_args("autoplan", root, OWNER_A, 1, cid)],
            lambda root, cid: ["pause", *mut_args("autoplan", root, OWNER_A, 1, cid), "--reason", "old hold"],
            lambda root, cid: ["resume", *mut_args("autoplan", root, OWNER_A, 1, cid), "--takeover"],
            lambda root, cid: ["close", *mut_args("autoplan", root, OWNER_A, 1, cid)],
        ],
        ids=["finish", "pause", "resume", "close"],
    )
    def test_old_generation_mutation_refused_after_new_chain(self, tmp_path, capsys, argv):
        old_id, _new_id = self._two_generations(capsys, tmp_path)
        journal_before = journal_path(tmp_path, "autoplan").read_bytes()
        summary_before = summary_path(tmp_path, "autoplan").read_bytes()
        err = cli_err(capsys, *argv(tmp_path, old_id))
        assert "never replace a stale request's chain-id" in err
        assert journal_path(tmp_path, "autoplan").read_bytes() == journal_before
        assert summary_path(tmp_path, "autoplan").read_bytes() == summary_before
        assert read_summary(tmp_path, "autoplan")["chain_id"] == "next-chain"

    def test_replayed_finish_cannot_receipt_new_generation(self, tmp_path, capsys):
        old_id, new_id = self._two_generations(capsys, tmp_path)
        err = cli_err(capsys, "finish", *mut_args("autoplan", tmp_path, OWNER_A, 1, old_id))
        assert "never replace a stale request's chain-id" in err
        status = cli_ok(capsys, "status", "--chain", "autoplan", "--root", str(tmp_path), "--format", "json")
        assert status["chain_id"] == new_id
        assert status["step"]["status"] == "active"
        assert len(journal_rows(tmp_path, "autoplan")) == 8

    def test_begin_with_foreign_id_refused_on_live_chain(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        journal_before = journal_path(tmp_path, "autoplan").read_bytes()
        err = cli_err(
            capsys,
            "begin",
            *mut_args("autoplan", tmp_path, OWNER_A, 1, "other-id"),
            "--step-id",
            "d1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "detail",
            "--target",
            DETAIL_REL,
        )
        assert "never replace a stale request's chain-id" in err
        assert journal_path(tmp_path, "autoplan").read_bytes() == journal_before

    def test_chain_id_reuse_after_close_refused_fresh_id_accepted(self, tmp_path, capsys):
        ready = autoplan_to_ready(capsys, tmp_path)
        old_id = ready["state"]["chain_id"]
        cli_ok(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 6, old_id))
        before = tree_bytes(tmp_path)
        err = cli_err(capsys, *begin_args(tmp_path, OWNER_A, 0, old_id))
        assert "earlier generation" in err
        assert tree_bytes(tmp_path) == before
        second = autoplan_begin(
            capsys, tmp_path, step_id="f2", kind="framework", revision=0, plan=MASTER_REL, chain_id="next-chain"
        )
        assert second["state"]["chain_id"] == "next-chain"
        assert second["state"]["revision"] == 1
        assert second["state"]["pass"] == 1

    def test_reused_id_refused_past_immediate_closure(self, tmp_path, capsys):
        ready_a = autoplan_to_ready(capsys, tmp_path)
        id_a = ready_a["state"]["chain_id"]
        cli_ok(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 6, id_a))
        ready_b = autoplan_to_ready(capsys, tmp_path, chain_id="chain-b")
        id_b = ready_b["state"]["chain_id"]
        cli_ok(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 6, id_b))
        before = tree_bytes(tmp_path)
        err = cli_err(capsys, *begin_args(tmp_path, OWNER_A, 0, id_a))
        assert "earlier generation" in err
        assert tree_bytes(tmp_path) == before
        third = autoplan_begin(
            capsys, tmp_path, step_id="f3", kind="framework", revision=0, plan=MASTER_REL, chain_id="chain-c"
        )
        assert third["state"]["chain_id"] == "chain-c"
        assert third["state"]["revision"] == 1

    def test_identical_initial_begin_retry_stays_idempotent(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        argv = begin_args(tmp_path, OWNER_A, 0, "chain-one")
        first = cli_ok(capsys, *argv)
        repeat = cli_ok(capsys, *argv)
        assert repeat["duplicate"] is True
        assert repeat["state"]["chain_id"] == first["state"]["chain_id"] == "chain-one"
        assert len(journal_rows(tmp_path, "autoplan")) == 1

    def test_missing_chain_id_is_usage_error_without_writes(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        argv = [
            "begin",
            "--chain",
            "autoplan",
            "--root",
            str(tmp_path),
            "--owner",
            OWNER_A,
            "--expected-revision",
            "0",
            "--step-id",
            "f1",
            "--client",
            "devin",
            "--surface",
            "desktop",
            "--kind",
            "framework",
            "--plan",
            MASTER_REL,
        ]
        with pytest.raises(SystemExit):
            chain_session.main(argv)
        capsys.readouterr()
        assert not summary_path(tmp_path, "autoplan").exists()
        assert not journal_path(tmp_path, "autoplan").exists()

    @pytest.mark.parametrize("bad", ["has space", "slash/sep", "x" * 129, ""])
    def test_invalid_chain_id_format_refused(self, tmp_path, capsys, bad):
        seed_autoplan(tmp_path)
        err = cli_err(capsys, *begin_args(tmp_path, OWNER_A, 0, bad))
        assert "chain-id" in err or "chain_id" in err
        assert not summary_path(tmp_path, "autoplan").exists()

    def test_close_on_closed_view_requires_recorded_marker_id(self, tmp_path, capsys):
        ready = autoplan_to_ready(capsys, tmp_path)
        old_id = ready["state"]["chain_id"]
        cli_ok(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 6, old_id))
        err = cli_err(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 0, "wrong-id"))
        assert "Do not retarget" in err
        again = cli_ok(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 0, old_id))
        assert again["status"] == "already_closed"


class TestSchemaPromotion:
    def _v2_epoch(self, root, chain_id="v2-chain", revision=1):
        git_init(root)
        write(root / MASTER_REL, MASTER_EXPANDED)
        state = current_state_dict("autoplan", chain_id=chain_id, revision=revision)
        write_summary(root, "autoplan", state)
        append_journal(root, "autoplan", journal_row(state))
        return state

    def test_v2_same_step_begin_seals_protocol_once(self, tmp_path, capsys):
        self._v2_epoch(tmp_path)
        journal_before = journal_path(tmp_path, "autoplan").read_bytes()
        sealed = autoplan_begin(
            capsys, tmp_path, step_id="f1", kind="framework", revision=1, plan=MASTER_REL, chain_id="v2-chain"
        )
        assert sealed.get("upgraded_from") == 2
        state = sealed["state"]
        assert state["schema_version"] == 3
        assert state["revision"] == 2
        assert state["pass"] == 1
        assert state["step"]["id"] == "f1"
        assert state["chain_id"] == "v2-chain"
        rows = journal_rows(tmp_path, "autoplan")
        assert len(rows) == 2
        assert rows[0]["state"]["schema_version"] == 2
        assert rows[1]["state"]["schema_version"] == 3
        assert journal_path(tmp_path, "autoplan").read_bytes().startswith(journal_before)
        again = autoplan_begin(
            capsys, tmp_path, step_id="f1", kind="framework", revision=1, plan=MASTER_REL, chain_id="v2-chain"
        )
        assert again["duplicate"] is True
        assert len(journal_rows(tmp_path, "autoplan")) == 2

    def test_v2_resume_promotes_to_schema3_once(self, tmp_path, capsys):
        self._v2_epoch(tmp_path)
        resumed = cli_ok(capsys, "resume", *mut_args("autoplan", tmp_path, OWNER_A, 1, "v2-chain"))
        assert resumed["state"]["schema_version"] == 3
        assert resumed["state"]["revision"] == 2
        assert resumed["state"]["pass"] == 1
        assert resumed.get("upgraded_from") == 2
        rows = journal_rows(tmp_path, "autoplan")
        assert len(rows) == 2
        assert rows[0]["state"]["schema_version"] == 2
        assert rows[1]["state"]["schema_version"] == 3

    def test_v2_row_after_v3_refused(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        append_journal(
            tmp_path,
            "autoplan",
            journal_row(current_state_dict("autoplan", chain_id="test-chain", revision=2)),
        )
        status = cli_ok(capsys, "status", "--chain", "autoplan", "--root", str(tmp_path), "--format", "json")
        assert status["diverged"]
        err = cli_err(capsys, "pause", *mut_args("autoplan", tmp_path, OWNER_A, 1), "--reason", "hold")
        assert "diverged" in err

    def test_closed_v2_marker_observed_and_idempotent_close(self, tmp_path, capsys):
        git_init(tmp_path)
        write(tmp_path / MASTER_REL, MASTER_EXPANDED)
        digest = "a" * 64
        marker = {
            "lifecycle": "closed",
            "chain": "autoplan",
            "archive": f".spec/autoplan/history/{digest}.json",
            "closed_at": "2026-10-20T00:00:00Z",
            "previous_digest": digest,
            "schema_version": 2,
            "execution_mode": "current",
            "chain_id": "v2-closed",
            "revision": 7,
        }
        write_summary(tmp_path, "autoplan", marker)
        payload = cli_ok(capsys, "status", "--chain", "autoplan", "--root", str(tmp_path), "--format", "json")
        assert payload["closed"] is True
        assert payload["chain_id"] == "v2-closed"
        assert payload["revision"] == 7
        assert payload["schema_version"] == 2
        again = cli_ok(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 0, "v2-closed"))
        assert again["status"] == "already_closed"
        err = cli_err(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 0, "wrong-id"))
        assert "Do not retarget" in err
        err = cli_err(capsys, *begin_args(tmp_path, OWNER_A, 0, "v2-closed"))
        assert "earlier generation" in err
        successor = autoplan_begin(
            capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL, chain_id="successor"
        )
        assert successor["state"]["schema_version"] == 3
        assert successor["state"]["chain_id"] == "successor"

    def test_unversioned_closed_marker_directs_to_legacy(self, tmp_path, capsys):
        git_init(tmp_path)
        digest = "b" * 64
        write_summary(
            tmp_path,
            "autoplan",
            {
                "lifecycle": "closed",
                "chain": "autoplan",
                "archive": f".spec/autoplan/history/{digest}.json",
                "closed_at": "2026-01-01T00:00:00Z",
                "previous_digest": digest,
            },
        )
        err = cli_err(capsys, "close", *mut_args("autoplan", tmp_path, OWNER_A, 0, "any-id"))
        assert "legacy" in err.lower()

    def test_summary_only_v2_takeover_promotes(self, tmp_path, capsys):
        git_init(tmp_path)
        write(tmp_path / MASTER_REL, MASTER_EXPANDED)
        write_summary(tmp_path, "autoplan", current_state_dict("autoplan", chain_id="v2-chain", revision=3))
        resumed = cli_ok(capsys, "resume", *mut_args("autoplan", tmp_path, OWNER_A, 3, "v2-chain"), "--takeover")
        assert resumed["state"]["schema_version"] == 3
        assert resumed["state"]["revision"] == 4
        assert resumed["state"]["chain_id"] == "v2-chain"
        assert resumed.get("upgraded_from") == 2

    def test_v2_reader_shape_rejects_v3_checkpoint(self, tmp_path, capsys):
        seed_autoplan(tmp_path)
        autoplan_begin(capsys, tmp_path, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        checkpoint = read_summary(tmp_path, "autoplan")
        assert checkpoint["schema_version"] == 3

        def v2_reader_kind(parsed):
            version = parsed.get("schema_version")
            if version is not None and (type(version) is not int or version != 2):
                return "unknown"
            if parsed.get("execution_mode") == "current":
                return "current"
            return "legacy"

        assert v2_reader_kind(checkpoint) == "unknown"
        assert v2_reader_kind(journal_rows(tmp_path, "autoplan")[-1]["state"]) == "unknown"


class TestGitEnvIsolation:
    @pytest.mark.parametrize("key", support.GIT_REPOSITORY_ENV)
    @pytest.mark.parametrize("value", ["nonempty", "empty"])
    def test_repository_env_override_refuses_before_writes(self, tmp_path, capsys, monkeypatch, key, value):
        source = tmp_path / "source"
        source.mkdir()
        seed_autoplan(source)
        other = tmp_path / "other"
        other.mkdir()
        git_init(other)
        setting = str(other / ".git") if key != "GIT_WORK_TREE" else str(other)
        monkeypatch.setenv(key, "" if value == "empty" else setting)
        before_source = tree_bytes(source)
        before_other = tree_bytes(other)
        err = cli_err(capsys, *begin_args(source, OWNER_A, 0, "env-chain"))
        assert key in err
        assert str(other) not in err
        assert tree_bytes(source) == before_source
        assert tree_bytes(other) == before_other
        assert not summary_path(source, "autoplan").exists()
        assert not journal_path(source, "autoplan").exists()
        assert not journal_path(other, "autoplan").exists()

    def test_combined_overrides_refuse_with_key_names_only(self, tmp_path, capsys, monkeypatch):
        source = tmp_path / "source"
        source.mkdir()
        seed_autoplan(source)
        other = tmp_path / "other"
        other.mkdir()
        git_init(other)
        monkeypatch.setenv("GIT_DIR", str(other / ".git"))
        monkeypatch.setenv("GIT_WORK_TREE", str(source))
        before_source = tree_bytes(source)
        before_other = tree_bytes(other)
        err = cli_err(capsys, *begin_args(source, OWNER_A, 0, "env-chain"))
        assert "GIT_DIR" in err
        assert "GIT_WORK_TREE" in err
        assert str(other) not in err
        assert str(source) not in err
        assert tree_bytes(source) == before_source
        assert tree_bytes(other) == before_other
        assert not journal_path(other, "autoplan").exists()

    def test_status_reads_root_state_despite_overrides(self, tmp_path, capsys, monkeypatch):
        source = tmp_path / "source"
        source.mkdir()
        seed_autoplan(source)
        autoplan_begin(capsys, source, step_id="f1", kind="framework", revision=0, plan=MASTER_REL)
        other = tmp_path / "other"
        other.mkdir()
        git_init(other)
        monkeypatch.setenv("GIT_DIR", str(other / ".git"))
        monkeypatch.setenv("GIT_WORK_TREE", str(other))
        payload = cli_ok(capsys, "status", "--chain", "autoplan", "--root", str(source), "--format", "json")
        assert payload["started"] is True
        assert payload["chain_id"] == "test-chain"
        assert not journal_path(other, "autoplan").exists()

    @requires_git
    def test_linked_worktree_probe_ignores_inherited_overrides(self, tmp_path, capsys, monkeypatch):
        main_repo = tmp_path / "main"
        main_repo.mkdir()
        git_init(main_repo)
        git_env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
        write(main_repo / "seed.txt", "seed\n")
        subprocess.run(["git", "-C", str(main_repo), "add", "seed.txt"], check=True)
        subprocess.run(["git", "-C", str(main_repo), "commit", "-qm", "seed"], check=True, env=git_env)
        worktree = tmp_path / "wt"
        subprocess.run(
            ["git", "-C", str(main_repo), "worktree", "add", str(worktree), "-b", "wt-branch"],
            check=True,
            capture_output=True,
        )
        write(worktree / MASTER_REL, MASTER_EXPANDED)
        other = tmp_path / "other"
        other.mkdir()
        git_init(other)
        monkeypatch.setenv("GIT_DIR", str(other / ".git"))
        monkeypatch.setenv("GIT_WORK_TREE", str(other))
        clean = {k: v for k, v in os.environ.items() if k not in support.GIT_REPOSITORY_ENV}
        probe = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "--absolute-git-dir"],
            check=True,
            capture_output=True,
            text=True,
            env=clean,
        )
        expected = Path(probe.stdout.strip()).resolve() / "spec-chain"
        assert support.chain_runtime_root(worktree) == expected
        before_other = tree_bytes(other)
        err = cli_err(capsys, *begin_args(worktree, OWNER_A, 0, "wt-chain"))
        assert "overrides" in err
        assert tree_bytes(other) == before_other
        assert not (expected / "autoplan" / support.CHAIN_SPAWNS_FILE).exists()
        assert not summary_path(worktree, "autoplan").exists()


class TestSummaryRepairAcknowledgement:
    def _reject_summary(self, monkeypatch):
        real = chain_session._atomic_write

        def reject(path, text):
            if Path(path).name == support.CHAIN_STATE_FILE:
                raise OSError("persistent summary failure")
            return real(path, text)

        monkeypatch.setattr(chain_session, "_atomic_write", reject)
        return real

    def test_persistent_begin_summary_failure_blocks_until_storage_recovers(self, tmp_path, capsys, monkeypatch):
        seed_autoplan(tmp_path)
        real = self._reject_summary(monkeypatch)
        argv = begin_args(tmp_path, OWNER_A, 0, "chain-one")
        first = cli_err(capsys, *argv)
        assert "summary write failed" in first or "committed" in first
        retry = cli_err(capsys, *argv)
        assert "summary repair" in retry
        assert len(journal_rows(tmp_path, "autoplan")) == 1
        assert not summary_path(tmp_path, "autoplan").exists()
        monkeypatch.setattr(chain_session, "_atomic_write", real)
        recovered = cli_ok(capsys, *argv)
        assert recovered["duplicate"] is True
        assert recovered["state"]["revision"] == 1
        assert len(journal_rows(tmp_path, "autoplan")) == 1
        assert read_summary(tmp_path, "autoplan")["revision"] == 1

    def test_persistent_close_summary_failure_blocks_until_storage_recovers(self, tmp_path, capsys, monkeypatch):
        autoplan_to_ready(capsys, tmp_path)
        real = self._reject_summary(monkeypatch)
        argv = ["close", *mut_args("autoplan", tmp_path, OWNER_A, 6, "test-chain")]
        first = cli_err(capsys, *argv)
        assert "summary" in first
        retry = cli_err(capsys, *argv)
        assert "summary repair" in retry
        rows = journal_rows(tmp_path, "autoplan")
        assert len(rows) == 7
        assert rows[-1]["state"]["lifecycle"] == "closed"
        assert read_summary(tmp_path, "autoplan")["lifecycle"] == "ready"
        monkeypatch.setattr(chain_session, "_atomic_write", real)
        recovered = cli_ok(capsys, *argv)
        assert recovered["status"] == "already_closed"
        assert recovered["revision"] == 7
        assert len(journal_rows(tmp_path, "autoplan")) == 7
        marker = read_summary(tmp_path, "autoplan")
        assert marker["lifecycle"] == "closed"
        assert marker["revision"] == 7
