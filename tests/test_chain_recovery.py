"""Tests for scripts/chain_recovery.py (plans/01 F4: recovery semantics).

Every decision-table row has at least one named positive test, with the
plans/01 row number in the test name; the read_state matrix covers the four
read statuses, the audit rebuild, and the never-rewrite invariant.
"""

import contextlib
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from chain_recovery import (  # noqa: E402  # type: ignore
    ACTIONS,
    append_recovery_event,
    decide,
    detail_doc_complete,
    read_audit_tail,
    read_state,
    recovery_event_recorded,
)


@contextlib.contextmanager
def _tmp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


def write_chain_state(chain_dir: Path, state=None, raw: str = None):
    chain_dir.mkdir(parents=True, exist_ok=True)
    target = chain_dir / "chain.json"
    if raw is not None:
        target.write_text(raw, encoding="utf-8")
    else:
        target.write_text(json.dumps(state) + "\n", encoding="utf-8")
    return target


def write_spawns(chain_dir: Path, *records, partial_tail: str = None, terminate: bool = True):
    chain_dir.mkdir(parents=True, exist_ok=True)
    target = chain_dir / "spawns.jsonl"
    body = "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)
    if partial_tail is not None:
        body += partial_tail + ("\n" if terminate else "")
    target.write_text(body, encoding="utf-8")
    return target


def autorun_state(round_index=3, max_rounds=20, **extra):
    return {"round": round_index, "max_rounds": max_rounds, "host": "pi", **extra}


def autoplan_state(pass_index=3, kind="detail", target="plans/01-a.md", **extra):
    return {"pass": pass_index, "max_passes": 12, "kind": kind, "target": target, **extra}


LINKS = ["plans/00-master.md", "plans/01-a.md", "plans/02-b.md", "plans/README.md"]
MASTER = "plans/00-master.md"


def docs(*incomplete):
    return [{"path": path, "exists": True, "complete": path not in incomplete} for path in LINKS]


class TestReadState:
    """read_state: ok / missing / recovered / corrupt + never-rewrite."""

    def test_ok_parses_and_fills_old_format_defaults(self, tmp_path):
        write_chain_state(tmp_path, {"round": 4, "max_rounds": 20})
        state, status = read_state(tmp_path, "autorun")
        assert status == "ok"
        assert state["round"] == 4
        # old-format defaults: window_recycle {} / host "unknown", in memory only
        assert state["window_recycle"] == {}
        assert state["host"] == "unknown"
        assert "recovered_from" not in state

    def test_ok_does_not_rewrite_old_records(self, tmp_path):
        raw = '{"round": 4, "max_rounds": 20}'
        write_chain_state(tmp_path, raw=raw)
        before = (tmp_path / "chain.json").read_bytes()
        read_state(tmp_path, "autorun")
        assert (tmp_path / "chain.json").read_bytes() == before
        assert not (tmp_path / "events.jsonl").exists()

    def test_missing_is_fresh_even_with_audit_lines(self, tmp_path):
        # spawn writes chain.json before appending spawns.jsonl, so a missing
        # state file means the chain never started (plans/01 assumption)
        write_spawns(tmp_path, autorun_state(round_index=2))
        state, status = read_state(tmp_path, "autorun")
        assert status == "missing"
        assert state is None

    def test_non_object_state_triggers_rebuild(self, tmp_path):
        write_chain_state(tmp_path, raw="[1, 2]")
        write_spawns(tmp_path, autorun_state(round_index=5))
        state, status = read_state(tmp_path, "autorun")
        assert status == "recovered"
        assert state["round"] == 5

    def test_recovered_rebuilds_from_last_parseable_tail_line(self, tmp_path):
        write_chain_state(tmp_path, raw="{half-written state")
        write_spawns(
            tmp_path,
            autorun_state(round_index=4),
            autorun_state(round_index=5),
            partial_tail='{"round": 6, "max_',
        )
        state, status = read_state(tmp_path, "autorun")
        assert status == "recovered"
        assert state["round"] == 5
        assert state["recovered_from"] == "spawns.jsonl line 2"
        # the write-back lands atomically: valid JSON, no tmp residue
        assert not list(tmp_path.glob(".chain.json.tmp"))
        rebuilt = json.loads((tmp_path / "chain.json").read_text(encoding="utf-8"))
        assert rebuilt["round"] == 5
        assert rebuilt["recovered_from"] == "spawns.jsonl line 2"
        # ... and the recovery is audited
        events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        assert events[-1]["kind"] == "recovery"
        assert events[-1]["chain"] == "autorun"
        assert events[-1]["action"] == "recovered_state"
        assert events[-1]["at"]

    def test_corrupt_leaves_files_untouched_when_audit_cannot_rebuild(self, tmp_path):
        write_chain_state(tmp_path, raw="{bad")
        state, status = read_state(tmp_path, "autorun")
        assert status == "corrupt"
        assert state is None
        assert (tmp_path / "chain.json").read_text(encoding="utf-8") == "{bad"
        assert not (tmp_path / "events.jsonl").exists()

    def test_corrupt_when_audit_has_only_partial_lines(self, tmp_path):
        write_chain_state(tmp_path, raw="{bad")
        write_spawns(tmp_path, partial_tail='{"round": 3, "max')
        state, status = read_state(tmp_path, "autorun")
        assert status == "corrupt"
        assert state is None

    def test_recovery_event_never_glues_onto_unterminated_tail(self, tmp_path):
        events = tmp_path / "events.jsonl"
        events.parent.mkdir(parents=True, exist_ok=True)
        events.write_text(json.dumps({"kind": "recycle_close", "result": "closed"}), encoding="utf-8")
        append_recovery_event(tmp_path, "autoplan", "state_diverged", {"target": "plans/09-x.md"})
        lines = events.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["kind"] == "recycle_close"
        assert json.loads(lines[1])["kind"] == "recovery"


class TestAuditTail:
    def test_returns_last_complete_record(self, tmp_path):
        write_spawns(tmp_path, autorun_state(round_index=4), autorun_state(round_index=5))
        assert read_audit_tail(tmp_path) == autorun_state(round_index=5)

    def test_skips_partial_tail_lines(self, tmp_path):
        write_spawns(tmp_path, autorun_state(round_index=4), partial_tail='{"round": 5')
        assert read_audit_tail(tmp_path)["round"] == 4

    def test_missing_or_unreadable_audit_is_none(self, tmp_path):
        assert read_audit_tail(tmp_path) is None


class TestRecoveryEventRecorded:
    def test_matches_action_and_target(self, tmp_path):
        events = tmp_path / "events.jsonl"
        append_recovery_event(tmp_path, "autoplan", "state_diverged", {"target": "plans/09-x.md"})
        assert recovery_event_recorded(events, "state_diverged", "plans/09-x.md")
        assert not recovery_event_recorded(events, "state_diverged", "plans/08-y.md")
        assert not recovery_event_recorded(events, "recovered_state", "plans/09-x.md")

    def test_missing_events_file_is_not_recorded(self, tmp_path):
        assert not recovery_event_recorded(tmp_path / "events.jsonl", "state_diverged", None)

    def test_partial_lines_are_tolerated(self, tmp_path):
        events = tmp_path / "events.jsonl"
        events.parent.mkdir(parents=True, exist_ok=True)
        events.write_text('{"kind": "recovery", "action": "state_diverged", "detail": {"target"', encoding="utf-8")
        assert not recovery_event_recorded(events, "state_diverged", "plans/09-x.md")


class TestDetailDocComplete:
    COMPLETE = (
        "# detail\n\n## 业务逻辑\n- one\n\n## 数据模型\n- two\n\n"
        "## 数据流与控制流\n- three\n\n## 接口与边界\n- four\n\n## 验收钩子\n- five\n"
    )

    def _write(self, root: Path, name: str, text: str):
        doc = root / "plans" / name
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(text, encoding="utf-8")
        return doc

    def test_complete_document_with_five_sections(self, tmp_path):
        self._write(tmp_path, "01-ok.md", self.COMPLETE)
        assert detail_doc_complete(tmp_path, "plans/01-ok.md")

    def test_english_equivalent_sections_accepted(self, tmp_path):
        self._write(
            tmp_path,
            "02-en.md",
            "# detail\n## Business Logic\n## Data Model\n## Data Flow and Control Flow\n"
            "## Interfaces and Boundaries\n## Acceptance Hooks\n",
        )
        assert detail_doc_complete(tmp_path, "plans/02-en.md")

    @pytest.mark.parametrize(
        "mutate",
        [
            pytest.param(lambda text: text.replace("## 数据模型", "## 其他"), id="missing-section"),
            pytest.param(lambda text: text + "\n- [ ] unfinished feature\n", id="unchecked-checkbox"),
            pytest.param(lambda text: text + "\n- [x] done feature\n", id="checked-checkbox"),
        ],
    )
    def test_incomplete_variants(self, tmp_path, mutate):
        self._write(tmp_path, "03-bad.md", mutate(self.COMPLETE))
        assert not detail_doc_complete(tmp_path, "plans/03-bad.md")

    def test_missing_document_is_incomplete(self, tmp_path):
        assert not detail_doc_complete(tmp_path, "plans/04-absent.md")


class TestDecideTableAutorun:
    """autorun rows 5–7 plus the shared rows 1–4 and the audit adoption."""

    PACKAGE = [{"slug": "2026-10-07_demo", "checked": 1, "total": 8}]

    def test_row1_state_corrupt_names_files_and_forbids_silent_restart(self):
        decision = decide("autorun", None, "corrupt", plan_unchecked=2)
        assert decision.action == "state_corrupt"
        assert "chain.json" in decision.reason and "spawns.jsonl" in decision.reason
        assert "never silently restart" in decision.reason

    def test_row2_cap_reached_uses_next_equals_last_plus_one(self):
        # next == cap still runs; next > cap stops (the F3 boundary semantics)
        running = decide(
            "autorun",
            autorun_state(round_index=19, max_rounds=20),
            "ok",
            plan_unchecked=1,
            active_packages=self.PACKAGE,
        )
        assert running.action == "continue_package"
        stopped = decide(
            "autorun",
            autorun_state(round_index=20, max_rounds=20),
            "ok",
            plan_unchecked=1,
            active_packages=self.PACKAGE,
        )
        assert stopped.action == "cap_reached"
        assert stopped.detail["next"] == 21 and stopped.detail["cap"] == 20

    def test_row2_cap_falls_back_to_default_when_state_lacks_max(self):
        decision = decide("autorun", {"round": 20}, "ok", plan_unchecked=1)
        assert decision.action == "cap_reached"
        assert decision.detail["cap"] == 20  # DEFAULT_MAX_ROUNDS

    def test_row3_chain_complete_when_every_feature_checked(self):
        decision = decide(
            "autorun", autorun_state(), "ok", plan_checked=13, plan_unchecked=0, active_packages=self.PACKAGE
        )
        assert decision.action == "chain_complete"

    def test_row3_tolerant_zero_counts_mean_nothing_to_consume(self):
        decision = decide(
            "autorun", autorun_state(), "ok", plan_checked=0, plan_unchecked=0, active_packages=self.PACKAGE
        )
        assert decision.action == "chain_complete"
        assert "no qualifying planning document" in decision.reason

    def test_row4_fresh_chain_ignores_audit_tail(self):
        decision = decide("autorun", None, "missing", plan_unchecked=2, audit_tail={"round": 9})
        assert decision.action == "fresh_chain"

    def test_row5_continue_package_carries_slug_and_progress(self):
        decision = decide("autorun", autorun_state(), "ok", plan_unchecked=2, active_packages=self.PACKAGE)
        assert decision.action == "continue_package"
        assert decision.detail["slug"] == "2026-10-07_demo"
        assert decision.detail["checked"] == 1 and decision.detail["total"] == 8
        assert "2026-10-07_demo (1/8 tasks)" in decision.reason

    def test_row6_await_new_package_when_no_active_package(self):
        decision = decide("autorun", autorun_state(), "ok", plan_unchecked=3, active_packages=[])
        assert decision.action == "await_new_package"
        assert "/spec:new" in decision.reason

    def test_row7_disambiguate_multiple_packages_lists_slugs(self):
        packages = self.PACKAGE + [{"slug": "2026-10-08_other", "checked": 0, "total": 2}]
        decision = decide("autorun", autorun_state(), "ok", plan_unchecked=1, active_packages=packages)
        assert decision.action == "disambiguate_multiple_packages"
        assert decision.detail["packages"] == ["2026-10-07_demo", "2026-10-08_other"]

    def test_row13_audit_mismatch_adopts_audit_and_rejudges_from_cap(self):
        # state claims round 3, audit says round 7, cap 7: next=8 > cap
        decision = decide(
            "autorun",
            autorun_state(round_index=3, max_rounds=7),
            "ok",
            plan_unchecked=1,
            active_packages=self.PACKAGE,
            audit_tail={"round": 7},
        )
        assert decision.action == "cap_reached"
        assert decision.detail["recovered_state"] is True

    def test_row13_audit_match_is_not_flagged_recovered(self):
        decision = decide(
            "autorun",
            autorun_state(round_index=3, max_rounds=20),
            "ok",
            plan_unchecked=1,
            active_packages=self.PACKAGE,
            audit_tail={"round": 3},
        )
        assert decision.action == "continue_package"
        assert "recovered_state" not in decision.detail

    def test_read_state_rebuild_annotates_every_downstream_action(self):
        # half-written chain.json -> rebuild -> correct action + note
        with _tmp_dir() as chain_dir:
            write_chain_state(chain_dir, raw="{half")
            write_spawns(chain_dir, autorun_state(round_index=2))
            state, status = read_state(chain_dir, "autorun")
            assert status == "recovered"
            decision = decide("autorun", state, status, plan_unchecked=1, active_packages=self.PACKAGE)
            assert decision.action == "continue_package"
            assert decision.detail["recovered_state"] is True

    def test_row_order_cap_beats_package_resume(self):
        decision = decide(
            "autorun",
            autorun_state(round_index=20, max_rounds=20),
            "ok",
            plan_unchecked=1,
            active_packages=self.PACKAGE,
        )
        assert decision.action == "cap_reached"

    def test_row_order_complete_beats_fresh(self):
        decision = decide("autorun", None, "missing", plan_checked=3, plan_unchecked=0)
        assert decision.action == "chain_complete"


class TestDecideTableAutoplan:
    """autoplan rows 8–12 (state_diverged judged as the target-integrity precondition)."""

    BASE = dict(plan_unchecked=2, master_links=LINKS, master_doc=MASTER)

    def test_row8_resume_pass_assignment_when_target_incomplete(self):
        decision = decide(
            "autoplan", autoplan_state(target="plans/01-a.md"), "ok", detail_docs=docs("plans/01-a.md"), **self.BASE
        )
        assert decision.action == "resume_pass_assignment"
        assert decision.detail["target"] == "plans/01-a.md"

    def test_row8_framework_pass_resumes_the_master_document(self):
        decision = decide(
            "autoplan", autoplan_state(kind="framework", target=None), "ok", detail_docs=docs(MASTER), **self.BASE
        )
        assert decision.action == "resume_pass_assignment"
        assert decision.detail["target"] == MASTER

    def test_row9_resume_review(self):
        decision = decide("autoplan", autoplan_state(kind="review", target=None), "ok", detail_docs=docs(), **self.BASE)
        assert decision.action == "resume_review"

    def test_row10_advance_detail_names_next_pending_document(self):
        decision = decide(
            "autoplan", autoplan_state(target="plans/01-a.md"), "ok", detail_docs=docs("plans/02-b.md"), **self.BASE
        )
        assert decision.action == "advance_detail"
        assert decision.detail["target"] == "plans/02-b.md"

    def test_row10_index_and_master_are_never_advance_candidates(self):
        # README.md and the master itself are linked but not detail-pass material
        decision = decide(
            "autoplan",
            autoplan_state(target="plans/01-a.md"),
            "ok",
            detail_docs=docs("plans/README.md", MASTER),
            **self.BASE,
        )
        assert decision.action == "advance_review"

    def test_row11_advance_review_when_all_detail_documents_complete(self):
        decision = decide("autoplan", autoplan_state(target="plans/01-a.md"), "ok", detail_docs=docs(), **self.BASE)
        assert decision.action == "advance_review"
        assert decision.detail["detail_docs"] == 2  # 01-a + 02-b, master/index excluded

    def test_row12_state_diverged_master_index_wins(self):
        decision = decide("autoplan", autoplan_state(target="plans/99-ghost.md"), "ok", detail_docs=docs(), **self.BASE)
        assert decision.action == "state_diverged"
        assert decision.detail["target"] == "plans/99-ghost.md"
        assert decision.detail["master_links"] == LINKS

    def test_row12_divergence_outranks_resuming_the_stale_target(self):
        # the diverged target is incomplete on disk, but resuming a document
        # the master no longer links is exactly what the row exists to stop
        decision = decide(
            "autoplan",
            autoplan_state(target="plans/99-ghost.md"),
            "ok",
            detail_docs=docs("plans/99-ghost.md"),
            **self.BASE,
        )
        assert decision.action == "state_diverged"

    def test_unscored_paths_are_conservatively_incomplete(self):
        decision = decide("autoplan", autoplan_state(target="plans/01-a.md"), "ok", detail_docs=[], **self.BASE)
        assert decision.action == "resume_pass_assignment"

    def test_cap_boundary_next_equals_cap_still_runs(self):
        decision = decide(
            "autoplan", autoplan_state(pass_index=11, kind="review", target=None), "ok", detail_docs=docs(), **self.BASE
        )
        assert decision.action == "resume_review"


class TestActionVocabulary:
    def test_decisions_only_ever_emit_the_twelve_fixed_words(self, tmp_path):
        # every reachable branch emits a word from the fixed vocabulary
        seen = {
            decide("autorun", None, "corrupt").action,
            decide("autorun", {"round": 99}, "ok", plan_unchecked=1).action,
            decide("autorun", {"round": 1}, "ok", plan_checked=1, plan_unchecked=0).action,
            decide("autorun", None, "missing", plan_unchecked=1).action,
            decide("autorun", {"round": 1}, "ok", plan_unchecked=1, active_packages=[]).action,
            decide(
                "autoplan",
                autoplan_state(kind="review", target=None),
                "ok",
                plan_unchecked=1,
                master_links=LINKS,
                master_doc=MASTER,
            ).action,
            decide(
                "autoplan",
                autoplan_state(target="plans/zz.md"),
                "ok",
                plan_unchecked=1,
                master_links=LINKS,
                master_doc=MASTER,
                detail_docs=docs(),
            ).action,
        }
        assert seen <= ACTIONS
