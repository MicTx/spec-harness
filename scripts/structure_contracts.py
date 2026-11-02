#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Direct, resolved import facts and architecture contracts for organize."""

from __future__ import annotations

import fnmatch
import json
import os
import secrets
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Optional

from safe_open_support import SafeOpenError, open_regular_read

MAX_CONFIG_BYTES = 2 * 1024 * 1024
_MISSING = object()
RULES = frozenset({"forbidden-import", "layer-direction", "public-api-bypass"})


@dataclass
class ImportGraph:
    """One scan's directed imports and the legacy reference-counting inputs."""

    edges: set[tuple[str, str]] = field(default_factory=set)
    referenced_paths: set[str] = field(default_factory=set)
    imported_modules: set[str] = field(default_factory=set)


def python_module_paths(tracked: list[str]) -> dict[str, str]:
    """Map repo-root module names to files, preferring packages over peer files."""
    files: dict[str, str] = {}
    packages: dict[str, str] = {}
    for rel in sorted(tracked):
        path = PurePosixPath(rel)
        if path.suffix != ".py":
            continue
        name = ".".join(path.with_suffix("").parts)
        files[name] = rel
        if path.name == "__init__.py" and len(path.parts) > 1:
            packages[".".join(path.parts[:-1])] = rel
    files.update(packages)
    return files


class ContractError(ValueError):
    """An invalid architecture contract or baseline with a source location."""


@dataclass(frozen=True)
class ContractModule:
    name: str
    layer: str
    paths: tuple[str, ...]
    members: frozenset[str]
    # None means unrestricted; an empty set means no public files.
    public: Optional[frozenset[str]]


@dataclass(frozen=True)
class ForbiddenImport:
    source: str
    target: str
    reason: str


@dataclass(frozen=True)
class Contracts:
    layers: tuple[str, ...]
    modules: tuple[ContractModule, ...]
    forbid: tuple[ForbiddenImport, ...]


@dataclass(frozen=True)
class Violation:
    rule: str
    importer: str
    target: str
    reason: str

    @property
    def key(self) -> tuple[str, str, str]:
        return self.rule, self.importer, self.target


@dataclass(frozen=True)
class Baseline:
    exists: bool
    generated_at: Optional[str] = None
    head: Optional[str] = None
    entries: tuple[Violation, ...] = ()


@dataclass(frozen=True)
class BaselineComparison:
    new: tuple[Violation, ...]
    matched: tuple[Violation, ...]
    stale: tuple[Violation, ...]


def _error(path: Path, location: str, message: str) -> None:
    raise ContractError(f"{path}: {location}: {message}")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: Path) -> object:
    try:
        with open_regular_read(path, max_bytes=MAX_CONFIG_BYTES) as descriptor:
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                data = handle.read(MAX_CONFIG_BYTES + 1)
        if len(data) > MAX_CONFIG_BYTES:
            raise ValueError(f"file exceeds {MAX_CONFIG_BYTES} bytes")
        return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except SafeOpenError as error:
        if error.reason == "missing":
            return _MISSING
        raise ContractError(f"{path}: cannot safely read file: {error}") from error
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise ContractError(f"{path}: invalid JSON: {error}") from error


def _object(path: Path, value: object, location: str, required: set[str], optional: set[str]) -> dict:
    if not isinstance(value, dict):
        _error(path, location, "expected object")
    missing = required - value.keys()
    if missing:
        _error(path, f"{location}.{sorted(missing)[0]}".lstrip("."), "missing required field")
    unknown = value.keys() - required - optional
    if unknown:
        _error(path, f"{location}.{sorted(unknown)[0]}".lstrip("."), "unknown field")
    return value


def _text(path: Path, value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        _error(path, location, "expected nonempty trimmed string")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        _error(path, location, "control characters are not allowed")
    return value


def _pattern(path: Path, value: object, location: str) -> str:
    pattern = _text(path, value, location)
    parts = pattern.split("/")
    if (
        "\\" in pattern
        or any(part in ("", ".", "..") for part in parts)
        or any("**" in part and part != "**" for part in parts)
        or ":" in parts[0]
    ):
        _error(path, location, "expected safe repository-relative glob; ** must be a whole segment")
    return pattern


def matches_path(pattern: str, path: str) -> bool:
    """Match a full POSIX path; ** spans zero or more whole components."""
    patterns = tuple(pattern.split("/"))
    parts = tuple(path.split("/"))

    @lru_cache(maxsize=None)
    def match(pattern_index: int, path_index: int) -> bool:
        if pattern_index == len(patterns):
            return path_index == len(parts)
        if patterns[pattern_index] == "**":
            return match(pattern_index + 1, path_index) or (
                path_index < len(parts) and match(pattern_index, path_index + 1)
            )
        return (
            path_index < len(parts)
            and fnmatch.fnmatchcase(parts[path_index], patterns[pattern_index])
            and match(pattern_index + 1, path_index + 1)
        )

    return match(0, 0)


def load_contracts(path: Path, tracked: list[str]) -> Optional[Contracts]:
    """Load a version-1 JSON contract against the scanned repository paths.

    Layers are high-to-low. Each module has name, paths, layer and optional
    public globs. Absent public permits all its files; [] permits none.
    Forbid entries have from, to and reason. Patterns match complete paths.
    """
    raw = _read_json(path)
    if raw is _MISSING:
        return None
    data = _object(path, raw, "", {"version", "layers", "modules", "forbid"}, set())
    if type(data["version"]) is not int or data["version"] != 1:
        _error(path, "version", "expected version 1")
    if not isinstance(data["layers"], list) or not data["layers"]:
        _error(path, "layers", "expected a nonempty ordered array")
    layers: list[str] = []
    for index, value in enumerate(data["layers"]):
        layer = _text(path, value, f"layers[{index}]")
        if layer in layers:
            _error(path, f"layers[{index}]", f"duplicate layer {layer!r}")
        layers.append(layer)

    if not isinstance(data["modules"], list) or not data["modules"]:
        _error(path, "modules", "expected a nonempty array")
    available = set(tracked)
    owners: dict[str, str] = {}
    names: set[str] = set()
    modules: list[ContractModule] = []
    for index, value in enumerate(data["modules"]):
        loc = f"modules[{index}]"
        module = _object(path, value, loc, {"name", "paths", "layer"}, {"public"})
        name = _text(path, module["name"], f"{loc}.name")
        if name in names:
            _error(path, f"{loc}.name", f"duplicate module {name!r}")
        names.add(name)
        layer = _text(path, module["layer"], f"{loc}.layer")
        if layer not in layers:
            _error(path, f"{loc}.layer", f"unknown layer {layer!r}")
        if not isinstance(module["paths"], list) or not module["paths"]:
            _error(path, f"{loc}.paths", "expected a nonempty array")
        patterns: list[str] = []
        members: set[str] = set()
        for number, value in enumerate(module["paths"]):
            field = f"{loc}.paths[{number}]"
            pattern = _pattern(path, value, field)
            hits = {item for item in available if matches_path(pattern, item)}
            if not hits:
                _error(path, field, f"glob matches no scanned files: {pattern!r}")
            patterns.append(pattern)
            members.update(hits)
        for member in sorted(members):
            _file_path(path, member, f"{loc}.paths")
            if member in owners:
                _error(path, f"{loc}.paths", f"{member!r} already belongs to module {owners[member]!r}")
            owners[member] = name
        public: Optional[frozenset[str]] = None
        if "public" in module:
            if not isinstance(module["public"], list):
                _error(path, f"{loc}.public", "expected an array")
            public_members: set[str] = set()
            for number, value in enumerate(module["public"]):
                field = f"{loc}.public[{number}]"
                pattern = _pattern(path, value, field)
                hits = {item for item in available if matches_path(pattern, item)}
                if not hits or not hits <= members:
                    _error(path, field, "glob must match only files in this module")
                public_members.update(hits)
            public = frozenset(public_members)
        modules.append(ContractModule(name, layer, tuple(patterns), frozenset(members), public))

    if not isinstance(data["forbid"], list):
        _error(path, "forbid", "expected an array")
    forbidden: list[ForbiddenImport] = []
    pairs: set[tuple[str, str]] = set()
    for index, value in enumerate(data["forbid"]):
        loc = f"forbid[{index}]"
        item = _object(path, value, loc, {"from", "to", "reason"}, set())
        source = _text(path, item["from"], f"{loc}.from")
        target = _text(path, item["to"], f"{loc}.to")
        reason = _text(path, item["reason"], f"{loc}.reason")
        for key, name in (("from", source), ("to", target)):
            if name not in names:
                _error(path, f"{loc}.{key}", f"unknown module {name!r}")
        if (source, target) in pairs:
            _error(path, loc, "duplicate forbidden module pair")
        pairs.add((source, target))
        forbidden.append(ForbiddenImport(source, target, reason))
    return Contracts(tuple(layers), tuple(modules), tuple(forbidden))


def evaluate_violations(graph: ImportGraph, contracts: Contracts) -> list[Violation]:
    """Check resolved direct edges, producing at most one violation per edge."""
    owners = {member: module for module in contracts.modules for member in module.members}
    layers = {name: index for index, name in enumerate(contracts.layers)}
    forbidden = {(item.source, item.target): item.reason for item in contracts.forbid}
    violations: list[Violation] = []
    for importer, target in sorted(graph.edges):
        source_module = owners.get(importer)
        target_module = owners.get(target)
        if source_module is None or target_module is None:
            continue
        reason = forbidden.get((source_module.name, target_module.name))
        if reason is not None:
            rule = "forbidden-import"
        elif source_module.name == target_module.name:
            continue
        elif layers[source_module.layer] > layers[target_module.layer]:
            rule = "layer-direction"
            reason = f"layer {source_module.layer!r} cannot import higher layer {target_module.layer!r}"
        elif target_module.public is not None and target not in target_module.public:
            rule = "public-api-bypass"
            reason = f"module {target_module.name!r} does not expose this file as public"
        else:
            continue
        violations.append(Violation(rule, importer, target, reason))
    return sorted(violations, key=lambda item: item.key)


def _file_path(path: Path, value: object, location: str) -> str:
    result = _text(path, value, location)
    parts = result.split("/")
    if "\\" in result or any(part in ("", ".", "..") for part in parts) or ":" in parts[0]:
        _error(path, location, "expected a safe repository-relative path")
    return result


def load_baseline(path: Path) -> Baseline:
    """Read a baseline strictly; a missing file is an empty exemption set."""
    raw = _read_json(path)
    if raw is _MISSING:
        return Baseline(exists=False)
    data = _object(path, raw, "", {"version", "generated_at", "head", "entries"}, set())
    if type(data["version"]) is not int or data["version"] != 1:
        _error(path, "version", "expected version 1")
    generated_at = _text(path, data["generated_at"], "generated_at")
    head = data["head"]
    if head is not None:
        head = _text(path, head, "head")
    if not isinstance(data["entries"], list):
        _error(path, "entries", "expected an array")
    entries: list[Violation] = []
    keys: set[tuple[str, str, str]] = set()
    for index, value in enumerate(data["entries"]):
        loc = f"entries[{index}]"
        item = _object(path, value, loc, {"rule", "importer", "target", "reason"}, set())
        rule = _text(path, item["rule"], f"{loc}.rule")
        if rule not in RULES:
            _error(path, f"{loc}.rule", f"unknown rule {rule!r}")
        violation = Violation(
            rule,
            _file_path(path, item["importer"], f"{loc}.importer"),
            _file_path(path, item["target"], f"{loc}.target"),
            _text(path, item["reason"], f"{loc}.reason"),
        )
        if violation.key in keys:
            _error(path, loc, "duplicate baseline entry")
        keys.add(violation.key)
        entries.append(violation)
    return Baseline(True, generated_at, head, tuple(sorted(entries, key=lambda item: item.key)))


def compare_baseline(violations: list[Violation], baseline: Baseline) -> BaselineComparison:
    current = {item.key: item for item in violations}
    frozen = {item.key: item for item in baseline.entries}
    return BaselineComparison(
        tuple(current[key] for key in sorted(current.keys() - frozen.keys())),
        tuple(current[key] for key in sorted(current.keys() & frozen.keys())),
        tuple(frozen[key] for key in sorted(frozen.keys() - current.keys())),
    )


def build_baseline(
    violations: list[Violation], old: Baseline, *, head: Optional[str], incomplete: tuple[str, ...] = ()
) -> Baseline:
    """Freeze current violations, preserve reviewed reasons and discard stale ones."""
    if incomplete:
        raise ContractError(f"cannot update baseline from an incomplete scan: {incomplete[0]}")
    previous = {item.key: item for item in old.entries}
    unique: dict[tuple[str, str, str], Violation] = {}
    for item in sorted(violations, key=lambda entry: (entry.key, entry.reason)):
        if not item.reason.strip():
            raise ContractError(f"baseline entry {item.key}: reason must not be empty")
        unique.setdefault(item.key, previous.get(item.key, item))
    entries = tuple(unique[key] for key in sorted(unique))
    if old.exists and old.entries == entries:
        return old
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return Baseline(True, generated, head, entries)


def write_baseline(path: Path, baseline: Baseline) -> None:
    """Atomically replace one baseline; refuse unsafe endpoints and clean temp files."""
    payload = {
        "version": 1,
        "generated_at": baseline.generated_at,
        "head": baseline.head,
        "entries": [
            {"rule": item.rule, "importer": item.importer, "target": item.target, "reason": item.reason}
            for item in baseline.entries
        ],
    }
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(data) > MAX_CONFIG_BYTES:
        raise ContractError(f"{path}: baseline exceeds {MAX_CONFIG_BYTES} bytes")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory_flag is None:
        raise ContractError(f"{path}: safe baseline writes are unsupported on this platform")
    directory = None
    temporary = None
    owns_temporary = False
    try:
        directory = os.open(path.parent, os.O_RDONLY | directory_flag | nofollow)
        try:
            current = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        if current is not None and not stat.S_ISREG(current.st_mode):
            raise ContractError(f"{path}: refusing symlink or non-regular baseline")
        if current is not None:
            with open_regular_read(path, max_bytes=MAX_CONFIG_BYTES) as descriptor:
                with os.fdopen(descriptor, "rb", closefd=False) as handle:
                    if handle.read() == data:
                        return
        temporary = f".{path.name}.{secrets.token_hex(12)}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600, dir_fd=directory)
        owns_temporary = True
        with os.fdopen(descriptor, "wb") as handle:
            if current is not None:
                os.fchmod(handle.fileno(), stat.S_IMODE(current.st_mode))
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        owns_temporary = False
    except (OSError, SafeOpenError) as error:
        raise ContractError(f"{path}: cannot safely update baseline: {error}") from error
    finally:
        if owns_temporary and temporary is not None and directory is not None:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
        if directory is not None:
            os.close(directory)
