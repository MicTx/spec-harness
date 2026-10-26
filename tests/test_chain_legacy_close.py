"""Legacy close proves the committed scope, not today's expanded plan."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import autoplan_spawn as autoplan
import autorun_spawn as autorun
import chain_lifecycle as lifecycle
import chain_spawn_support as support


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def commit(root, message):
    git(root, "add", ".spec")
    git(root, "commit", "-qm", message)
    return git(root, "rev-parse", "HEAD")


def seed(root, chain="autoplan"):
    git(root, "init", "-q")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.test")
    git(root, "config", "commit.gpgsign", "false")
    doc = root / ".spec/plan.md"
    doc.parent.mkdir()
    doc.write_text("# Old\n- [x] prior feature\n- [ ] final feature\n")
    state = {"host": "pi", "plan_docs": [".spec/plan.md"], "pass": 18, "round": 5}
    summary = support.chain_dir(root, chain)
    runtime = support.chain_runtime_dir(root, chain)
    support.write_chain_state(summary, runtime, state)
    support.append_audit(runtime / "spawns.jsonl", state)
    scope = commit(root, "record legacy scope")
    doc.write_text(doc.read_text().replace("[ ]", "[x]"))
    completion = commit(root, "complete legacy features")
    doc.write_text(doc.read_text() + "- [x] later independent feature\n")
    commit(root, "extend project plan after completion")
    return doc, summary, runtime, scope, completion


def close(root, chain, scope, completion, **kwargs):
    return lifecycle.close_chain(root, chain, legacy_scope_commit=scope, legacy_completion_commit=completion, **kwargs)


@pytest.mark.parametrize("chain,module", [("autoplan", autoplan), ("autorun", autorun)])
def test_cli_legacy_close_preserves_scope_and_original_state(tmp_path, chain, module, capsys):
    doc, summary, runtime, scope, completion = seed(tmp_path, chain)
    before = (summary / "chain.json").read_text()
    plan_before = doc.read_bytes()
    audit_before = (runtime / "spawns.jsonl").read_bytes()
    flags = [
        "close",
        "--root",
        str(tmp_path),
        "--legacy-scope-commit",
        scope,
        "--legacy-completion-commit",
        completion,
        "--format",
        "json",
    ]
    assert module.main(flags + ["--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["checked"] == 2
    assert (summary / "chain.json").read_text() == before
    assert not (summary / "history").exists()
    assert module.main(flags) == 0
    result = json.loads(capsys.readouterr().out)
    archive = json.loads((tmp_path / result["archive"]).read_text())
    assert archive["previous_state_text"] == before
    assert "feature_fingerprint" not in archive["previous_state"]
    proof = archive["proof"]
    assert proof["totals"]["checked"] == 2
    assert proof["legacy_evidence"]["scope_commit"] == scope
    assert proof["legacy_evidence"]["completion_commit"] == completion
    assert proof["legacy_evidence"]["current_plan"]["totals"]["checked"] == 3
    assert "- [ ] final feature" in proof["legacy_evidence"]["documents"][0]["scope_text"]
    assert doc.read_bytes() == plan_before
    assert (runtime / "spawns.jsonl").read_bytes().startswith(audit_before)
    assert support.read_state(summary, runtime, chain) == (None, "missing")
    assert close(tmp_path, chain, scope, completion)["status"] == "already_closed"


@pytest.mark.parametrize(
    "damage",
    [
        "wrong-scope",
        "wrong-completion",
        "missing-half",
        "edited-state",
        "edited-plan",
        "new-scope",
        "unfinished",
        "no-features",
        "symlink",
        "outside",
        "active",
        "audit",
        "modern",
        "invalid-ref",
        "nonancestor",
        "reopened",
        "permission-audit",
        "staged",
    ],
)
def test_legacy_evidence_rejects_ambiguity_without_writes(tmp_path, damage):
    doc, summary, runtime, scope, completion = seed(tmp_path)
    state_path = summary / "chain.json"
    state = json.loads(state_path.read_text())
    if damage == "wrong-scope":
        scope = completion
    elif damage == "wrong-completion":
        completion = scope
    elif damage == "missing-half":
        completion = None
    elif damage == "edited-state":
        state["pass"] = 19
        state_path.write_text(json.dumps(state))
    elif damage == "edited-plan":
        doc.write_text(doc.read_text() + "uncommitted prose\n")
    elif damage in ("new-scope", "unfinished", "no-features", "symlink", "outside", "modern"):
        if damage == "new-scope":
            doc.write_text("- [x] substituted feature\n")
        elif damage == "unfinished":
            doc.write_text("- [ ] final feature\n")
        elif damage == "no-features":
            doc.write_text("# Empty\n")
        elif damage == "symlink":
            doc.unlink()
            doc.symlink_to("other.md")
        elif damage == "outside":
            state["plan_docs"] = ["../outside.md"]
            state_path.write_text(json.dumps(state))
        else:
            state["feature_fingerprint"] = support.feature_fingerprint([doc], tmp_path)
            state_path.write_text(json.dumps(state))
        completion = commit(tmp_path, "invalid completion")
    elif damage == "active":
        (tmp_path / ".spec/specs/unfinished").mkdir(parents=True)
    elif damage == "audit":
        support.append_audit(runtime / "spawns.jsonl", {**state, "pass": 19})
    elif damage == "permission-audit":
        support.append_audit(runtime / "spawns.jsonl", {**state, "permission_effective": "bypass"})
    elif damage == "staged":
        before_doc = doc.read_bytes()
        doc.write_text("- [x] staged replacement\n")
        git(tmp_path, "add", ".spec/plan.md")
        doc.write_bytes(before_doc)
    elif damage == "invalid-ref":
        completion = "HEAD:../../invalid"
    elif damage == "nonancestor":
        completion = git(tmp_path, "commit-tree", f"{scope}^{{tree}}", "-m", "unrelated")
    elif damage == "reopened":
        doc.write_text(doc.read_text().replace("[x] final", "[ ] final"))
        commit(tmp_path, "reopen")
    before = state_path.read_bytes()
    audit_before = (runtime / "spawns.jsonl").read_bytes()
    with pytest.raises(support.ChainSpawnError):
        close(tmp_path, "autoplan", scope, completion)
    assert state_path.read_bytes() == before
    assert (runtime / "spawns.jsonl").read_bytes() == audit_before
    assert not (summary / "history").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PATH attack; shared Git resolver owns Windows filtering")
def test_fake_git_on_caller_path_is_never_executed(tmp_path, monkeypatch):
    _, _, _, scope, completion = seed(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text("#!/bin/sh\necho forged-git-evidence\nexit 0\n")
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ.get("PATH", ""))
    assert close(tmp_path, "autoplan", scope, completion, dry_run=True)["checked"] == 2


def test_checkout_eol_conversion_fails_with_precise_reason(tmp_path):
    doc, _, _, scope, completion = seed(tmp_path)
    (tmp_path / ".gitattributes").write_text(".spec/plan.md text eol=crlf\n")
    git(tmp_path, "add", ".gitattributes")
    git(tmp_path, "commit", "-qm", "checkout EOL policy")
    doc.unlink()
    git(tmp_path, "checkout-index", "--", ".spec/plan.md")
    assert b"\r\n" in doc.read_bytes()
    assert git(tmp_path, "diff", "--", ".spec/plan.md") == ""
    with pytest.raises(support.ChainSpawnError, match="checkout EOL/filter conversions are unsupported"):
        close(tmp_path, "autoplan", scope, completion)


def test_legacy_close_honors_mutex(tmp_path):
    _, _, runtime, scope, completion = seed(tmp_path)
    with support.single_chain_lock(runtime, "autoplan"):
        with pytest.raises(support.ChainSpawnError):
            close(tmp_path, "autoplan", scope, completion)


def test_legacy_audit_failure_is_retryable(tmp_path, monkeypatch):
    _, summary, _, scope, completion = seed(tmp_path)
    before = (summary / "chain.json").read_bytes()
    with monkeypatch.context() as patch:

        def fail(*args):
            raise OSError("disk full")

        patch.setattr(lifecycle, "_append_closure", fail)
        with pytest.raises(support.ChainSpawnError, match="disk full"):
            close(tmp_path, "autoplan", scope, completion)
    assert (summary / "chain.json").read_bytes() == before
    assert close(tmp_path, "autoplan", scope, completion)["status"] == "closed"


def test_legacy_summary_failure_recovers_closure(tmp_path, monkeypatch):
    _, summary, runtime, scope, completion = seed(tmp_path)
    before = (summary / "chain.json").read_bytes()
    write = support.atomic_write
    with monkeypatch.context() as patch:

        def fail_summary(path, content):
            if path.name == "chain.json":
                raise OSError("summary failure")
            write(path, content)

        patch.setattr(support, "atomic_write", fail_summary)
        with pytest.raises(support.ChainSpawnError, match="summary failure"):
            close(tmp_path, "autoplan", scope, completion)
    assert (summary / "chain.json").read_bytes() == before
    assert close(tmp_path, "autoplan", scope, completion)["status"] == "closed"
    (summary / "chain.json").write_text("{partial")
    assert support.read_state(summary, runtime, "autoplan")[0] is None
    assert json.loads((summary / "chain.json").read_text())["lifecycle"] == "closed"


def test_existing_archive_tamper_refuses_retry(tmp_path, monkeypatch):
    _, summary, _, scope, completion = seed(tmp_path)
    with monkeypatch.context() as patch:

        def fail(*args):
            raise OSError("disk full")

        patch.setattr(lifecycle, "_append_closure", fail)
        with pytest.raises(support.ChainSpawnError):
            close(tmp_path, "autoplan", scope, completion)
    archive = next((summary / "history").glob("*.json"))
    evidence = json.loads(archive.read_text())
    evidence["proof"]["totals"]["checked"] = 999
    archive.write_text(json.dumps(evidence))
    with pytest.raises(support.ChainSpawnError, match="collision"):
        close(tmp_path, "autoplan", scope, completion)


@pytest.mark.parametrize("damage", ["missing", "duplicate", "invalid-utf8", "state-identity", "option"])
def test_historical_objects_must_prove_the_recorded_scope(tmp_path, damage):
    doc, summary, runtime, _, _ = seed(tmp_path)
    if damage == "missing":
        doc.unlink()
    elif damage == "duplicate":
        doc.write_text("- [x] repeated\n- [x] repeated\n")
    elif damage == "invalid-utf8":
        doc.write_bytes(b"\xff")
    state = json.loads((summary / "chain.json").read_text())
    state["pass"] = 19
    support.write_chain_state(summary, runtime, state)
    support.append_audit(runtime / "spawns.jsonl", state)
    scope = commit(tmp_path, "record broken scope")
    doc.write_text("- [x] replacement\n")
    completion = commit(tmp_path, "claim completion")
    if damage == "state-identity":
        state["permission_mode"] = "bypass"
        support.write_chain_state(summary, runtime, state)
    elif damage == "option":
        scope = "--help"
    before = (summary / "chain.json").read_bytes()
    with pytest.raises(support.ChainSpawnError):
        close(tmp_path, "autoplan", scope, completion)
    assert (summary / "chain.json").read_bytes() == before
    assert not (summary / "history").exists()


# --- independent successor package: closing a completed chain while a later,
# unrelated package is already active (Git-anchored, never parameter-trusted) ---

SUCCESSOR = "2026-10-23_add-next"


def seed_current(root, chain="autorun", checked=True, package_first=None):
    git(root, "init", "-q")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.test")
    git(root, "config", "commit.gpgsign", "false")
    doc = root / ".spec/plans/old/master.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Old\n- [{}] original feature\n".format("x" if checked else " "))
    if package_first:
        add_package(root, package_first)
    state = {
        "plan_docs": [str(doc.relative_to(root))],
        "feature_fingerprint": support.feature_fingerprint([doc], root),
        "host": "pi",
        "model": {"id": "old-provider/old-model"},
        "permission_mode": "ask",
        "permission_effective": "ask",
        "kind": "review",
        "pass": 20,
        "round": 5,
        "max_passes": 20,
        "max_rounds": 5,
    }
    summary = support.chain_dir(root, chain)
    runtime = support.chain_runtime_dir(root, chain)
    support.write_chain_state(summary, runtime, state)
    support.append_audit(runtime / "spawns.jsonl", state)
    commit(root, "record chain state")
    return doc, summary, runtime


def add_package(root, slug=SUCCESSOR, committed=True):
    package = root / ".spec/specs" / slug
    package.mkdir(parents=True, exist_ok=True)
    (package / "spec.md").write_text("# successor\n")
    (package / "tasks.md").write_text("# tasks\n")
    return commit(root, "add package " + slug) if committed else None


def snapshot(summary, runtime):
    return (
        (summary / "chain.json").read_bytes(),
        (runtime / "spawns.jsonl").read_bytes(),
        (summary / "history").exists(),
    )


@pytest.mark.parametrize("chain", ["autoplan", "autorun"])
def test_independent_successor_package_allows_proven_close(tmp_path, chain):
    _, summary, runtime = seed_current(tmp_path, chain)
    add_package(tmp_path)
    before = (summary / "chain.json").read_bytes()
    audit_before = (runtime / "spawns.jsonl").read_bytes()
    package_before = (tmp_path / ".spec/specs" / SUCCESSOR / "spec.md").read_bytes()
    result = lifecycle.close_chain(tmp_path, chain, independent_package=SUCCESSOR)
    assert result["status"] == "closed"
    archive = json.loads((tmp_path / result["archive"]).read_text())
    assert archive["previous_state_text"].encode() == before
    assert archive["proof"]["independent_package"]["slug"] == SUCCESSOR
    assert (runtime / "spawns.jsonl").read_bytes().startswith(audit_before)
    assert support.read_state(summary, runtime, chain) == (None, "missing")
    assert (tmp_path / ".spec/specs" / SUCCESSOR / "spec.md").read_bytes() == package_before
    assert lifecycle.close_chain(tmp_path, chain)["status"] == "already_closed"


def test_independent_successor_dry_run_writes_nothing(tmp_path):
    _, summary, runtime = seed_current(tmp_path)
    add_package(tmp_path)
    before = snapshot(summary, runtime)
    result = lifecycle.close_chain(tmp_path, "autorun", dry_run=True, independent_package=SUCCESSOR)
    assert result["status"] == "would_close"
    assert snapshot(summary, runtime) == before


def test_active_package_still_blocks_close_without_the_proof(tmp_path):
    _, summary, runtime = seed_current(tmp_path)
    add_package(tmp_path)
    before = snapshot(summary, runtime)
    with pytest.raises(support.ChainSpawnError, match="active package"):
        lifecycle.close_chain(tmp_path, "autorun")
    assert snapshot(summary, runtime) == before


@pytest.mark.parametrize(
    "case",
    [
        "package-predates-final-state",
        "package-never-committed",
        "named-package-not-active",
        "second-active-package",
        "state-edited-after-commit",
        "invalid-slug",
        "path-slug",
        "archive-slug",
    ],
)
def test_independent_successor_refuses_unproven_independence(tmp_path, case):
    # An old, unfinished package renamed as "successor" must never open the gate,
    # whatever its plan checkboxes say: only Git ancestry after the final state counts.
    slug = SUCCESSOR
    if case == "package-predates-final-state":
        _, summary, runtime = seed_current(tmp_path, package_first=SUCCESSOR)
    else:
        _, summary, runtime = seed_current(tmp_path)
        if case == "package-never-committed":
            add_package(tmp_path, committed=False)
        elif case == "named-package-not-active":
            add_package(tmp_path, "2026-10-22_add-other")
        elif case == "second-active-package":
            add_package(tmp_path)
            add_package(tmp_path, "2026-10-22_add-other")
        elif case == "state-edited-after-commit":
            add_package(tmp_path)
            (summary / "chain.json").write_text((summary / "chain.json").read_text() + "\n")
        elif case == "invalid-slug":
            add_package(tmp_path)
            slug = "Not A Slug"
        elif case == "path-slug":
            add_package(tmp_path)
            slug = "../" + SUCCESSOR
        else:
            add_package(tmp_path)
            slug = "archive"
    before = snapshot(summary, runtime)
    with pytest.raises(support.ChainSpawnError):
        lifecycle.close_chain(tmp_path, "autorun", independent_package=slug)
    assert snapshot(summary, runtime) == before


def test_successor_added_before_plan_completion_may_be_old_work(tmp_path):
    # The package appears after the final chain state but before the recorded plan
    # was completed: it could be the chain's own last package, so it never counts.
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Fixture")
    git(tmp_path, "config", "user.email", "fixture@example.test")
    git(tmp_path, "config", "commit.gpgsign", "false")
    doc = tmp_path / ".spec/plans/old/master.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Old\n- [ ] original feature\n")
    state = {
        "plan_docs": [str(doc.relative_to(tmp_path))],
        "feature_fingerprint": support.feature_fingerprint([doc], tmp_path),
        "host": "pi",
        "model": None,
        "permission_mode": "ask",
        "permission_effective": "ask",
        "round": 5,
        "max_rounds": 5,
    }
    summary = support.chain_dir(tmp_path, "autorun")
    runtime = support.chain_runtime_dir(tmp_path, "autorun")
    support.write_chain_state(summary, runtime, state)
    support.append_audit(runtime / "spawns.jsonl", state)
    commit(tmp_path, "record chain state")
    add_package(tmp_path)
    doc.write_text(doc.read_text().replace("[ ]", "[x]"))
    commit(tmp_path, "complete plan after the package appeared")
    before = snapshot(summary, runtime)
    with pytest.raises(support.ChainSpawnError):
        lifecycle.close_chain(tmp_path, "autorun", independent_package=SUCCESSOR)
    assert snapshot(summary, runtime) == before


def test_independent_successor_cannot_close_unfinished_scope(tmp_path):
    _, summary, runtime = seed_current(tmp_path, checked=False)
    add_package(tmp_path)
    before = snapshot(summary, runtime)
    with pytest.raises(support.ChainSpawnError, match="unfinished"):
        lifecycle.close_chain(tmp_path, "autorun", independent_package=SUCCESSOR)
    assert snapshot(summary, runtime) == before


@pytest.mark.parametrize("chain,module", [("autoplan", autoplan), ("autorun", autorun)])
def test_cli_close_accepts_independent_package_flag(tmp_path, chain, module, capsys):
    _, summary, _ = seed_current(tmp_path, chain)
    add_package(tmp_path)
    flags = ["close", "--root", str(tmp_path), "--independent-package", SUCCESSOR, "--format", "json"]
    assert module.main(flags + ["--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "would_close"
    assert not (summary / "history").exists()
    assert module.main(flags) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "closed"


@pytest.mark.parametrize("move", ["rename", "copy"])
def test_the_chains_own_package_moved_or_copied_later_is_not_independent(tmp_path, move):
    doc, summary, runtime = seed_current(tmp_path, package_first=None)
    own = "2026-10-01_add-own"
    package = tmp_path / ".spec/specs" / own
    package.mkdir(parents=True)
    (package / "spec.md").write_text("# the chain's own package\n" + "body line\n" * 40)
    (package / "tasks.md").write_text("# tasks\n")
    # the package belongs to the chain: it exists BEFORE the final committed chain state
    state = json.loads((summary / "chain.json").read_text())
    support.write_chain_state(summary, runtime, state)
    support.append_audit(runtime / "spawns.jsonl", state)
    commit(tmp_path, "chain state with its own package")
    successor = "2026-10-23_add-moved"
    target = tmp_path / ".spec/specs" / successor
    if move == "rename":
        git(tmp_path, "mv", f".spec/specs/{own}", f".spec/specs/{successor}")
    else:
        target.mkdir(parents=True)
        (target / "spec.md").write_bytes((package / "spec.md").read_bytes())
        (target / "tasks.md").write_bytes((package / "tasks.md").read_bytes())
        git(tmp_path, "rm", "-rq", f".spec/specs/{own}")
    commit(tmp_path, "move the chain's own package under a new slug")
    before = snapshot(summary, runtime)
    with pytest.raises(support.ChainSpawnError):
        lifecycle.close_chain(tmp_path, "autorun", independent_package=successor)
    assert snapshot(summary, runtime) == before


def test_a_genuinely_new_package_is_still_independent_after_the_rename_check(tmp_path):
    _, summary, runtime = seed_current(tmp_path)
    add_package(tmp_path)
    assert (
        lifecycle.close_chain(tmp_path, "autorun", independent_package=SUCCESSOR, dry_run=True)["status"]
        == "would_close"
    )


def test_the_residual_limit_of_the_independence_proof_is_documented():
    source = (Path(__file__).resolve().parent.parent / "scripts" / "chain_lifecycle.py").read_text(encoding="utf-8")
    doc = source[source.index("def independent_successor_proof") :]
    doc = doc[: doc.index('"""', doc.index('"""') + 3)]
    assert "CANNOT show" in doc and "wholly rewritten" in doc
    assert "operator assertion" in doc
