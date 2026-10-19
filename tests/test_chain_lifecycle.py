"""Completed-chain closeout, scope isolation, and crash/restart boundaries."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import autoplan_spawn as autoplan
import autorun_spawn as autorun
import chain_lifecycle as lifecycle
import chain_spawn_support as support
from chain_recovery import decide


def seed(root, chain="autoplan", checked=True):
    doc = root / ".spec/plans/old/master.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Old\n- [{}] original feature\n".format("x" if checked else " "))
    state = {
        "plan_docs": [str(doc.relative_to(root))],
        "feature_fingerprint": support.feature_fingerprint([doc], root),
        "host": "pi", "model": {"id": "old-provider/old-model"},
        "permission_mode": "ask", "permission_effective": "ask",
        "kind": "review", "pass": 20, "round": 5,
        "max_passes": 20, "max_rounds": 5,
    }
    summary = support.chain_dir(root, chain)
    runtime = support.chain_runtime_dir(root, chain)
    support.write_chain_state(summary, runtime, state)
    support.append_audit(runtime / "spawns.jsonl", state)
    new_doc = root / ".spec/plans/new/master.md"
    new_doc.parent.mkdir(parents=True)
    new_doc.write_text("# New\n- [ ] unrelated new feature\n")
    return doc, state, summary, runtime, new_doc


@pytest.mark.parametrize("chain,module", [("autoplan", autoplan), ("autorun", autorun)])
def test_status_is_scoped_and_completed_at_cap(tmp_path, chain, module):
    seed(tmp_path, chain)
    status = module.status_payload(tmp_path)
    assert status["resume"]["action"] == "chain_complete"
    assert status["resume"]["detail"]["checked"] == 1
    if chain == "autorun":
        assert status["plan"]["totals"] == {"checked": 1, "unchecked": 0, "total": 1}


@pytest.mark.parametrize("chain,module", [("autoplan", autoplan), ("autorun", autorun)])
@pytest.mark.parametrize("damage", ["missing", "empty", "changed", "invalid-utf8", "outside", "duplicate"])
def test_status_and_close_refuse_unproven_completion(tmp_path, chain, module, damage):
    doc, state, summary, runtime, _ = seed(tmp_path, chain)
    if damage == "missing":
        doc.unlink()
    elif damage == "empty":
        doc.write_text("# No features\n")
    elif damage == "changed":
        doc.write_text("- [x] changed identity\n")
    elif damage == "invalid-utf8":
        doc.write_bytes(b"\xff")
    else:
        state["plan_docs"] = ["../outside.md"] if damage == "outside" else state["plan_docs"] * 2
        support.write_chain_state(summary, runtime, state)
    before = (summary / "chain.json").read_bytes()
    assert module.status_payload(tmp_path)["resume"]["action"] == "state_diverged"
    with pytest.raises(support.ChainSpawnError):
        lifecycle.close_chain(tmp_path, chain)
    assert (summary / "chain.json").read_bytes() == before
    assert not (summary / "history").exists()


@pytest.mark.parametrize("chain", ["autoplan", "autorun"])
def test_close_preserves_history_and_is_idempotent(tmp_path, chain):
    _, state, summary, runtime, _ = seed(tmp_path, chain)
    before = (summary / "chain.json").read_bytes()
    audit_before = (runtime / "spawns.jsonl").read_bytes()
    result = lifecycle.close_chain(tmp_path, chain)
    assert result["status"] == "closed"
    snapshot = json.loads((tmp_path / result["archive"]).read_text())
    assert snapshot["previous_state"] == state
    assert snapshot["previous_state_text"].encode() == before
    assert snapshot["proof"]["totals"]["checked"] == 1
    assert (runtime / "spawns.jsonl").read_bytes().startswith(audit_before)
    assert support.read_state(summary, runtime, chain) == (None, "missing")
    assert lifecycle.close_chain(tmp_path, chain)["status"] == "already_closed"
    assert len(list((summary / "history").iterdir())) == 1


@pytest.mark.parametrize("chain,module", [("autoplan", autoplan), ("autorun", autorun)])
def test_cli_close_then_new_chain_dry_run_resets_identity_and_counter(tmp_path, chain, module, capsys, monkeypatch):
    _, _, summary, _, new_doc = seed(tmp_path, chain)
    monkeypatch.setattr(support.platform, "system", lambda: "Darwin")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    assert module.main(["close", "--root", str(tmp_path), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "closed"
    marker = (summary / "chain.json").read_bytes()
    args = ["spawn", "--root", str(tmp_path), "--plan", str(new_doc), "--host", "pi", "--dry-run", "--format", "json"]
    if chain == "autoplan":
        args += ["--next", "framework"]
    else:
        package = tmp_path / ".spec/specs/new/spec.md"
        package.parent.mkdir(parents=True)
        package.write_text("# New package\n")
    assert module.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["pass" if chain == "autoplan" else "round"] == 1
    assert result["model"] is None
    assert (summary / "chain.json").read_bytes() == marker


def test_corrupt_closed_summary_recovers_closure_not_old_chain(tmp_path):
    _, _, summary, runtime, _ = seed(tmp_path)
    lifecycle.close_chain(tmp_path, "autoplan")
    (summary / "chain.json").write_text("{partial")
    assert support.read_state(summary, runtime, "autoplan")[0] is None
    assert json.loads((summary / "chain.json").read_text())["lifecycle"] == "closed"
    assert autoplan._read_chain_state(tmp_path) is None


def test_new_spawn_audit_recovery_is_not_hidden_by_old_closure(tmp_path):
    _, state, summary, runtime, _ = seed(tmp_path)
    lifecycle.close_chain(tmp_path, "autoplan")
    state = {**state, "pass": 1, "model": None, "feature_fingerprint": "new"}
    support.append_audit(runtime / "spawns.jsonl", state)
    (summary / "chain.json").write_text("{partial")
    recovered, status = support.read_state(summary, runtime, "autoplan")
    assert status == "recovered" and recovered["pass"] == 1
    assert recovered.get("lifecycle") != "closed"


@pytest.mark.parametrize("issue", ["unfinished", "active", "corrupt", "no-scope", "no-fingerprint", "audit-diverged"])
def test_close_rejects_incomplete_or_ambiguous_state(tmp_path, issue):
    _, state, summary, runtime, _ = seed(tmp_path, checked=issue != "unfinished")
    if issue == "active":
        package = tmp_path / ".spec/specs/active/spec.md"
        package.parent.mkdir(parents=True)
        package.write_text("active")
    elif issue == "corrupt":
        (summary / "chain.json").write_text("{partial")
    elif issue in ("no-scope", "no-fingerprint"):
        state.pop("plan_docs" if issue == "no-scope" else "feature_fingerprint")
        support.write_chain_state(summary, runtime, state)
    elif issue == "audit-diverged":
        support.append_audit(runtime / "spawns.jsonl", {**state, "pass": 21})
    before = (summary / "chain.json").read_bytes()
    with pytest.raises(support.ChainSpawnError):
        lifecycle.close_chain(tmp_path, "autoplan")
    assert (summary / "chain.json").read_bytes() == before


def test_close_is_mutex_protected(tmp_path):
    _, _, _, runtime, _ = seed(tmp_path)
    with support.single_chain_lock(runtime, "autoplan"):
        with pytest.raises(support.ChainSpawnError, match="holds|parallel"):
            lifecycle.close_chain(tmp_path, "autoplan")


@pytest.mark.parametrize("path", [
    ".spec/autoplan/history", ".spec/autoplan/chain.json", ".git/spec-chain/autoplan/spawns.jsonl",
])
def test_symlinked_state_or_archive_refused(tmp_path, path):
    seed(tmp_path)
    target = tmp_path / path
    external = tmp_path.parent / (tmp_path.name + "-outside")
    if target.exists():
        external.write_bytes(target.read_bytes())
        target.unlink()
    else:
        external.mkdir()
    target.symlink_to(external)
    with pytest.raises(support.ChainSpawnError, match="symlink|root"):
        lifecycle.close_chain(tmp_path, "autoplan")


def test_dry_run_does_not_close_or_archive(tmp_path):
    _, _, summary, runtime, _ = seed(tmp_path)
    before = (summary / "chain.json").read_bytes()
    log = (runtime / "spawns.jsonl").read_bytes()
    assert lifecycle.close_chain(tmp_path, "autoplan", dry_run=True)["status"] == "would_close"
    assert (summary / "chain.json").read_bytes() == before
    assert (runtime / "spawns.jsonl").read_bytes() == log
    assert not (summary / "history").exists()


def test_audit_failure_keeps_active_state_retryable(tmp_path, monkeypatch):
    _, _, summary, _, _ = seed(tmp_path)
    before = (summary / "chain.json").read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(lifecycle, "_append_closure", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(support.ChainSpawnError, match="disk full"):
            lifecycle.close_chain(tmp_path, "autoplan")
    assert (summary / "chain.json").read_bytes() == before
    assert lifecycle.close_chain(tmp_path, "autoplan")["status"] == "closed"
    assert len(list((summary / "history").iterdir())) == 1


def test_summary_failure_after_audit_is_retryable(tmp_path, monkeypatch):
    _, _, summary, _, _ = seed(tmp_path)
    before = (summary / "chain.json").read_bytes()
    write = support.atomic_write
    def fail_summary(path, content):
        if path.name == "chain.json":
            raise OSError("summary failure")
        write(path, content)
    with monkeypatch.context() as patch:
        patch.setattr(support, "atomic_write", fail_summary)
        with pytest.raises(support.ChainSpawnError, match="summary failure"):
            lifecycle.close_chain(tmp_path, "autoplan")
    assert (summary / "chain.json").read_bytes() == before
    assert lifecycle.close_chain(tmp_path, "autoplan")["status"] == "closed"


def test_cap_is_not_raised_for_unfinished_work():
    assert decide("autorun", {"round": 5, "max_rounds": 5}, "ok", plan_unchecked=1).action == "cap_reached"


@pytest.mark.parametrize("chain", ["autoplan", "autorun"])
def test_close_missing_chain_is_noop(tmp_path, chain):
    assert lifecycle.close_chain(tmp_path, chain)["status"] == "not_started"


def test_bare_closed_flag_cannot_reset_chain(tmp_path):
    summary = tmp_path / ".spec/autoplan"
    summary.mkdir(parents=True)
    (summary / "chain.json").write_text('{"lifecycle": "closed"}')
    with pytest.raises(support.ChainSpawnError, match="invalid closed"):
        autoplan._read_chain_state(tmp_path)
    assert support.read_state(summary, tmp_path / "runtime", "autoplan") == (None, "corrupt")
    with pytest.raises(support.ChainSpawnError, match="invalid closed"):
        lifecycle.close_chain(tmp_path, "autoplan")


def test_unreadable_audit_cannot_authorize_close(tmp_path):
    _, _, summary, runtime, _ = seed(tmp_path)
    (runtime / "spawns.jsonl").write_text("{broken")
    before = (summary / "chain.json").read_bytes()
    with pytest.raises(support.ChainSpawnError, match="audit unreadable"):
        lifecycle.close_chain(tmp_path, "autoplan")
    assert (summary / "chain.json").read_bytes() == before


def test_legacy_status_remains_readable_but_close_needs_fingerprint(tmp_path):
    _, state, summary, runtime, _ = seed(tmp_path)
    state.pop("feature_fingerprint")
    support.write_chain_state(summary, runtime, state)
    assert autoplan.status_payload(tmp_path)["resume"]["action"] == "chain_complete"
    with pytest.raises(support.ChainSpawnError, match="feature_fingerprint"):
        lifecycle.close_chain(tmp_path, "autoplan")


def test_active_package_prevents_false_completion():
    result = decide("autorun", {"round": 1}, "ok", plan_checked=1, active_packages=[{"slug": "active"}])
    assert result.action == "continue_package"
