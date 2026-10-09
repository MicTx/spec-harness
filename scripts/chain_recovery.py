"""Recovery semantics for the autorun/autoplan chains (plans/01 F4).

The F4 decision table lives here; the tolerant state read it consumes lives
in ``scripts/chain_spawn_support.py`` (the F5 shared module) and is
re-exported below for the chain scripts and the recovery tests. Two
surfaces, both consumed by the two chain scripts' ``status`` render:

``read_state(chain_dir, chain)``
    Re-export of ``chain_spawn_support.read_state``: tolerant read of
    ``chain.json`` with a rebuild path (a corrupt state is rebuilt from the
    last parseable ``spawns.jsonl`` line — the audit is the truth source —
    written back with ``recovered_from`` plus a ``recovery`` event). A
    missing ``chain.json`` is a fresh chain even when audit lines exist: the
    spawn flow writes ``chain.json`` before appending audit.

``decide(...)``
    The 13-row decision table from ``plans/01`` F4, judged in order: the four
    common rows strictly (corrupt → cap → complete → fresh), the audit-mismatch
    adoption between the corrupt row and the cap row (audit wins, the decision
    is re-judged from the cap row with a ``recovered_state`` note), then the
    per-chain rows (autorun: continue/await/disambiguate; autoplan:
    state_diverged as the target-integrity precondition, then
    resume/resume_review/advance_detail/advance_review). ``decide`` is a pure
    function — every recovery side effect (rebuild write-back, divergence
    audit) belongs to the shared ``read_state`` / the caller.

The action vocabulary is fixed at twelve words (plans/01 table); a new action
word requires a plans/01 table change first. Chain caps are opt-in only
(explicit ``--max-rounds`` / ``--max-passes``): a missing or null cap means
the chain is unbounded, and ``cap_reached`` is reachable only for a chain
whose last spawn carried an explicit cap. The section-title and
feature-checkbox patterns mirror ``autorun_spawn`` (F5 owns the
shared-layer extraction).

Import direction after F5: ``chain_recovery`` imports only from
``chain_spawn_support`` (the leaf) and re-exports the recovery read surface;
it no longer imports either chain script, so the old lazy-import circularity
workarounds are gone.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Recovery read surface and guard caps: one implementation in the shared
# module, re-exported here so the chain scripts and recovery tests keep
# their import site (the caps are the F12 unified home's fallback values).
from chain_spawn_support import (  # noqa: F401  # re-exports
    CHAIN_EVENTS_FILE,
    CHAIN_SPAWNS_FILE,
    CHAIN_STATE_FILE,
    READ_CORRUPT,
    READ_MISSING,
    READ_OK,
    READ_RECOVERED,
    append_recovery_event,
    read_audit_tail,
    read_state,
    recovery_event_recorded,
)

COUNTER_KEY = {"autorun": "round", "autoplan": "pass"}
CAP_KEY = {"autorun": "max_rounds", "autoplan": "max_passes"}
CAP_LABEL = {"autorun": ("round", "rounds"), "autoplan": ("pass", "passes")}

# The fixed twelve-word action vocabulary (plans/01 F4 decision table).
ACTIONS = frozenset(
    {
        "state_corrupt",
        "cap_reached",
        "chain_complete",
        "fresh_chain",
        "continue_package",
        "await_new_package",
        "disambiguate_multiple_packages",
        "resume_pass_assignment",
        "resume_review",
        "advance_detail",
        "advance_review",
        "state_diverged",
    }
)

# Feature-checkbox and section-title patterns mirror autorun_spawn's
# FEATURE_CHECKBOX / detail-doc shape (five canonical sections; equivalent
# English titles accepted, case-insensitive).
_FEATURE_CHECKBOX = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s*(\S.*)$")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$")
SECTION_TITLES: Tuple[Tuple[str, ...], ...] = (
    ("业务逻辑", "business logic"),
    ("数据模型", "data model"),
    ("数据流", "控制流", "data flow", "control flow"),
    ("接口", "边界", "interface", "boundary"),
    ("验收钩子", "验收", "acceptance hook", "acceptance"),
)
# The planning index and the master document itself are maintained by other
# passes (gate/organize/framework), never by a detail pass, so they stay out
# of the advance_detail candidates.
PLANNING_INDEX_DOC = "plans/README.md"


@dataclass
class RecoveryDecision:
    """One row of the F4 decision table: what the chain should do next."""

    action: str
    reason: str
    detail: Dict[str, object] = field(default_factory=dict)


def detail_doc_complete(root: Path, rel_path: str) -> bool:
    """Five-section completeness heuristic for one detail document.

    Complete = the file exists + every canonical section has an equivalent
    heading (业务逻辑/数据模型/数据流与控制流/接口与边界/验收钩子, English
    equivalents accepted) + no feature checkbox of either state. Cluster-level
    qualification and structure invariants stay with ``autoplan_gate.py``;
    this heuristic only feeds the resume decision.
    """
    doc = root / rel_path
    if not doc.is_file():
        return False
    try:
        text = doc.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if any(_FEATURE_CHECKBOX.match(line) for line in text.splitlines()):
        return False
    headings = []
    for line in text.splitlines():
        match = _HEADING.match(line)
        if match:
            headings.append(match.group(1).lower())
    for titles in SECTION_TITLES:
        if not any(title.lower() in heading for heading in headings for title in titles):
            return False
    return True


def _as_int(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def decide(
    chain: str,
    state: Optional[Dict[str, object]],
    read_status: str,
    *,
    plan_checked: int = 0,
    plan_unchecked: int = 0,
    cap: Optional[int] = None,
    audit_tail: Optional[Dict[str, object]] = None,
    active_packages: Optional[List[Dict[str, object]]] = None,
    master_links: Optional[List[str]] = None,
    detail_docs: Optional[List[Dict[str, object]]] = None,
    master_doc: Optional[str] = None,
) -> RecoveryDecision:
    """Judge the 13-row F4 decision table and return the next chain action.

    Row order: corrupt → (audit adoption, re-judged from the cap row) → cap →
    complete → fresh → per-chain rows. ``plan_checked``/``plan_unchecked``
    come from the planning-document counts (a tolerant ``(0, 0)`` when the
    caller found no qualifying document); ``cap`` falls back to the state's
    ``max_rounds``/``max_passes`` and then to no cap at all — chains are
    unbounded unless a spawn carried an explicit cap. Pure: no I/O,
    no clock — the caller owns every recovery side effect.
    """
    counter_key = COUNTER_KEY.get(chain, "round")
    counter_label, cap_label = CAP_LABEL.get(chain, ("round", "rounds"))

    # Row 1: unreadable state that the audit could not rebuild.
    if read_status == READ_CORRUPT or (state is None and read_status != READ_MISSING):
        return RecoveryDecision(
            "state_corrupt",
            "chain state unreadable and the spawns.jsonl audit cannot rebuild it — inspect "
            f"{CHAIN_STATE_FILE} and {CHAIN_SPAWNS_FILE} manually before touching the chain; "
            f"never silently restart from {counter_label} 1 (that would bypass the cap)",
            {"read_status": read_status},
        )

    detail: Dict[str, object] = {}
    recovered_state = read_status == READ_RECOVERED

    # Audit adoption (the table's final row, judged before the cap row): the
    # audit tail is the truth source, so a half-written state counter adopts
    # the audited value and judgment continues from the cap row.
    if state is not None and isinstance(audit_tail, dict):
        state_counter = _as_int(state.get(counter_key))
        audit_counter = _as_int(audit_tail.get(counter_key))
        if audit_counter is not None and audit_counter != state_counter:
            state = dict(state)
            state[counter_key] = audit_counter
            recovered_state = True

    effective_cap = cap
    if effective_cap is None and state is not None:
        effective_cap = _as_int(state.get(CAP_KEY.get(chain, "max_rounds")))

    counter = _as_int(state.get(counter_key)) if state is not None else None
    counter = counter if counter is not None else 0

    # Row 2: cap — next = last + 1; next == cap still runs, next > cap stops.
    # An unbounded chain (no explicit cap anywhere) never takes this row.
    next_index = counter + 1
    if effective_cap is not None and next_index > effective_cap:
        if recovered_state:
            detail["recovered_state"] = True
        return RecoveryDecision(
            "cap_reached",
            f"next {counter_label} {next_index} exceeds the cap {effective_cap} — raise "
            f"--max-{cap_label} deliberately or close the chain; the cap exists to stop runaway loops",
            {"next": next_index, "cap": effective_cap, **detail},
        )

    # Row 3: every planning feature checked (a tolerant 0/0 means no
    # qualifying document — equally nothing for the chain to consume).
    if plan_unchecked == 0:
        if plan_checked == 0:
            reason = "no qualifying planning document found — nothing for the chain to consume"
        else:
            reason = f"all {plan_checked} planning features checked — the chain is complete"
        if recovered_state:
            detail["recovered_state"] = True
        return RecoveryDecision("chain_complete", reason, {"checked": plan_checked, **detail})

    # Row 4: no chain.json — a fresh chain (the audit never re-creates it).
    if state is None:
        return RecoveryDecision(
            "fresh_chain",
            "no chain.json — the chain has not started; spawn writes the initial state",
            {},
        )

    if recovered_state:
        detail["recovered_state"] = True

    if chain == "autorun":
        packages = [entry for entry in (active_packages or []) if isinstance(entry, dict)]
        # Row 5: exactly one active package — resume it.
        if len(packages) == 1:
            entry = packages[0]
            return RecoveryDecision(
                "continue_package",
                "active package {} ({}/{} tasks) unfinished — /spec:run resumes it".format(
                    entry.get("slug", "unknown"), entry.get("checked", 0), entry.get("total", 0)
                ),
                {
                    "slug": entry.get("slug"),
                    "checked": entry.get("checked", 0),
                    "total": entry.get("total", 0),
                    **detail,
                },
            )
        # Row 7: multiple active packages — one package per chain, by invariant.
        if len(packages) > 1:
            slugs = [str(entry.get("slug", "unknown")) for entry in packages]
            return RecoveryDecision(
                "disambiguate_multiple_packages",
                "multiple active packages ({}) — archive or finish all but one, then re-run "
                "status; one chain carries one package".format(", ".join(slugs)),
                {"packages": slugs, **detail},
            )
        # Row 6: no active package and the plan is unfinished — plan first.
        return RecoveryDecision(
            "await_new_package",
            f"no active package and {plan_unchecked} planning features unchecked — run "
            "/spec:new for the next feature, then spawn the next round",
            {"unchecked": plan_unchecked, **detail},
        )

    # autoplan rows. The integrity precondition comes first: a recorded target
    # the master plan no longer links must not be resumed — the master index
    # wins and the assignment is re-derived from it.
    links = [link for link in (master_links or []) if isinstance(link, str)]
    target = state.get("target") if isinstance(state.get("target"), str) else None
    if target is not None and links and target not in links:
        return RecoveryDecision(
            "state_diverged",
            "state target {} is not linked from the master plan — re-derive the assignment "
            "from the master index ({} linked document{})".format(target, len(links), "s" if len(links) != 1 else ""),
            {"target": target, "master_links": links, **detail},
        )

    kind = state.get("kind") if isinstance(state.get("kind"), str) else None
    # decide is pure: completeness scores arrive via detail_docs; a path the
    # caller did not score is conservatively incomplete.
    completeness = {
        entry.get("path"): bool(entry.get("complete")) for entry in (detail_docs or []) if isinstance(entry, dict)
    }

    # Row 9: a review pass in flight — resume the review session.
    if kind == "review":
        return RecoveryDecision(
            "resume_review",
            "review pass in progress — resume the review session and let it close the chain",
            {**detail},
        )

    # Rows 8/10/11: framework and detail passes both resume an assigned
    # document (the framework pass's assignment is the master document).
    assignment = target if target is not None else master_doc
    if assignment is not None and not completeness.get(assignment, False):
        return RecoveryDecision(
            "resume_pass_assignment",
            "assigned document {} is incomplete (missing, lacks the five sections, or still "
            "has feature checkboxes) — continue the pass on it".format(assignment),
            {"target": assignment, **detail},
        )

    # Row 10: assignment done but master-linked detail documents remain.
    excluded = {link for link in (master_doc, PLANNING_INDEX_DOC) if link}
    candidates = [link for link in links if link not in excluded]
    pending = [link for link in candidates if not completeness.get(link, False)]
    if pending:
        return RecoveryDecision(
            "advance_detail",
            "assigned document complete; next detail document {} is missing or incomplete — "
            "spawn a detail pass for it".format(pending[0]),
            {"target": pending[0], **detail},
        )

    # Row 11: every master-linked detail document complete, review not entered.
    return RecoveryDecision(
        "advance_review",
        "all {} master-linked detail documents are complete — spawn the review pass".format(len(candidates)),
        {"detail_docs": len(candidates), **detail},
    )
