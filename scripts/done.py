"""Final-tree verification, archive, summary and script-owned delivery commit."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import evidence
import gate
import gitx
import local_evidence
import push
from package import (
    append_run,
    checkpoint_paths,
    delivery_receipt,
    effective_finish,
    extract_branch,
    git_add_paths,
    latest_gate,
    now,
    owned_product_tree,
    package_baseline,
    package_dir,
    park_decisions,
    parse_ledger,
    parse_tasks,
    read_archived_package,
    read_package,
    refresh_product_tree,
    render_task_marks,
    runs,
    staged_product_tree,
    task_records,
    tree_fingerprint,
    worktree_receipt,
)
from path_safety import validate_source_tree


def build_summary(root: Path, slug: str, result: dict) -> str:
    package, spec, tasks = read_package(root, slug)
    records = task_records(package, tasks)
    decisions = parse_ledger(spec, "Decisions", "D")
    parked = parse_ledger(spec, "Parked", "P")
    decisions += [
        dict(id=row["id"], text=f"{row['choice']}: {row['reason']} | by: {row['by']}")
        for row in park_decisions(package).values()
    ]
    emergent = [record.title for record in records if record.origin == "emergent"]
    lines = [
        f"# {slug} - Completion Summary",
        "",
        "## Delivery",
        f"- Authorized finish: {result['finish']} (requested: {result['requested_finish']}).",
        *[f"- {record.title}" for record in records if record.checked or result["finish"] == "worktree"],
        "",
        "## Evidence",
        "- Final-tree verifies passed; see runs.jsonl.",
        "",
        "## Decisions",
    ]
    lines.extend(f"- {item['id']} | {item['text']}" for item in decisions)
    lines.extend(
        [
            "",
            "## Parked",
            *(f"- {item['id']} | {item['text']}" for item in parked),
            "",
            "## Emergent tasks",
            *(f"- {item}" for item in emergent),
            "",
            f"- Archived: {now()}",
            "",
            "## Git",
            f"- Commit interval: {gitx.git(root, 'rev-parse', 'HEAD').stdout.strip()}",
        ]
    )
    return "\n".join(lines) + "\n"


def recover_archive(root: Path, slug: str) -> dict | None:
    archive, spec, tasks = read_archived_package(root, slug)
    if package_baseline(archive).get("finish") == "worktree":
        return None
    final = latest_gate(archive, execution=True)
    if not final.get("product_tree") or final.get("passed") is not True or extract_branch(spec) != gitx.branch(root):
        return None
    validate_source_tree(archive)
    commit, receipt = delivery_receipt(root, slug)
    if receipt:
        if not local_evidence.is_local(root) or any(row.get("kind") == "delivery" for row in runs(archive)):
            return None
        tree = staged_product_tree(root, revision=commit)
        if errors := gate.archive_proof_errors(archive, spec, tasks, tree, receipt):
            raise ValueError("; ".join(errors))
        append_run(archive, {"kind": "delivery", "head": commit, "ts": now()})
        delivery, requested = effective_finish(archive, spec)
        return dict(
            slug=slug,
            passed=True,
            findings=[],
            errors=[],
            parked=[],
            verification="passed",
            archive=str(archive),
            local_archive=local_evidence.sync_archive(root, archive, slug),
            head=commit,
            finish=delivery,
            requested_finish=requested,
        )
    active = package_dir(root, slug)
    if active.exists() or any(row.get("kind") == "delivery" for row in runs(archive)):
        raise ValueError("cannot restore the interrupted package safely")
    paths = tuple(path.relative_to(root).as_posix() for path in (active, archive))
    staged = gitx.git(root, "diff", "--cached", "--name-only", "-z", "--", *paths, text=False).stdout.split(b"\0")
    if staged != [b""]:
        names = [os.fsdecode(path) for path in staged if path]
        gitx.git(root, "--literal-pathspecs", "restore", "--staged", "--source=HEAD", "--", *names)
    shutil.move(str(archive), str(active))
    return finish(root, slug)


def finish(root: Path, slug: str) -> dict:
    package, spec, tasks = read_package(root, slug)
    head = gitx.head(root)
    quick = package_baseline(package).get("finish") == "worktree"
    with gitx.temporary_index(root) as env:
        live_tree, before = refresh_product_tree(root, env)
        result = gate.compute(root, slug, fingerprint=before, snapshot=env if quick else None)
        if not result["passed"]:
            gate.record(root, slug, result)
            return result
        records = parse_tasks(tasks)
        patterns = tuple(pattern for task in records for pattern in task.globs)
        delivery, requested = effective_finish(package, spec)
        product_tree = live_tree if quick else owned_product_tree(root, env, patterns, head)
        working = root if live_tree == before == product_tree else gitx.materialize_tree(root, env, product_tree, head)
        checks = evidence.final_batch(working, records)
        _, after = refresh_product_tree(root, env)
        result = gate.compute(root, slug, fingerprint=after, snapshot=env if quick else None)
        result.update(product_tree=product_tree, checks=checks)
        failures = gate.proof_findings(records, checks)
        candidate_changed = working != root and tree_fingerprint(working) != product_tree
        if (
            after != before
            or gitx.head(root) != head
            or read_package(root, slug)[1:] != (spec, tasks)
            or candidate_changed
        ):
            failures.append(gate.finding("tree-changed", "product tree or plan changed during final verification"))
        gate.add(result, *failures)
        result["verification"] = "failed" if failures else "passed"
    if result["passed"] and delivery in {"merge", "push"}:
        try:
            push.preflight(root, slug, spec, list(patterns))
        except push.HandoffBlocked as exc:
            gate.add(
                result,
                gate.finding(
                    "handoff-blocked", str(exc), exc.subject, owner="user", action="choose-handoff", requested=requested
                ),
            )
    gate.record(root, slug, result, final=True)
    if not result["passed"]:
        return result
    result.update(finish=delivery, requested_finish=requested)
    if quick:
        validate_source_tree(package)
    else:
        _, local = checkpoint_paths(root, slug, patterns)
        index_before = gitx.stdout(root, "write-tree")
        if local:
            local_evidence.preflight_archive(root, slug)
            local_evidence.ensure(root)
            local_evidence.untrack(root)
    marked = render_task_marks(tasks, {task.id: "x" for task in records})
    (package / "tasks.md").write_text(marked, encoding="utf-8")
    (package / "completion-summary.md").write_text(build_summary(root, slug, result), encoding="utf-8")
    destination = package.parent / "archive" / slug
    if destination.parent.is_symlink() or destination.exists() or destination.is_symlink():
        raise ValueError(f"archive destination is not available: {destination}")
    destination.parent.mkdir(exist_ok=True)
    if quick:
        worktree_receipt(root, slug, gate.archive_digest(package))
        package.rename(destination)
        return dict(result, slug=slug, archive=str(destination), head=head)
    package.rename(destination)
    try:
        paths = list(patterns)
        if not local:
            paths += [f".spec/specs/{slug}", f".spec/specs/archive/{slug}"]
        git_add_paths(root, paths)
        if staged_product_tree(root) != product_tree:
            raise ValueError("delivery index differs from the verified product tree")
        digest = gate.archive_digest(destination)
        env = dict(gitx.clean_env(), SPEC_DELIVERY_RECEIPT=f"{slug}:{digest}")
        message = f"feat(spec): deliver {slug}\n\nSpec: {slug}\nSpec-Proof: {digest}"
        gitx.git(root, "commit", "--allow-empty", "-m", message, env=env)
        if staged_product_tree(root, revision=gitx.head(root)) != product_tree:
            raise ValueError("the Git hook changed the verified product tree; no delivery was recorded")
    except Exception:
        shutil.move(str(destination), str(package))
        if gitx.head(root) == head:
            gitx.git(root, "read-tree", index_before)
        raise
    result.update(slug=slug, archive=str(destination), head=gitx.head(root))
    if local:
        append_run(destination, {"kind": "delivery", "head": result["head"], "ts": now()})
        result["local_archive"] = local_evidence.sync_archive(root, destination, slug)
    return result
