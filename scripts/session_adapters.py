# scripts/session_adapters.py
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Bounded read-only parsers for native agent session stores.

Each verified provider exposes ``discover(ctx)`` (metadata-level candidate
enumeration inside the workspace scope) and ``read_messages(ctx, candidate)``
(bounded export of plain user/assistant text plus recognized summaries).
Candidates carry a private ``_locator`` resolved through scoped discovery;
provider bodies are only ever reached through whitelisted store paths that
pass the safe-open contract. Everything exported is untrusted data.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import time
import urllib.parse
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from safe_open_support import SafeOpenError, open_regular_read

DEFAULT_CANDIDATE_LIMIT = 12
HARD_CANDIDATE_CAP = 50
DEFAULT_MESSAGE_LIMIT = 24
HARD_MESSAGE_CAP = 100
METADATA_ROW_CAP = 200
PARENT_TRAVERSAL_CAP = 128
DIR_LISTING_CAP = 200
JSONL_HEAD_BYTES = 128 * 1024
JSONL_TAIL_BYTES = 512 * 1024
JSONL_WHOLE_MAX = 2 * 1024 * 1024
JSONL_RECORD_CAP = 256 * 1024
TOTAL_DECODE_CAP = 8 * 1024 * 1024
SOFT_DEADLINE_SECONDS = 5.0
SQL_TEXT_CAP = 256 * 1024
SQL_PROGRESS_LIMIT = 5_000_000
SQL_PROGRESS_INTERVAL = 10_000
SQL_CONNECT_TIMEOUT = 5.0
SQLITE_HEADER_BYTES = 100
SHM_MULTIPLE = 32768

DISCOVERY_PROVIDER_BYTES = 1 * 1024 * 1024
METADATA_PEEK_HEAD = 64 * 1024
METADATA_PEEK_TAIL = 64 * 1024
METADATA_PEEK_WHOLE = METADATA_PEEK_HEAD + METADATA_PEEK_TAIL
DISCOVERY_NODE_COUNT = 8
DISCOVERY_NODE_TEXT_BYTES = 8192
JSONL_RECORDS_PER_FILE = 4096
JSONL_RECORDS_PER_CALL = 16384
SQL_METADATA_BYTES = 16384
DIR_ENTRIES_PER_CALL = 16384

VERIFIED_AGENTS = ("claude", "codex", "devin", "pi", "opencode", "gemini", "copilot")
UNVERIFIED_AGENTS = ("cursor",)
AGENTS = ("claude", "codex", "devin", "pi", "cursor", "opencode", "gemini", "copilot")
UNVERIFIED_REASON = "project-mapping-unverified"
UNVERIFIED_LOCATIONS = {
    "cursor": (
        "Library/Application Support/Cursor/User/workspaceStorage",
        ".cursor/projects",
        ".cursor/chats",
    ),
}

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SESSION_FILE_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}\.jsonl$")

REDACTED = "[REDACTED]"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+")
_SECRET_QUERY_RE = re.compile(r"(?i)([?&](?:key|token|secret|password|access_token|sig|signature)=)[^&\s]+")
_AUTH_HEADER_RE = re.compile(r"(?i)\bauthorization\s*:\s*(?:bearer|basic)\s+[^\s\"']+")
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|$)",
    re.S,
)
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)([\"']?\b(?:api[_-]?key|secret|token|password|passwd|authorization)[\"']?"
    r"\s*[:=]\s*)(\[REDACTED\]|\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|[^\s,\"'}\]]+)"
)
_SECRET_RES = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S),
    re.compile(r"://[^\s/:@]{1,128}:[^\s/@]{1,128}@"),
)
_PATH_TOKEN_RE = re.compile(r"(?<![\w/])/(?:[^\s/]+/)+[^\s]*")
_WS_RE = re.compile(r"\s+")


def valid_session_id(value) -> bool:
    return isinstance(value, str) and bool(SESSION_ID_RE.match(value))


class AdapterError(Exception):
    def __init__(self, status: str, reason_code: str, message: str = ""):
        super().__init__(message or reason_code)
        self.status = status
        self.reason_code = reason_code


class Budget:
    __slots__ = ("remaining", "parent")

    def __init__(self, total: int, parent=None):
        self.remaining = total
        self.parent = parent

    def sub(self, cap: int) -> "Budget":
        return Budget(min(cap, self.remaining), parent=self)

    def take(self, amount: int) -> int:
        granted = max(0, min(amount, self.remaining))
        if self.parent is not None:
            granted = self.parent.take(granted)
        self.remaining -= granted
        return granted


@dataclass
class ScanContext:
    home: Path
    accepts: object
    relation: object
    bucket_paths: tuple
    deadline: float
    explicit_home: bool = False
    excluded: frozenset = frozenset()
    trust_anchor: Path | None = None
    target_session_id: str | None = None
    project_path: object = None
    counters: dict = field(default_factory=lambda: {"records": 0, "entries": 0})
    budget: Budget = field(default_factory=lambda: Budget(TOTAL_DECODE_CAP))

    def expired(self) -> bool:
        return time.monotonic() > self.deadline

    def for_provider(self, excluded=frozenset()) -> "ScanContext":
        return replace(
            self,
            budget=self.budget.sub(DISCOVERY_PROVIDER_BYTES),
            excluded=excluded,
        )


def source_entry(agent: str, status: str, reason_code: str, complete: bool) -> dict:
    return {"agent": agent, "status": status, "reason_code": reason_code, "complete": complete}


def _source(agent: str) -> dict:
    return {"status": "ok", "reason_code": "ok", "complete": True, "flags": set(), "candidates": []}


def _finish(src: dict) -> dict:
    flags = src.pop("flags")
    if flags:
        src["complete"] = False
        if src["reason_code"] == "ok":
            src["reason_code"] = sorted(flags)[0]
        if src["status"] == "ok":
            src["status"] = "partial"
    if src["status"] in ("unsupported", "unreadable"):
        src["complete"] = False
    return src


def _norm_ts(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        epoch = value / 1000.0 if value > 1e11 else float(value)
        try:
            return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def sanitize_text(text: str) -> str:
    out = _OSC_RE.sub("", text)
    out = _ANSI_RE.sub("", out)
    out = _CONTROL_RE.sub("", out)
    out = _PRIVATE_KEY_RE.sub(REDACTED, out)
    out = _AUTH_HEADER_RE.sub(REDACTED, out)
    out = _SECRET_QUERY_RE.sub(lambda m: m.group(1) + REDACTED, out)
    for rx in _SECRET_RES:
        out = rx.sub(REDACTED, out)

    def _mask_assign(match):
        value = match.group(2)
        quote = '"' if value.startswith('"') else ("'" if value.startswith("'") else "")
        return match.group(1) + quote + REDACTED + quote

    out = _SECRET_ASSIGN_RE.sub(_mask_assign, out)
    return out


def store_path_spans(ctx: ScanContext) -> list:
    spans = []
    stores = [
        ".claude/projects",
        ".codex",
        ".local/share/devin/cli",
        ".pi/agent/sessions",
        ".local/share/opencode",
        ".gemini/tmp",
        ".cache/.gemini/tmp",
        ".copilot/session-state",
    ]
    for store in stores:
        spans.append(str(ctx.home / store))
        spans.append("~/" + store)
    if not ctx.explicit_home:
        override = os.environ.get("PI_CODING_AGENT_DIR")
        if override:
            spans.append(str(Path(override) / "sessions"))
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            spans.append(str(Path(xdg) / "opencode"))
        gem = os.environ.get("GEMINI_CLI_HOME")
        if gem:
            spans.append(str(Path(gem) / ".gemini" / "tmp"))
            spans.append(str(Path(gem) / ".cache" / ".gemini" / "tmp"))
        copilot = os.environ.get("COPILOT_HOME")
        if copilot:
            spans.append(str(Path(copilot) / "session-state"))
    try:
        resolved = ctx.home.resolve()
        for store in stores:
            spans.append(str(resolved / store))
    except OSError:
        pass
    return [span for span in spans if span]


def mask_store_paths(text: str, ctx: ScanContext) -> str:
    out = text
    for span in sorted(store_path_spans(ctx), key=len, reverse=True):
        start = 0
        while True:
            index = out.find(span, start)
            if index < 0:
                break
            end = index + len(span)
            if end == len(out) or out[end].isspace() or out[end] in "/\\'\",;:)]}>":
                out = out[:index] + "[LOCAL_STORE]" + out[end:]
                start = index + len("[LOCAL_STORE]")
            else:
                start = end
    return out


def sanitize_title(raw, ctx=None) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    if ctx is not None:
        raw = mask_store_paths(raw, ctx)
    text = _WS_RE.sub(" ", _CONTROL_RE.sub(" ", _ANSI_RE.sub("", raw))).strip()
    text = _PATH_TOKEN_RE.sub(REDACTED, text)
    return sanitize_text(text)[:160]


def extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") in ("text", "input_text", "output_text")
            and isinstance(block.get("text"), str)
            and block["text"]
        ]
        return "\n".join(parts)
    return ""


def trusted_regular_file(path: Path, anchor: Path) -> bool:
    try:
        info = os.lstat(anchor)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return False
    norm_root = Path(os.path.normpath(str(anchor)))
    norm_path = Path(os.path.normpath(str(path)))
    try:
        relative = norm_path.relative_to(norm_root)
    except ValueError:
        return False
    try:
        root_resolved = anchor.resolve()
        resolved = path.resolve()
    except OSError:
        return False
    if resolved != root_resolved and root_resolved not in resolved.parents:
        return False
    current = norm_root
    for part in relative.parts[:-1]:
        current = current / part
        try:
            info = os.lstat(current)
        except OSError:
            return False
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return False
    try:
        leaf = os.lstat(norm_path)
    except OSError:
        return False
    return stat.S_ISREG(leaf.st_mode)


def _anchor(ctx: ScanContext, fallback: Path) -> Path:
    return ctx.trust_anchor if ctx.trust_anchor is not None else fallback


def trusted_directory(directory: Path, anchor: Path) -> bool:
    try:
        info = os.lstat(anchor)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return False
    norm_root = Path(os.path.normpath(str(anchor)))
    norm_path = Path(os.path.normpath(str(directory)))
    try:
        relative = norm_path.relative_to(norm_root)
    except ValueError:
        return False
    current = norm_root
    for part in relative.parts:
        current = current / part
        try:
            info = os.lstat(current)
        except OSError:
            return False
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return False
    return True


def _iter_entries(directory: Path, cap: int, ctx: ScanContext, flags: set, anchor: Path):
    try:
        info = os.lstat(directory)
    except OSError:
        flags.add("unreadable-dir")
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        flags.add("untrusted-dir")
        return
    if not trusted_directory(directory, anchor):
        flags.add("untrusted-dir")
        return
    try:
        iterator = os.scandir(directory)
    except OSError:
        flags.add("unreadable-dir")
        return
    count = 0
    with iterator as entries:
        for entry in entries:
            ctx.counters["entries"] += 1
            if ctx.counters["entries"] > DIR_ENTRIES_PER_CALL:
                flags.add("entry-cap")
                return
            count += 1
            if count > cap:
                flags.add("row-cap")
                return
            yield entry


def _session_files(directory: Path, ctx: ScanContext, flags: set, anchor: Path) -> list:
    files = []
    for entry in _iter_entries(directory, DIR_LISTING_CAP, ctx, flags, anchor):
        if not SESSION_FILE_RE.match(entry.name) and not entry.name.endswith(".jsonl"):
            continue
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        files.append((info.st_mtime, Path(entry.path)))
    files.sort(key=lambda pair: pair[0], reverse=True)
    return [path for _, path in files]


def _pread(descriptor: int, count: int, offset: int) -> bytes:
    if hasattr(os, "pread"):
        return os.pread(descriptor, count, offset)
    current = os.lseek(descriptor, 0, os.SEEK_CUR)
    try:
        os.lseek(descriptor, offset, os.SEEK_SET)
        return os.read(descriptor, count)
    finally:
        os.lseek(descriptor, current, os.SEEK_SET)


def _jsonl_records(
    path: Path,
    ctx: ScanContext,
    *,
    whole_max: int = JSONL_WHOLE_MAX,
    head_bytes: int = JSONL_HEAD_BYTES,
    tail_bytes: int = JSONL_TAIL_BYTES,
):
    flags: set = set()
    records: list = []
    with open_regular_read(path, max_bytes=None) as descriptor:
        size = os.fstat(descriptor).st_size
        chunks = []
        if size <= whole_max:
            granted = ctx.budget.take(size)
            chunks.append((_pread(descriptor, granted, 0), False))
            if granted < size:
                flags.add("byte-cap")
        else:
            flags.add("file-windowed")
            head = ctx.budget.take(head_bytes)
            chunks.append((_pread(descriptor, head, 0), False))
            tail = ctx.budget.take(min(tail_bytes, size - head))
            if tail:
                chunks.append((_pread(descriptor, tail, size - tail), True))
            if head < head_bytes or tail < min(tail_bytes, size - head):
                flags.add("byte-cap")
    per_file = 0
    multi_chunk = len(chunks) > 1
    for data, is_tail_fragment in chunks:
        segment = "tail" if is_tail_fragment else ("head" if multi_chunk else "whole")
        lines = data.split(b"\n")
        if is_tail_fragment:
            lines = lines[1:]
        for raw in lines:
            if not raw.strip():
                continue
            per_file += 1
            ctx.counters["records"] += 1
            if per_file > JSONL_RECORDS_PER_FILE or ctx.counters["records"] > JSONL_RECORDS_PER_CALL:
                flags.add("record-count")
                return records, flags
            if len(raw) > JSONL_RECORD_CAP:
                flags.add("record-cap")
                continue
            try:
                record = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                flags.add("record-parse")
                continue
            if isinstance(record, dict):
                record["_seg"] = segment
                records.append(record)
            else:
                flags.add("record-parse")
    return records, flags


def _probe_records(path: Path, ctx: ScanContext):
    return _jsonl_records(
        path,
        ctx,
        whole_max=METADATA_PEEK_WHOLE,
        head_bytes=METADATA_PEEK_HEAD,
        tail_bytes=METADATA_PEEK_TAIL,
    )


def _first_json_record(path: Path, ctx: ScanContext):
    chunk = 8192
    buffered = b""
    offset = 0
    exhausted = False
    with open_regular_read(path, max_bytes=None) as descriptor:
        size = os.fstat(descriptor).st_size
        while offset < min(JSONL_RECORD_CAP, size):
            granted = ctx.budget.take(min(chunk, JSONL_RECORD_CAP - offset, size - offset))
            if granted <= 0:
                exhausted = True
                break
            piece = _pread(descriptor, granted, offset)
            offset += granted
            newline = piece.find(b"\n")
            if newline >= 0:
                buffered += piece[:newline]
                break
            buffered += piece
    line = buffered
    if exhausted:
        return None, "byte-cap"
    if not line:
        return None, "header-missing"
    if len(line) > JSONL_RECORD_CAP:
        return None, "record-cap"
    try:
        record = json.loads(line)
    except ValueError:
        return None, "record-parse"
    return (record, None) if isinstance(record, dict) else (None, "record-parse")


def _encode_project_dir(path_str: str) -> str:
    return "".join(c if c.isalnum() or c in "_-" else "-" for c in path_str)


def _has_user_finalize(found: bool, flags: set):
    if found:
        return True
    return False if not flags else None


def _trace_chain(nodes: dict, leaf_key, parent_of, flags: set, ctx: ScanContext, cap: int) -> list:
    order = []
    seen = set()
    current = leaf_key
    while current is not None:
        if ctx.expired():
            flags.add("deadline")
            break
        if current in seen:
            flags.add("graph-cycle")
            break
        if len(order) >= cap:
            flags.add("graph-cap")
            break
        seen.add(current)
        record = nodes.get(current)
        if record is None:
            flags.add("graph-gap")
            break
        order.append(record)
        current = parent_of(record)
    order.reverse()
    return order


# --------------------------------------------------------------------------- #
# SQLite read policy                                                          #
# --------------------------------------------------------------------------- #

_SQLITE_SIGNATURE = b"SQLite format 3\x00"


class _SqliteGuard:
    def __init__(self, db: Path, anchor: Path, ctx: ScanContext):
        self.db = db
        self.mode = None
        self.anchor = anchor
        self._snapshot = None
        self._wal = db.with_name(db.name + "-wal")
        self._shm = db.with_name(db.name + "-shm")
        self._journal = db.with_name(db.name + "-journal")
        if not trusted_regular_file(db, anchor):
            raise AdapterError("unreadable", "db-untrusted")
        try:
            with open_regular_read(db, max_bytes=None) as descriptor:
                header = _pread(descriptor, min(SQLITE_HEADER_BYTES, os.fstat(descriptor).st_size), 0)
        except SafeOpenError as exc:
            raise AdapterError("unreadable", "db-open-failed") from exc
        if len(header) < 20 or header[:16] != _SQLITE_SIGNATURE:
            raise AdapterError("unreadable", "db-signature")
        read_version, write_version = header[18], header[19]
        if read_version != write_version or read_version not in (1, 2):
            raise AdapterError("unsupported", "unknown-journal-mode")
        wal_state = self._sidecar_state(self._wal)
        shm_state = self._sidecar_state(self._shm)
        journal_state = self._sidecar_state(self._journal)
        if journal_state != "absent":
            raise AdapterError("unreadable", "journal-present")
        if read_version == 1:
            if wal_state != "absent" or shm_state != "absent":
                raise AdapterError("unreadable", "sidecar-ambiguous")
            self.mode = "ro"
        elif wal_state == "live":
            if shm_state != "live":
                raise AdapterError("unreadable", "wal-without-shm")
            if not os.access(self._wal, os.R_OK):
                raise AdapterError("unreadable", "wal-unreadable")
            shm_size = os.lstat(self._shm).st_size
            if shm_size <= 0 or shm_size % SHM_MULTIPLE != 0 or not os.access(self._shm, os.R_OK):
                raise AdapterError("unreadable", "shm-invalid")
            self.mode = "ro"
        elif wal_state == "absent" and shm_state == "absent":
            self.mode = "immutable"
            try:
                stat_info = os.stat(db)
                self._snapshot = (
                    stat_info.st_dev,
                    stat_info.st_ino,
                    stat_info.st_size,
                    stat_info.st_mtime_ns,
                    stat_info.st_ctime_ns,
                )
            except OSError as exc:
                raise AdapterError("unreadable", "db-stat-failed") from exc
        else:
            raise AdapterError("unreadable", "sidecar-ambiguous")

    @staticmethod
    def _sidecar_state(path: Path) -> str:
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return "absent"
        except OSError as exc:
            raise AdapterError("unreadable", "sidecar-stat-failed") from exc
        if not stat.S_ISREG(info.st_mode):
            return "invalid"
        return "live"

    def connect(self, ctx: ScanContext) -> sqlite3.Connection:
        if self.mode == "immutable":
            uri = "file:" + urllib.parse.quote(str(self.db)) + "?mode=ro&immutable=1"
        else:
            uri = "file:" + urllib.parse.quote(str(self.db)) + "?mode=ro"
        conn = None
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=SQL_CONNECT_TIMEOUT)
            conn.execute("PRAGMA query_only = ON")
            state = {"steps": 0}

            def _progress():
                state["steps"] += 1
                if ctx.expired() or state["steps"] * SQL_PROGRESS_INTERVAL > SQL_PROGRESS_LIMIT:
                    return 1
                return 0

            conn.set_progress_handler(_progress, SQL_PROGRESS_INTERVAL)
            conn.execute("BEGIN")
            encoding = conn.execute("PRAGMA encoding").fetchone()
            if not encoding or str(encoding[0]).lower().replace("-", "") != "utf8":
                raise AdapterError("unsupported", "non-utf8-encoding")
        except AdapterError:
            if conn is not None:
                conn.close()
            raise
        except sqlite3.Error as exc:
            if conn is not None:
                conn.close()
            raise AdapterError("unreadable", "db-open-failed") from exc
        return conn

    def recheck(self) -> bool:
        if self.mode != "immutable":
            return True
        try:
            stat_info = os.stat(self.db)
            snapshot = (
                stat_info.st_dev,
                stat_info.st_ino,
                stat_info.st_size,
                stat_info.st_mtime_ns,
                stat_info.st_ctime_ns,
            )
        except OSError:
            return False
        if snapshot != self._snapshot:
            return False
        for sidecar in (self._wal, self._shm, self._journal):
            try:
                os.lstat(sidecar)
            except FileNotFoundError:
                continue
            except OSError:
                return False
            return False
        return trusted_regular_file(self.db, self.anchor)


def _table_columns(conn: sqlite3.Connection, table: str) -> set:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _like_descendant(anchor: str) -> str:
    escaped = anchor.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "/%"


# --------------------------------------------------------------------------- #
# Frozen SQL recipes                                                          #
# --------------------------------------------------------------------------- #

CODEX_META_COLUMNS = (
    ("id", 128),
    ("rollout_path", 4096),
    ("cwd", 4096),
    ("title", 1024),
    ("git_branch", 1024),
    ("git_sha", 128),
    ("source", 4096),
    ("archived", None),
    ("updated_at", 64),
)
DEVIN_META_COLUMNS = (
    ("id", 128),
    ("working_directory", 4096),
    ("created_at", 64),
    ("last_activity_at", 64),
    ("title", 1024),
    ("main_chain_id", None),
)
PROVIDERS_SQL = {
    "codex": (CODEX_META_COLUMNS, "threads", "cwd", "updated_at", ""),
    "devin": (DEVIN_META_COLUMNS, "sessions", "working_directory", "last_activity_at", "hidden = 0"),
}
TIME_COLUMNS = {"updated_at", "created_at", "last_activity_at"}


def _guarded_fields(columns):
    fields, invalid = [], []
    for name, cap in columns:
        if name == "archived":
            valid = "typeof(archived) = 'integer' AND archived IN (0,1)"
        elif cap is None:
            valid = f"typeof({name}) IN ('integer','null')"
        else:
            types = "('text','integer','real','null')" if name in TIME_COLUMNS else "('text','null')"
            size = f"coalesce(length(CAST({name} AS BLOB)),0)"
            valid = f"typeof({name}) IN {types} AND {size} <= {cap}"
        fields.append(f"CASE WHEN {valid} THEN {name} ELSE NULL END")
        invalid.append(f"NOT ({valid})")
    return fields, invalid


def _metadata_query(agent, anchor_count, *, selected=False, excluded=False):
    assert agent in PROVIDERS_SQL
    assert 0 <= anchor_count <= 4
    assert anchor_count or selected
    assert not (selected and excluded)
    columns, table, directory, activity, base = PROVIDERS_SQL[agent]
    fields, invalid = _guarded_fields(columns)
    fields.append("(" + " OR ".join(invalid) + ") AS clipped")
    filters = [base] if base else []
    if anchor_count:
        clause = f"({directory} = ? OR {directory} LIKE ? ESCAPE '\\')"
        filters.append("(" + " OR ".join(clause for _ in range(anchor_count)) + ")")
    if selected:
        filters.append("id = ?")
    if excluded:
        filters.append("id != ?")
    where = " AND ".join(filters)
    return f"SELECT {','.join(fields)} FROM {table} WHERE {where} ORDER BY {activity} DESC LIMIT ?"


def _project_cwd_fallback_query(agent, *, excluded=False):
    assert agent in PROVIDERS_SQL
    _columns, table, directory, activity, base = PROVIDERS_SQL[agent]
    fields, invalid = _guarded_fields((("id", 128), (directory, 4096), (activity, 64)))
    fields.append("(" + " OR ".join(invalid) + ") AS clipped")
    filters = [base] if base else []
    if excluded:
        filters.append("id != ?")
    where = " WHERE " + " AND ".join(filters) if filters else ""
    return f"SELECT {','.join(fields)} FROM {table}{where} ORDER BY {activity} DESC LIMIT ?"


def _metadata_binds(anchors: list, excluded_id=None, selected_id=None) -> list:
    binds = []
    for anchor in anchors:
        binds.extend([anchor, _like_descendant(anchor)])
    if selected_id is not None:
        binds.append(selected_id)
    if excluded_id is not None:
        binds.append(excluded_id)
    return binds


DEVIN_NODE_SQL = (
    "SELECT "
    "CASE WHEN typeof(node_id) = 'integer' THEN node_id ELSE NULL END,"
    "CASE WHEN typeof(parent_node_id) IN ('integer','null') THEN parent_node_id ELSE NULL END,"
    "CASE WHEN typeof(chat_message) IN ('text','null') AND "
    "coalesce(length(CAST(chat_message AS BLOB)),0) <= ? THEN chat_message ELSE NULL END,"
    "CASE WHEN typeof(metadata) IN ('text','null') AND "
    "coalesce(length(CAST(metadata AS BLOB)),0) <= ? THEN metadata ELSE NULL END,"
    "coalesce(length(CAST(chat_message AS BLOB)),0),"
    "coalesce(length(CAST(metadata AS BLOB)),0),"
    "(typeof(node_id) != 'integer' OR typeof(parent_node_id) NOT IN ('integer','null')) AS pointer_clipped,"
    "(typeof(chat_message) NOT IN ('text','null') OR typeof(metadata) NOT IN ('text','null')) AS text_type_invalid "
    "FROM message_nodes WHERE session_id = ? AND node_id = ? LIMIT 1"
)

_CODEX_ROW_MAX = sum(cap for _, cap in CODEX_META_COLUMNS if cap) + 64
_DEVIN_ROW_MAX = sum(cap for _, cap in DEVIN_META_COLUMNS if cap) + 64


def _is_worker_source(value) -> bool:
    if value is None:
        return False
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    lowered = text.lower()
    if "subagent" in lowered:
        return True
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    return isinstance(parsed, dict) and "parent" in parsed


# --------------------------------------------------------------------------- #
# Shared candidate helpers                                                    #
# --------------------------------------------------------------------------- #

SOURCE_FORMATS = {
    "claude": "claude-jsonl",
    "codex": "codex-rollout-jsonl",
    "devin": "devin-sqlite-message-nodes",
    "pi": "pi-jsonl-v3",
    "opencode": "opencode-session-v2",
    "gemini": "gemini-jsonl",
    "copilot": "copilot-events-jsonl",
}


def _provenance(ctx: ScanContext, cwd, agent: str) -> dict:
    project_path = ctx.project_path(cwd) if callable(ctx.project_path) else None
    return {
        "project_path": project_path,
        "association": "native-cwd",
        "association_confidence": "verified",
        "source_format": SOURCE_FORMATS[agent],
    }


def _charge_row(row, ctx: ScanContext, flags: set) -> bool:
    total = 0
    for value in row:
        if isinstance(value, str):
            total += len(value.encode("utf-8"))
        elif value is not None:
            total += 16
    if ctx.budget.take(total) < total:
        flags.add("byte-cap")
        return False
    return True


def _metadata_limit(ctx: ScanContext, row_max: int, flags: set) -> int:
    remaining = ctx.budget.remaining
    if remaining < row_max:
        flags.add("byte-cap")
        return 0
    return min(METADATA_ROW_CAP + 1, remaining // row_max)


# --------------------------------------------------------------------------- #
# Claude Code                                                                 #
# --------------------------------------------------------------------------- #

_CLAUDE_TITLE_TYPES = {"title", "ai-title", "custom-title"}


class ClaudeAdapter:
    name = "claude"

    def _store(self, ctx: ScanContext) -> Path:
        return ctx.home / ".claude" / "projects"

    def _anchor(self, ctx: ScanContext) -> Path:
        return _anchor(ctx, ctx.home)

    def _buckets(self, ctx: ScanContext, src: dict) -> list:
        store = self._store(ctx)
        anchor = self._anchor(ctx)
        preferred = []
        seen = set()
        for base in ctx.bucket_paths:
            encoded = _encode_project_dir(base)
            bucket = store / encoded
            if encoded not in seen and bucket.is_dir() and not bucket.is_symlink():
                seen.add(encoded)
                preferred.append(bucket)
        others = []
        for entry in _iter_entries(store, DIR_LISTING_CAP, ctx, src["flags"], anchor):
            if entry.name in seen:
                continue
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if Path(entry.path).is_symlink():
                continue
            others.append(Path(entry.path))
        return preferred + others

    def discover(self, ctx: ScanContext) -> dict:
        src = _source(self.name)
        store = self._store(ctx)
        anchor = self._anchor(ctx)
        if store.is_symlink() or not store.is_dir() or not trusted_directory(store, anchor):
            if store.is_symlink() or store.exists():
                src["flags"].add("untrusted-dir")
                return _finish({**src, "status": "unreadable", "reason_code": "untrusted-store"})
            return _finish({**src, "status": "absent", "reason_code": "store-missing"})
        buckets = self._buckets(ctx, src)
        if not buckets:
            return _finish({**src, "status": "absent", "reason_code": "bucket-missing"})
        for bucket in buckets:
            for path in _session_files(bucket, ctx, src["flags"], anchor):
                if ctx.expired():
                    src["flags"].add("deadline")
                    break
                if path.stem in ctx.excluded:
                    continue
                if not trusted_regular_file(path, anchor):
                    src["flags"].add("untrusted-file")
                    continue
                candidate = self._probe_candidate(path, ctx, src)
                if candidate is not None:
                    src["candidates"].append(candidate)
        return _finish(src)

    def locate(self, ctx: ScanContext, session_id: str, src: dict):
        store = self._store(ctx)
        anchor = self._anchor(ctx)
        if not trusted_directory(store, anchor):
            return None
        for bucket in self._buckets(ctx, src):
            path = bucket / f"{session_id}.jsonl"
            if not trusted_regular_file(path, anchor):
                continue
            candidate = self._probe_candidate(path, ctx, src)
            if candidate is not None:
                return candidate
        return None

    def _probe_candidate(self, path, ctx, src):
        session_id = path.stem
        try:
            records, flags = _probe_records(path, ctx)
        except SafeOpenError:
            src["flags"].add("unreadable-file")
            return None
        src["flags"] |= flags
        cwds = set()
        updated_at = None
        title = None
        conflict = False
        leaf_pointer = None
        nodes = {}
        main = []
        for record in records:
            ts = record.get("timestamp")
            if isinstance(ts, str) and (updated_at is None or ts > updated_at):
                updated_at = ts
            if record.get("isSidechain"):
                continue
            sid = record.get("sessionId") or record.get("session_id")
            if isinstance(sid, str) and sid and sid != session_id:
                conflict = True
            cwd = record.get("cwd")
            if isinstance(cwd, str) and cwd:
                cwds.add(cwd)
            if record.get("type") in _CLAUDE_TITLE_TYPES:
                title = sanitize_title(record.get("title") or record.get("text"), ctx) or title
            if record.get("type") == "last-prompt":
                leaf_pointer = record.get("leafUuid")
            uuid = record.get("uuid")
            if isinstance(uuid, str):
                nodes[uuid] = record
            main.append(record)
        if conflict:
            src["flags"].add("session-conflict")
            return None
        if not cwds:
            src["flags"].add("scope-unverified")
            return None
        if not all(ctx.accepts(cwd) for cwd in cwds):
            return None
        if leaf_pointer is not None:
            leaf = leaf_pointer if leaf_pointer in nodes else None
            if leaf is None:
                flags.add("graph-gap")
        else:
            leaf = None
            for record in reversed(main):
                if record.get("type") in ("user", "assistant") and isinstance(record.get("uuid"), str):
                    leaf = record["uuid"]
                    break
        chain = _trace_chain(nodes, leaf, lambda r: r.get("parentUuid"), flags, ctx, PARENT_TRAVERSAL_CAP)
        has_user = any(record.get("uuid") and self._is_user_turn(record) for record in chain)
        meaningful = any(record.get("uuid") and self._exportable(record) for record in chain)
        src["flags"] |= flags
        if not meaningful and flags:
            meaningful = None
        if meaningful is False:
            return None
        return {
            "agent": self.name,
            "session_id": session_id,
            "title": title,
            "updated_at": _norm_ts(updated_at),
            "branch": (nodes.get(leaf) or {}).get("gitBranch"),
            "head": None,
            "relation": ctx.relation(sorted(cwds)[0]),
            "has_user": has_user or _has_user_finalize(False, flags),
            "complete": not flags,
            "_locator": {"path": str(path)},
            **_provenance(ctx, sorted(cwds)[0], self.name),
        }

    @staticmethod
    def _is_summary(record: dict) -> bool:
        return record.get("type") == "summary" or bool(record.get("isCompactSummary"))

    @staticmethod
    def _is_user_turn(record: dict) -> bool:
        if record.get("type") != "user" or record.get("isMeta"):
            return False
        message = record.get("message")
        if not isinstance(message, dict) or message.get("role") != "user":
            return False
        content = message.get("content")
        if isinstance(content, str):
            return bool(content.strip())
        if isinstance(content, list):
            return any(
                isinstance(b, dict) and b.get("type") == "text" and str(b.get("text", "")).strip() for b in content
            )
        return False

    @classmethod
    def _exportable(cls, record: dict) -> bool:
        if cls._is_summary(record):
            return True
        if record.get("type") == "user":
            return cls._is_user_turn(record)
        if record.get("type") == "assistant":
            message = record.get("message")
            return (
                isinstance(message, dict)
                and message.get("role") == "assistant"
                and bool(extract_text(message.get("content")))
            )
        return False

    def read_messages(self, ctx: ScanContext, candidate: dict) -> dict:
        anchor = self._anchor(ctx)
        target = Path(candidate["_locator"]["path"])
        if not trusted_regular_file(target, anchor):
            raise AdapterError("unavailable", "transcript-untrusted")
        try:
            records, flags = _jsonl_records(target, ctx)
        except SafeOpenError as exc:
            raise AdapterError("unavailable", "transcript-untrusted") from exc
        session_id = candidate["session_id"]
        nodes = {}
        cwds = set()
        leaf_pointer = None
        main = []
        for record in records:
            if record.get("isSidechain"):
                continue
            main.append(record)
            sid = record.get("sessionId") or record.get("session_id")
            if isinstance(sid, str) and sid and sid != session_id:
                raise AdapterError("refused", "session-conflict")
            cwd = record.get("cwd")
            if isinstance(cwd, str) and cwd:
                cwds.add(cwd)
            if record.get("type") == "last-prompt":
                leaf_pointer = record.get("leafUuid")
            uuid = record.get("uuid")
            if isinstance(uuid, str):
                nodes[uuid] = record
        if not cwds or not all(ctx.accepts(cwd) for cwd in cwds):
            raise AdapterError("refused", "scope-mismatch")
        if leaf_pointer is not None:
            if leaf_pointer in nodes:
                leaf = leaf_pointer
            else:
                flags.add("graph-gap")
                leaf = None
        else:
            leaf = None
            for record in reversed(main):
                if record.get("type") in ("user", "assistant") and isinstance(record.get("uuid"), str):
                    leaf = record["uuid"]
                    break
        chain = _trace_chain(nodes, leaf, lambda r: r.get("parentUuid"), flags, ctx, PARENT_TRAVERSAL_CAP)
        chain_ids = {record.get("uuid") for record in chain}
        branch = None
        if leaf is not None:
            leaf_record = nodes.get(leaf) or {}
            branch = leaf_record.get("gitBranch")
        messages = []
        for record in main:
            if record.get("type") == "summary" and record.get("uuid") not in chain_ids:
                if record.get("leafUuid") in chain_ids:
                    text = record.get("summary")
                    if isinstance(text, str) and text:
                        messages.append({"role": "summary", "text": text, "record_id": ""})
                else:
                    flags.add("summary-ambiguous")
        for record in chain:
            exported = self._export(record)
            if exported is not None:
                role, text = exported
                messages.append({"role": role, "text": text, "record_id": str(record.get("uuid") or "")})
        for message in messages:
            message["text"] = sanitize_text(message["text"])
        candidate["branch"] = branch
        truncation = sorted(flags)[0] if flags else None
        return {
            "messages": messages,
            "complete": not flags,
            "truncation": truncation,
            "truncation_reasons": sorted(flags),
        }

    @classmethod
    def _export(cls, record: dict):
        if cls._is_summary(record):
            text = record.get("summary") or extract_text(
                (record.get("message") or {}).get("content") if isinstance(record.get("message"), dict) else None
            )
            return ("summary", text) if text else None
        if record.get("type") == "user" and cls._is_user_turn(record):
            message = record.get("message") or {}
            return ("user", extract_text(message.get("content")))
        if record.get("type") == "assistant":
            message = record.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                text = extract_text(message.get("content"))
                return ("assistant", text) if text else None
        return None


# --------------------------------------------------------------------------- #
# Codex                                                                       #
# --------------------------------------------------------------------------- #

_CODEX_META_COLUMNS = {
    "id",
    "rollout_path",
    "cwd",
    "title",
    "git_branch",
    "git_sha",
    "source",
    "archived",
    "updated_at",
}
_CODEX_META_ROOT_TYPES = {
    "session_meta",
    "event_msg",
    "turn_context",
    "token_usage_record",
    "world_state",
    "inter_agent_communication_metadata",
}
_CODEX_ITEM_TYPES = {
    "message",
    "reasoning",
    "function_call",
    "function_call_output",
    "local_shell_call",
    "custom_tool_call",
    "custom_tool_call_output",
    "web_search_call",
    "ghost_snapshot",
    "turn_context",
    "token_count",
    "additional_tools",
    "configuration_update",
    "agent_message",
}
_CODEX_REWRITE_EVENTS = {"rollback", "rolled_back", "rewind", "clear", "thread_rolled_back"}


class CodexAdapter:
    name = "codex"

    def _store(self, ctx: ScanContext) -> Path:
        return ctx.home / ".codex"

    def _anchor(self, ctx: ScanContext) -> Path:
        return _anchor(ctx, ctx.home)

    def discover(self, ctx: ScanContext) -> dict:
        src = _source(self.name)
        store = self._store(ctx)
        anchor = self._anchor(ctx)
        if store.is_symlink() or not store.is_dir() or not trusted_directory(store, anchor):
            if store.is_symlink() or store.exists():
                src["flags"].add("untrusted-dir")
                return _finish({**src, "status": "unreadable", "reason_code": "untrusted-store"})
            return _finish({**src, "status": "absent", "reason_code": "store-missing"})
        db = store / "state_5.sqlite"
        sessions_dir = store / "sessions"
        if not db.exists():
            if not sessions_dir.is_dir() or sessions_dir.is_symlink():
                return _finish({**src, "status": "absent", "reason_code": "store-missing"})
            src["flags"].add("index-missing")
            self._fallback_scan(sessions_dir, ctx, src)
            return _finish(src)
        guard = _SqliteGuard(db, anchor, ctx)
        conn = guard.connect(ctx)
        try:
            if not _CODEX_META_COLUMNS <= _table_columns(conn, "threads"):
                return _finish({**src, "status": "unsupported", "reason_code": "unknown-schema"})
            anchors = list(dict.fromkeys(ctx.bucket_paths))[:4]
            excluded_id = next(iter(ctx.excluded), None)
            sql = _metadata_query("codex", len(anchors), excluded=excluded_id is not None)
            binds = _metadata_binds(anchors, excluded_id=excluded_id)
            limit = _metadata_limit(ctx, _CODEX_ROW_MAX, src["flags"])
            rows = []
            if limit:
                binds.append(limit)
                try:
                    rows = conn.execute(sql, binds).fetchall()
                except sqlite3.Error:
                    src["flags"].add("sql-aborted")
            if limit and len(rows) >= limit:
                src["flags"].add("row-cap")
            for row in rows:
                if not _charge_row(row, ctx, src["flags"]):
                    break
            rows = rows[:METADATA_ROW_CAP]
            seen_ids = set()
            for row in rows:
                if ctx.expired():
                    src["flags"].add("deadline")
                    break
                if isinstance(row[0], str):
                    seen_ids.add(row[0])
                candidate = self._row_candidate(row, store, ctx, src)
                if candidate is not None:
                    src["candidates"].append(candidate)
            self._alias_candidates(conn, seen_ids, ctx, src, store)
            conn.rollback()
            if not guard.recheck():
                src["candidates"] = []
                src["flags"].add("concurrent-change")
        finally:
            conn.close()
        return _finish(src)

    def _alias_candidates(self, conn, seen_ids: set, ctx, src, store):
        limit = _metadata_limit(ctx, _CODEX_ROW_MAX, src["flags"])
        if not limit:
            return
        excluded_id = next(iter(ctx.excluded), None)
        sql = _project_cwd_fallback_query("codex", excluded=excluded_id is not None)
        binds = [excluded_id] if excluded_id is not None else []
        binds.append(limit)
        try:
            rows = conn.execute(sql, binds).fetchall()
        except sqlite3.Error:
            src["flags"].add("sql-aborted")
            return
        if len(rows) >= limit:
            src["flags"].add("row-cap")
        for row in rows:
            if not _charge_row(row, ctx, src["flags"]):
                break
        for row in rows:
            if ctx.expired():
                src["flags"].add("deadline")
                break
            session_id, cwd, _act, _clipped = row
            if not valid_session_id(session_id) or session_id in seen_ids or not isinstance(cwd, str):
                continue
            if not ctx.accepts(cwd):
                continue
            if ctx.budget.remaining < _CODEX_ROW_MAX:
                src["flags"].add("byte-cap")
                break
            sql = _metadata_query("codex", 0, selected=True)
            binds = _metadata_binds([], selected_id=session_id)
            binds.append(1)
            try:
                selected = conn.execute(sql, binds).fetchall()
            except sqlite3.Error:
                src["flags"].add("sql-aborted")
                break
            if not selected:
                continue
            seen_ids.add(session_id)
            row = selected[0]
            if not _charge_row(row, ctx, src["flags"]):
                break
            candidate = self._row_candidate(row, store, ctx, src)
            if candidate is not None:
                src["candidates"].append(candidate)

    def _fallback_scan(self, sessions_dir: Path, ctx: ScanContext, src: dict):
        anchor = self._anchor(ctx)
        files = []
        for year in _iter_entries(sessions_dir, DIR_LISTING_CAP, ctx, src["flags"], anchor):
            if not year.is_dir(follow_symlinks=False) or not year.name.isdigit():
                continue
            for month in _iter_entries(Path(year.path), DIR_LISTING_CAP, ctx, src["flags"], anchor):
                if not month.is_dir(follow_symlinks=False) or not month.name.isdigit():
                    continue
                for day in _iter_entries(Path(month.path), DIR_LISTING_CAP, ctx, src["flags"], anchor):
                    if not day.is_dir(follow_symlinks=False) or not day.name.isdigit():
                        continue
                    files.extend(_session_files(Path(day.path), ctx, src["flags"], anchor))
        for path in files:
            if ctx.expired():
                src["flags"].add("deadline")
                break
            if any(path.stem == ex or path.stem.endswith("-" + ex) for ex in ctx.excluded):
                continue
            if not trusted_regular_file(path, anchor):
                src["flags"].add("untrusted-file")
                continue
            try:
                records, flags = _probe_records(path, ctx)
            except SafeOpenError:
                src["flags"].add("unreadable-file")
                continue
            src["flags"] |= flags
            meta = self._session_meta(records)
            if meta is None:
                continue
            cwd = meta.get("cwd")
            if not isinstance(cwd, str) or not ctx.accepts(cwd):
                continue
            if _is_worker_source(meta.get("thread_source")) or _is_worker_source(meta.get("source")):
                continue
            session_id = meta.get("id") or meta.get("session_id")
            if not valid_session_id(session_id) or session_id in ctx.excluded:
                continue
            if not self._preflight(records, session_id, cwd, ctx):
                src["flags"].add("meta-mismatch")
                continue
            projected, pflags = self._project(records, session_id)
            local_flags = flags | pflags
            src["flags"] |= local_flags
            has_user = any(m["role"] == "user" for m in projected)
            meaningful = bool(projected)
            if not meaningful and local_flags:
                has_user = None
            elif not meaningful:
                continue
            src["candidates"].append(
                {
                    "agent": self.name,
                    "session_id": session_id,
                    "title": None,
                    "updated_at": _norm_ts(meta.get("timestamp")),
                    "branch": None,
                    "head": None,
                    "relation": ctx.relation(cwd),
                    "has_user": has_user or _has_user_finalize(False, local_flags),
                    "complete": not local_flags,
                    "_locator": {"path": str(path), "expected_cwd": cwd},
                    **_provenance(ctx, cwd, self.name),
                }
            )

    def locate(self, ctx: ScanContext, session_id: str, src: dict):
        store = self._store(ctx)
        anchor = self._anchor(ctx)
        db = store / "state_5.sqlite"
        if not db.exists() or not trusted_directory(store, anchor):
            return None
        guard = _SqliteGuard(db, anchor, ctx)
        conn = guard.connect(ctx)
        try:
            if not _CODEX_META_COLUMNS <= _table_columns(conn, "threads"):
                src["flags"].add("unknown-schema")
                return None
            if ctx.budget.remaining < _CODEX_ROW_MAX:
                src["flags"].add("byte-cap")
                return None
            sql = _metadata_query("codex", 0, selected=True)
            binds = _metadata_binds([], selected_id=session_id)
            binds.append(1)
            try:
                rows = conn.execute(sql, binds).fetchall()
            except sqlite3.Error:
                src["flags"].add("sql-aborted")
                return None
            if not rows:
                return None
            row = rows[0]
            _charge_row(row, ctx, src["flags"])
            candidate = self._row_candidate(row, store, ctx, src)
            conn.rollback()
            if not guard.recheck():
                src["flags"].add("concurrent-change")
                return None
            return candidate
        finally:
            conn.close()

    def _row_candidate(self, row, store: Path, ctx: ScanContext, src: dict):
        (
            session_id,
            rollout_path,
            cwd,
            title,
            git_branch,
            git_sha,
            source,
            archived,
            updated_at,
            clipped,
        ) = row
        if clipped:
            src["flags"].add("metadata-clipped")
        if not valid_session_id(session_id) or not isinstance(cwd, str):
            return None
        if not ctx.accepts(cwd):
            return None
        if _is_worker_source(source):
            return None
        if not isinstance(rollout_path, str):
            src["flags"].add("path-outside-store")
            return None
        anchor = self._anchor(ctx)
        allowed = store / "archived_sessions" if archived else store / "sessions"
        rollout = Path(rollout_path)
        try:
            Path(os.path.normpath(str(rollout))).relative_to(Path(os.path.normpath(str(allowed))))
        except ValueError:
            src["flags"].add("path-outside-store")
            return None
        if not rollout.is_absolute() or not trusted_regular_file(rollout, anchor):
            src["flags"].add("path-outside-store")
            return None
        try:
            records, flags = _probe_records(rollout, ctx)
        except SafeOpenError:
            src["flags"].add("unreadable-file")
            return None
        src["flags"] |= flags
        if not self._preflight(records, session_id, cwd, ctx):
            src["flags"].add("meta-mismatch")
            return None
        projected, pflags = self._project(records, session_id)
        local_flags = flags | pflags
        src["flags"] |= local_flags
        has_user = any(m["role"] == "user" for m in projected)
        meaningful = bool(projected)
        if not meaningful and local_flags:
            has_user = None
        elif not meaningful:
            return None
        return {
            "agent": self.name,
            "session_id": session_id,
            "title": sanitize_title(title, ctx),
            "updated_at": _norm_ts(updated_at),
            "branch": git_branch if isinstance(git_branch, str) else None,
            "head": git_sha if isinstance(git_sha, str) else None,
            "relation": ctx.relation(cwd),
            "has_user": has_user or _has_user_finalize(False, local_flags),
            "complete": not local_flags,
            "_locator": {"path": str(rollout), "expected_cwd": cwd},
            **_provenance(ctx, cwd, self.name),
        }

    @staticmethod
    def _project(records, session_id):
        flags: set = set()
        messages = []
        index = 0
        windowed = any(r.get("_seg") == "tail" for r in records)
        for record in records:
            if windowed and record.get("_seg") != "tail":
                continue
            kind = record.get("type")
            if kind == "response_item":
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    flags.add("record-shape")
                    messages = []
                    continue
                sub = payload.get("type")
                if sub == "message":
                    role = payload.get("role")
                    content = payload.get("content")
                    if role in ("user", "assistant"):
                        if content is not None and not isinstance(content, (str, list)):
                            flags.add("record-shape")
                            messages = []
                            continue
                        text = extract_text(content)
                        if text:
                            messages.append({"role": role, "text": text, "record_id": f"{session_id}:{index}"})
                elif sub in _CODEX_ITEM_TYPES:
                    continue
                else:
                    flags.add("unhandled-record")
                    messages = []
            elif kind == "compacted":
                messages = []
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    flags.add("record-shape")
                    index += 1
                    continue
                summary = payload.get("message")
                replacement = payload.get("replacement_history")
                if isinstance(replacement, list):
                    for item in replacement:
                        if not isinstance(item, dict) or item.get("type") != "message":
                            flags.add("record-shape")
                            continue
                        role = item.get("role")
                        content = item.get("content")
                        if role not in ("user", "assistant"):
                            continue
                        if content is not None and not isinstance(content, (str, list)):
                            flags.add("record-shape")
                            continue
                        text = extract_text(content)
                        if text:
                            messages.append({"role": role, "text": text, "record_id": f"{session_id}:{index}"})
                    if isinstance(summary, str) and summary.strip():
                        messages.append({"role": "summary", "text": summary, "record_id": f"{session_id}:{index}"})
                else:
                    flags.add("compaction-shape")
                    if isinstance(summary, str) and summary.strip():
                        messages.append({"role": "summary", "text": summary, "record_id": f"{session_id}:{index}"})
            elif kind == "event_msg":
                payload = record.get("payload")
                if isinstance(payload, dict) and payload.get("type") in _CODEX_REWRITE_EVENTS:
                    flags.add("unsupported-control")
                    messages = []
                continue
            elif kind in _CODEX_META_ROOT_TYPES:
                continue
            else:
                flags.add("unhandled-record")
                messages = []
            index += 1
        if windowed:
            flags.add("history-gap")
        return messages, flags

    @staticmethod
    def _preflight(records, session_id, expected_cwd, ctx) -> bool:
        seen = False
        for record in records:
            if record.get("type") != "session_meta":
                continue
            meta = record.get("payload")
            if not isinstance(meta, dict):
                return False
            if not isinstance(meta.get("cwd"), str):
                return False
            if (meta.get("id") or meta.get("session_id")) != session_id:
                return False
            meta_cwd = meta.get("cwd")
            try:
                same = isinstance(meta_cwd, str) and Path(meta_cwd).resolve() == Path(expected_cwd).resolve()
            except OSError:
                same = False
            if not same or not ctx.accepts(meta_cwd):
                return False
            if _is_worker_source(meta.get("thread_source")) or _is_worker_source(meta.get("source")):
                return False
            roots = meta.get("runtime_workspace_roots")
            if roots is not None:
                if not isinstance(roots, list):
                    return False
                for entry in roots:
                    if not isinstance(entry, str) or not ctx.accepts(entry):
                        return False
            seen = True
        if not seen:
            return False
        for record in records:
            if record.get("type") == "turn_context":
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    return False
                turn_cwd = payload.get("cwd")
                if "cwd" in payload and (not isinstance(turn_cwd, str) or not ctx.accepts(turn_cwd)):
                    return False
        return True

    @staticmethod
    def _session_meta(records: list):
        for record in records:
            if record.get("type") == "session_meta":
                payload = record.get("payload")
                return payload if isinstance(payload, dict) else None
        return None

    def read_messages(self, ctx: ScanContext, candidate: dict) -> dict:
        locator = candidate["_locator"]
        path = Path(locator["path"])
        anchor = self._anchor(ctx)
        store = self._store(ctx)
        allowed = store / "archived_sessions" if "archived_sessions" in path.parts else store / "sessions"
        try:
            Path(os.path.normpath(str(path))).relative_to(Path(os.path.normpath(str(allowed))))
        except ValueError:
            raise AdapterError("unavailable", "path-outside-store") from None
        if not trusted_regular_file(path, anchor):
            raise AdapterError("unavailable", "transcript-untrusted")
        try:
            records, flags = _jsonl_records(path, ctx)
        except SafeOpenError as exc:
            raise AdapterError("unavailable", "transcript-untrusted") from exc
        if not self._preflight(records, candidate["session_id"], locator["expected_cwd"], ctx):
            raise AdapterError("refused", "meta-mismatch")
        projected, pflags = self._project(records, candidate["session_id"])
        flags |= pflags
        for message in projected:
            message["text"] = sanitize_text(message["text"])
        truncation = sorted(flags)[0] if flags else None
        return {
            "messages": projected,
            "complete": not flags,
            "truncation": truncation,
            "truncation_reasons": sorted(flags),
        }


# --------------------------------------------------------------------------- #
# Devin                                                                       #
# --------------------------------------------------------------------------- #

_DEVIN_SESSION_COLUMNS = {
    "id",
    "working_directory",
    "created_at",
    "last_activity_at",
    "title",
    "main_chain_id",
    "hidden",
}
_DEVIN_NODE_COLUMNS = {"session_id", "node_id", "parent_node_id", "chat_message", "metadata"}


class DevinAdapter:
    name = "devin"

    def _store(self, ctx: ScanContext) -> Path:
        return ctx.home / ".local" / "share" / "devin" / "cli"

    def _anchor(self, ctx: ScanContext) -> Path:
        return _anchor(ctx, ctx.home)

    def _db(self, ctx: ScanContext) -> Path:
        return self._store(ctx) / "sessions.db"

    def _open(self, ctx: ScanContext):
        anchor = self._anchor(ctx)
        guard = _SqliteGuard(self._db(ctx), anchor, ctx)
        return guard, guard.connect(ctx)

    def discover(self, ctx: ScanContext) -> dict:
        src = _source(self.name)
        db = self._db(ctx)
        anchor = self._anchor(ctx)
        if db.is_symlink():
            src["flags"].add("untrusted-file")
            return _finish({**src, "status": "unreadable", "reason_code": "untrusted-store"})
        if not db.exists():
            return _finish({**src, "status": "absent", "reason_code": "store-missing"})
        if not trusted_regular_file(db, anchor):
            src["flags"].add("untrusted-file")
            return _finish({**src, "status": "unreadable", "reason_code": "untrusted-store"})
        guard, conn = self._open(ctx)
        try:
            if not _DEVIN_SESSION_COLUMNS <= _table_columns(conn, "sessions"):
                return _finish({**src, "status": "unsupported", "reason_code": "unknown-schema"})
            if not _DEVIN_NODE_COLUMNS <= _table_columns(conn, "message_nodes"):
                return _finish({**src, "status": "unsupported", "reason_code": "unknown-schema"})
            anchors = list(dict.fromkeys(ctx.bucket_paths))[:4]
            excluded_id = next(iter(ctx.excluded), None)
            sql = _metadata_query("devin", len(anchors), excluded=excluded_id is not None)
            binds = _metadata_binds(anchors, excluded_id=excluded_id)
            limit = _metadata_limit(ctx, _DEVIN_ROW_MAX, src["flags"])
            rows = []
            if limit:
                binds.append(limit)
                try:
                    rows = conn.execute(sql, binds).fetchall()
                except sqlite3.Error:
                    src["flags"].add("sql-aborted")
            if limit and len(rows) >= limit:
                src["flags"].add("row-cap")
            for row in rows:
                if not _charge_row(row, ctx, src["flags"]):
                    break
            rows = rows[:METADATA_ROW_CAP]
            seen_ids = set()
            for row in rows:
                if ctx.expired():
                    src["flags"].add("deadline")
                    break
                if isinstance(row[0], str):
                    seen_ids.add(row[0])
                candidate = self._row_candidate(conn, row, ctx, src)
                if candidate is not None:
                    src["candidates"].append(candidate)
            self._alias_candidates(conn, seen_ids, ctx, src)
            conn.rollback()
            if not guard.recheck():
                src["candidates"] = []
                src["flags"].add("concurrent-change")
        finally:
            conn.close()
        return _finish(src)

    def _alias_candidates(self, conn, seen_ids: set, ctx, src):
        limit = _metadata_limit(ctx, _DEVIN_ROW_MAX, src["flags"])
        if not limit:
            return
        excluded_id = next(iter(ctx.excluded), None)
        sql = _project_cwd_fallback_query("devin", excluded=excluded_id is not None)
        binds = [excluded_id] if excluded_id is not None else []
        binds.append(limit)
        try:
            rows = conn.execute(sql, binds).fetchall()
        except sqlite3.Error:
            src["flags"].add("sql-aborted")
            return
        if len(rows) >= limit:
            src["flags"].add("row-cap")
        for row in rows:
            if not _charge_row(row, ctx, src["flags"]):
                break
        for row in rows:
            if ctx.expired():
                src["flags"].add("deadline")
                break
            session_id, cwd, _act, _clipped = row
            if not valid_session_id(session_id) or session_id in seen_ids or not isinstance(cwd, str):
                continue
            if not ctx.accepts(cwd):
                continue
            if ctx.budget.remaining < _DEVIN_ROW_MAX:
                src["flags"].add("byte-cap")
                break
            sql = _metadata_query("devin", 0, selected=True)
            binds = _metadata_binds([], selected_id=session_id)
            binds.append(1)
            try:
                selected = conn.execute(sql, binds).fetchall()
            except sqlite3.Error:
                src["flags"].add("sql-aborted")
                break
            if not selected:
                continue
            seen_ids.add(session_id)
            row = selected[0]
            if not _charge_row(row, ctx, src["flags"]):
                break
            candidate = self._row_candidate(conn, row, ctx, src)
            if candidate is not None:
                src["candidates"].append(candidate)

    def locate(self, ctx: ScanContext, session_id: str, src: dict):
        db = self._db(ctx)
        anchor = self._anchor(ctx)
        if not db.exists() or not trusted_regular_file(db, anchor):
            return None
        guard, conn = self._open(ctx)
        try:
            if not _DEVIN_SESSION_COLUMNS <= _table_columns(conn, "sessions"):
                src["flags"].add("unknown-schema")
                return None
            if ctx.budget.remaining < _DEVIN_ROW_MAX:
                src["flags"].add("byte-cap")
                return None
            sql = _metadata_query("devin", 0, selected=True)
            binds = _metadata_binds([], selected_id=session_id)
            binds.append(1)
            try:
                rows = conn.execute(sql, binds).fetchall()
            except sqlite3.Error:
                src["flags"].add("sql-aborted")
                return None
            if not rows:
                return None
            row = rows[0]
            _charge_row(row, ctx, src["flags"])
            candidate = self._row_candidate(conn, row, ctx, src)
            conn.rollback()
            if not guard.recheck():
                src["flags"].add("concurrent-change")
                return None
            return candidate
        finally:
            conn.close()

    def _fetch_node(self, conn, session_id, node_id, ctx, chat_cap):
        if ctx.budget.remaining < 128:
            return "exhausted"
        ctx.budget.take(128)
        metadata_cap = min(SQL_METADATA_BYTES, ctx.budget.remaining // 2)
        chat_cap = min(chat_cap, ctx.budget.remaining - metadata_cap)
        if chat_cap < 0:
            return "exhausted"
        row = conn.execute(DEVIN_NODE_SQL, (chat_cap, metadata_cap, session_id, node_id)).fetchone()
        if row is None:
            return None
        node_id, parent, chat, meta, chat_len, meta_len, pointer_clipped, text_invalid = row
        if chat is not None and ctx.budget.take(len(chat.encode("utf-8"))) < len(chat.encode("utf-8")):
            return "exhausted"
        if meta is not None and ctx.budget.take(len(meta.encode("utf-8"))) < len(meta.encode("utf-8")):
            return "exhausted"
        if pointer_clipped:
            return "pointer-clipped"
        if text_invalid:
            return "text-invalid"
        return (node_id, parent, chat, meta, chat_len, meta_len)

    def _row_candidate(self, conn, row, ctx, src):
        session_id, cwd, _created, activity, title, head, clipped = row
        if clipped:
            src["flags"].add("metadata-clipped")
        if not valid_session_id(session_id) or not isinstance(cwd, str):
            return None
        if not ctx.accepts(cwd):
            return None
        order, flags = self._walk(conn, session_id, head, ctx, DISCOVERY_NODE_COUNT, DISCOVERY_NODE_TEXT_BYTES)
        src["flags"] |= flags
        has_user = False
        meaningful = False
        for node in order:
            parsed = self._parse_node(node, flags)
            if parsed is None:
                continue
            role, text = parsed
            if role == "user":
                has_user = True
            if text:
                meaningful = True
        src["flags"] |= flags
        if not meaningful and flags:
            has_user = None
        elif not meaningful:
            return None
        return {
            "agent": self.name,
            "session_id": session_id,
            "title": sanitize_title(title, ctx),
            "updated_at": _norm_ts(activity),
            "branch": None,
            "head": None,
            "relation": ctx.relation(cwd),
            "has_user": has_user or _has_user_finalize(False, flags),
            "complete": not flags,
            "_locator": {"session_id": session_id, "head": head},
            **_provenance(ctx, cwd, self.name),
        }

    def _walk(self, conn, session_id, head, ctx, cap, chat_cap):
        order = []
        flags: set = set()
        seen = set()
        current = head
        while current is not None:
            if ctx.expired():
                flags.add("deadline")
                break
            if current in seen:
                flags.add("graph-cycle")
                break
            if len(order) >= cap:
                flags.add("graph-cap")
                break
            seen.add(current)
            try:
                row = self._fetch_node(conn, session_id, current, ctx, chat_cap)
            except sqlite3.Error:
                flags.add("sql-aborted")
                break
            if row == "exhausted":
                flags.add("byte-cap")
                break
            if row == "pointer-clipped":
                flags.add("pointer-clipped")
                break
            if row == "text-invalid":
                flags.add("text-type-invalid")
                break
            if row is None:
                flags.add("graph-gap")
                break
            order.append(row)
            current = row[1]
        order.reverse()
        return order, flags

    @staticmethod
    def _parse_node(row, flags: set):
        _node_id, _parent, chat_message, metadata, chat_len, meta_len = row
        if chat_message is None and chat_len:
            flags.add("record-cap")
            return None
        if metadata is None and meta_len:
            flags.add("metadata-clipped")
            return None
        if chat_message is None:
            return None
        try:
            message = json.loads(chat_message)
        except (ValueError, TypeError):
            flags.add("record-parse")
            return None
        if not isinstance(message, dict):
            flags.add("record-parse")
            return None
        meta = {}
        if metadata is not None:
            try:
                meta = json.loads(metadata)
            except (ValueError, TypeError):
                flags.add("metadata-parse")
                return None
            if not isinstance(meta, dict):
                flags.add("metadata-parse")
                return None
        if meta.get("is_system_prefix"):
            return None
        text = extract_text(message.get("content"))
        if meta.get("summarized_from"):
            return ("summary", text)
        role = message.get("role")
        if role in ("user", "assistant"):
            return (role, text)
        return None

    def read_messages(self, ctx: ScanContext, candidate: dict) -> dict:
        guard, conn = self._open(ctx)
        flags: set = set()
        try:
            sql = _metadata_query("devin", 0, selected=True)
            binds = _metadata_binds([], selected_id=candidate["session_id"])
            binds.append(1)
            if ctx.budget.remaining < _DEVIN_ROW_MAX:
                raise AdapterError("unavailable", "byte-cap")
            try:
                rows = conn.execute(sql, binds).fetchall()
            except sqlite3.Error as exc:
                raise AdapterError("unavailable", "sql-aborted") from exc
            if not rows:
                raise AdapterError("refused", "scope-mismatch")
            row = rows[0]
            _charge_row(row, ctx, flags)
            _sid, cwd, _c, _a, _t, head, clipped = row
            if clipped or not isinstance(cwd, str) or not ctx.accepts(cwd):
                raise AdapterError("refused", "scope-mismatch")
            chain, walk_flags = self._walk(conn, candidate["session_id"], head, ctx, PARENT_TRAVERSAL_CAP, SQL_TEXT_CAP)
            flags |= walk_flags
            conn.rollback()
            if not guard.recheck():
                raise AdapterError("unavailable", "concurrent-change")
        finally:
            conn.close()
        messages = []
        for row in chain:
            parsed = self._parse_node(row, flags)
            if parsed is None:
                continue
            role, text = parsed
            if text:
                messages.append({"role": role, "text": sanitize_text(text), "record_id": str(row[0])})
        truncation = sorted(flags)[0] if flags else None
        return {
            "messages": messages,
            "complete": not flags,
            "truncation": truncation,
            "truncation_reasons": sorted(flags),
        }


# --------------------------------------------------------------------------- #
# Pi                                                                          #
# --------------------------------------------------------------------------- #

_PI_SUMMARY_TYPES = {"compaction", "branch_summary"}


class PiAdapter:
    name = "pi"

    def _store(self, ctx: ScanContext) -> Path:
        if not ctx.explicit_home:
            override = os.environ.get("PI_CODING_AGENT_DIR")
            if override:
                return Path(override) / "sessions"
        return ctx.home / ".pi" / "agent" / "sessions"

    def _anchor(self, ctx: ScanContext) -> Path:
        if not ctx.explicit_home:
            override = os.environ.get("PI_CODING_AGENT_DIR")
            if override:
                return Path(override)
        return _anchor(ctx, ctx.home)

    def discover(self, ctx: ScanContext) -> dict:
        src = _source(self.name)
        store = self._store(ctx)
        anchor = self._anchor(ctx)
        if store.is_symlink() or not store.is_dir() or not trusted_directory(store, anchor):
            if store.is_symlink() or store.exists():
                src["flags"].add("untrusted-dir")
                return _finish({**src, "status": "unreadable", "reason_code": "untrusted-store"})
            return _finish({**src, "status": "absent", "reason_code": "store-missing"})
        for entry in _iter_entries(store, DIR_LISTING_CAP, ctx, src["flags"], anchor):
            bucket = Path(entry.path)
            if not entry.is_dir(follow_symlinks=False) or bucket.is_symlink():
                continue
            self._scan_bucket(bucket, ctx, src)
        return _finish(src)

    def _scan_bucket(self, bucket, ctx, src):
        anchor = self._anchor(ctx)
        for path in _session_files(bucket, ctx, src["flags"], anchor):
            if ctx.expired():
                src["flags"].add("deadline")
                break
            if not trusted_regular_file(path, anchor):
                src["flags"].add("untrusted-file")
                continue
            candidate = self._probe_file(path, ctx, src, anchor)
            if candidate is not None:
                src["candidates"].append(candidate)

    def _probe_file(self, path, ctx, src, anchor):
        try:
            header, header_flag = _first_json_record(path, ctx)
        except SafeOpenError:
            src["flags"].add("unreadable-file")
            return None
        if header_flag is not None:
            src["flags"].add(header_flag)
        if not isinstance(header, dict) or header.get("type") != "session":
            return None
        cwd = header.get("cwd")
        if not isinstance(cwd, str) or not ctx.accepts(cwd):
            return None
        if header.get("version") != 3:
            src["flags"].add("unknown-version")
            return None
        session_id = header.get("id")
        if not valid_session_id(session_id) or session_id in ctx.excluded:
            return None
        if ctx.target_session_id and session_id != ctx.target_session_id:
            return None
        try:
            records, flags = _probe_records(path, ctx)
        except SafeOpenError:
            src["flags"].add("unreadable-file")
            return None
        src["flags"] |= flags
        sampled = records[0] if records else None
        if (
            not isinstance(sampled, dict)
            or sampled.get("type") != "session"
            or sampled.get("version") != 3
            or sampled.get("id") != session_id
            or not isinstance(sampled.get("cwd"), str)
            or not ctx.accepts(sampled["cwd"])
        ):
            src["flags"].add("meta-mismatch")
            return None
        info = self._markers(records, flags, ctx)
        src["flags"] |= flags
        if not info["meaningful"] and flags:
            info["has_user"] = None
        elif not info["meaningful"]:
            return None
        return {
            "agent": self.name,
            "session_id": session_id,
            "title": None,
            "updated_at": info["updated_at"],
            "branch": None,
            "head": None,
            "relation": ctx.relation(cwd),
            "has_user": info["has_user"] or _has_user_finalize(False, flags),
            "complete": not flags,
            "_locator": {"path": str(path), "session_id": session_id},
            **_provenance(ctx, cwd, self.name),
        }

    def locate(self, ctx: ScanContext, session_id: str, src: dict):
        store = self._store(ctx)
        anchor = self._anchor(ctx)
        if not trusted_directory(store, anchor):
            return None
        for entry in _iter_entries(store, DIR_LISTING_CAP, ctx, src["flags"], anchor):
            bucket = Path(entry.path)
            if not entry.is_dir(follow_symlinks=False) or bucket.is_symlink():
                continue
            for file_entry in _iter_entries(bucket, DIR_LISTING_CAP, ctx, src["flags"], anchor):
                name = file_entry.name
                if name != f"{session_id}.jsonl" and not name.endswith(f"_{session_id}.jsonl"):
                    continue
                path = Path(file_entry.path)
                if trusted_regular_file(path, anchor):
                    candidate = self._probe_file(path, ctx, src, anchor)
                    if candidate is not None:
                        return candidate
        return None

    def _markers(self, records, flags, ctx):
        info = {"updated_at": None, "has_user": False, "meaningful": False}
        nodes = {}
        leaf = None
        for record in records:
            ts = record.get("timestamp")
            if isinstance(ts, str) and (info["updated_at"] is None or ts > info["updated_at"]):
                info["updated_at"] = ts
            if isinstance(record.get("id"), str) and "parentId" in record:
                nodes[record["id"]] = record
                leaf = record["id"]
        chain = _trace_chain(nodes, leaf, lambda r: r.get("parentId"), flags, ctx, PARENT_TRAVERSAL_CAP)
        effective, latest = self._effective(chain, flags)
        edits = self._context_edits(effective, flags)
        for record in effective:
            exported = self._export(record, latest, edits, flags)
            if exported is not None:
                role, text = exported
                if role == "user":
                    info["has_user"] = True
                if text:
                    info["meaningful"] = True
        info["updated_at"] = _norm_ts(info["updated_at"])
        return info

    def _scan(self, path, session_id, ctx):
        records, flags = _jsonl_records(path, ctx)
        if not records:
            raise AdapterError("refused", "meta-mismatch")
        header = records[0]
        if header.get("type") != "session" or header.get("id") != session_id:
            raise AdapterError("refused", "meta-mismatch")
        if header.get("version") != 3:
            raise AdapterError("refused", "meta-mismatch")
        cwd = header.get("cwd")
        if not isinstance(cwd, str) or not ctx.accepts(cwd):
            raise AdapterError("refused", "scope-mismatch")
        nodes = {}
        leaf = None
        for record in records:
            if isinstance(record.get("id"), str) and "parentId" in record:
                nodes[record["id"]] = record
                leaf = record["id"]
        chain = _trace_chain(nodes, leaf, lambda r: r.get("parentId"), flags, ctx, PARENT_TRAVERSAL_CAP)
        effective, latest = self._effective(chain, flags)
        edits = self._context_edits(effective, flags)
        return effective, latest, edits, flags

    @staticmethod
    def _effective(chain: list, flags: set):
        latest = None
        for index, record in enumerate(chain):
            if record.get("type") == "compaction":
                latest = index
        if latest is None:
            return list(chain), None
        compaction = chain[latest]
        kept_boundary = compaction.get("firstKeptEntryId")
        kept = []
        if kept_boundary is None:
            flags.add("kept-boundary-missing")
        else:
            start = next(
                (index for index, record in enumerate(chain[:latest]) if record.get("id") == kept_boundary),
                None,
            )
            if start is None:
                flags.add("kept-boundary-missing")
            else:
                kept = chain[start:latest]
        return [compaction] + kept + chain[latest + 1 :], compaction

    @staticmethod
    def _context_edits(effective: list, flags: set) -> dict:
        edits = {}
        for record in effective:
            if record.get("type") != "context_edit":
                continue
            target = record.get("targetId")
            if not isinstance(target, str):
                flags.add("edit-unknown-shape")
                continue
            if "replacement" not in record:
                flags.add("edit-unknown-shape")
                edits[target] = None
                continue
            edits[target] = record.get("replacement")
        applied = {}
        for target, replacement in edits.items():
            if replacement is None:
                applied[target] = None
            elif (
                isinstance(replacement, dict)
                and "content" in replacement
                and set(replacement) <= {"content"}
                and isinstance(replacement["content"], (str, list))
            ):
                applied[target] = replacement["content"]
            else:
                flags.add("edit-unknown-shape")
                applied[target] = None
        return applied

    @classmethod
    def _export(cls, record: dict, latest_compaction, edits: dict, flags: set):
        kind = record.get("type")
        if kind == "context_edit":
            return None
        if kind == "compaction":
            if record is not latest_compaction:
                return None
            text = record.get("summary")
            return ("summary", text) if isinstance(text, str) and text else None
        if kind == "branch_summary":
            text = record.get("summary")
            return ("summary", text) if isinstance(text, str) and text else None
        if kind == "message":
            message = record.get("message")
            if not isinstance(message, dict):
                return None
            record_id = record.get("id")
            if record_id in edits:
                replacement = edits[record_id]
                if replacement is None:
                    return None
                text = extract_text(replacement)
            else:
                text = extract_text(message.get("content"))
            role = message.get("role")
            if role in ("user", "assistant"):
                return (role, text)
            return None
        return None

    def read_messages(self, ctx: ScanContext, candidate: dict) -> dict:
        locator = candidate["_locator"]
        path = Path(locator["path"])
        anchor = self._anchor(ctx)
        if not trusted_regular_file(path, anchor):
            raise AdapterError("unavailable", "transcript-untrusted")
        try:
            effective, latest, edits, flags = self._scan(path, locator["session_id"], ctx)
        except SafeOpenError as exc:
            raise AdapterError("unavailable", "transcript-untrusted") from exc
        messages = []
        for record in effective:
            exported = self._export(record, latest, edits, flags)
            if exported is None:
                continue
            role, text = exported
            if text:
                messages.append(
                    {
                        "role": role,
                        "text": sanitize_text(text),
                        "record_id": str(record.get("id") or ""),
                    }
                )
        truncation = sorted(flags)[0] if flags else None
        return {
            "messages": messages,
            "complete": not flags,
            "truncation": truncation,
            "truncation_reasons": sorted(flags),
        }


OPENCODE_COLUMNS = (
    ("id", 128),
    ("directory", 4096),
    ("title", 1024),
    ("version", 64),
    ("time_created", 64),
    ("time_updated", 64),
)
OPENCODE_METADATA_ROW_MAX = sum(cap for _, cap in OPENCODE_COLUMNS) + 512
OPENCODE_MESSAGE_RESERVE = 1024
OPENCODE_PART_RESERVE = 256


def _opencode_fields(columns):
    fields, invalid = [], []
    for name, cap in columns:
        types = "('text','integer','real','null')" if name.startswith("time_") else "('text','null')"
        size = f"coalesce(length(CAST({name} AS BLOB)),0)"
        valid = f"typeof({name}) IN {types} AND {size} <= {cap}"
        fields.append(f"CASE WHEN {valid} THEN {name} ELSE NULL END")
        invalid.append(f"NOT ({valid})")
    return fields, invalid


def _opencode_metadata_query(anchor_count, *, selected=False, excluded=False):
    assert 0 <= anchor_count <= 4
    assert anchor_count or selected
    assert not (selected and excluded)
    fields, invalid = _opencode_fields(OPENCODE_COLUMNS)
    fields.extend(
        (
            "(fork_session_id IS NOT NULL) AS has_fork",
            "(revert IS NOT NULL AND revert != 'null') AS has_revert",
            "(" + " OR ".join(invalid) + ") AS clipped",
        )
    )
    filters = ["parent_id IS NULL"]
    if anchor_count:
        clause = "(directory = ? OR directory LIKE ? ESCAPE '\\')"
        filters.append("(" + " OR ".join(clause for _ in range(anchor_count)) + ")")
    if selected:
        filters.append("id = ?")
    if excluded:
        filters.append("id != ?")
    return f"SELECT {','.join(fields)} FROM session_v2 WHERE {' AND '.join(filters)} ORDER BY time_updated DESC LIMIT ?"


def _opencode_cwd_fallback_query(*, excluded=False):
    fields, invalid = _opencode_fields((("id", 128), ("directory", 4096), ("time_updated", 64)))
    fields.append("(" + " OR ".join(invalid) + ") AS clipped")
    where = "parent_id IS NULL" + (" AND id != ?" if excluded else "")
    return f"SELECT {','.join(fields)} FROM session_v2 WHERE {where} ORDER BY time_updated DESC LIMIT ?"


OPENCODE_MESSAGE_SQL = (
    "SELECT "
    "CASE WHEN typeof(id) = 'text' AND length(CAST(id AS BLOB)) <= 128 THEN id ELSE NULL END,"
    "CASE WHEN typeof(type) = 'text' AND length(CAST(type AS BLOB)) <= 64 THEN type ELSE NULL END,"
    "CASE WHEN typeof(seq) = 'integer' AND seq >= 0 THEN seq ELSE NULL END,"
    "CASE WHEN type IN ('user','compaction') AND typeof(data) IN ('text','null') "
    "AND coalesce(length(CAST(data AS BLOB)),0) <= ? THEN data ELSE NULL END,"
    "coalesce(length(CAST(data AS BLOB)),0),"
    "(typeof(id) != 'text' OR length(CAST(id AS BLOB)) > 128 OR "
    "typeof(type) != 'text' OR length(CAST(type AS BLOB)) > 64 OR "
    "typeof(seq) != 'integer' OR seq < 0 OR "
    "(type IN ('user','compaction') AND typeof(data) NOT IN ('text','null'))) AS clipped "
    "FROM session_message WHERE session_id = ? AND (? IS NULL OR seq < ?) ORDER BY seq DESC LIMIT 1"
)
OPENCODE_TEXT_PART_SQL = (
    "SELECT "
    "CASE WHEN typeof(ordinal) = 'integer' AND ordinal >= 0 THEN ordinal ELSE NULL END,"
    "CASE WHEN typeof(data) IN ('text','null') AND coalesce(length(CAST(data AS BLOB)),0) <= ? "
    "THEN data ELSE NULL END,"
    "coalesce(length(CAST(data AS BLOB)),0),"
    "(typeof(ordinal) != 'integer' OR ordinal < 0 OR typeof(data) NOT IN ('text','null')) AS clipped "
    "FROM session_part WHERE session_id = ? AND message_id = ? AND type = 'text' "
    "AND (? IS NULL OR ordinal > ?) ORDER BY ordinal ASC LIMIT 1"
)
OPENCODE_LOCATION_SWITCH_SQL = (
    "SELECT 1 FROM session_message WHERE session_id = ? AND type = 'location-switched' LIMIT 1"
)
OPENCODE_MESSAGE_DUPLICATE_SQL = "SELECT 1 FROM session_message WHERE session_id = ? AND seq = ? LIMIT 1 OFFSET 1"
OPENCODE_TEXT_PART_DUPLICATE_SQL = (
    "SELECT 1 FROM session_part WHERE session_id = ? AND message_id = ? "
    "AND type = 'text' AND ordinal = ? LIMIT 1 OFFSET 1"
)

_OPENCODE_SESSION_COLUMNS = {
    "id",
    "directory",
    "title",
    "version",
    "time_created",
    "time_updated",
    "parent_id",
    "fork_session_id",
    "revert",
}
_OPENCODE_MESSAGE_COLUMNS = {"id", "session_id", "type", "seq", "data"}
_OPENCODE_PART_COLUMNS = {"session_id", "message_id", "ordinal", "type", "data"}
_OPENCODE_NONTEXT = {"agent-switched", "model-switched", "synthetic", "system", "skill", "shell", "idle"}


class OpencodeAdapter:
    name = "opencode"

    def _store(self, ctx: ScanContext) -> Path:
        if not ctx.explicit_home:
            xdg = os.environ.get("XDG_DATA_HOME")
            if xdg:
                return Path(xdg) / "opencode"
        return ctx.home / ".local" / "share" / "opencode"

    def _anchor(self, ctx: ScanContext) -> Path:
        if not ctx.explicit_home:
            xdg = os.environ.get("XDG_DATA_HOME")
            if xdg:
                return Path(xdg)
        return _anchor(ctx, ctx.home)

    def _db(self, ctx: ScanContext) -> Path:
        return self._store(ctx) / "opencode.db"

    def _open(self, ctx: ScanContext):
        guard = _SqliteGuard(self._db(ctx), self._anchor(ctx), ctx)
        return guard, guard.connect(ctx)

    def _schema_ok(self, conn, src) -> bool:
        session_cols = _table_columns(conn, "session_v2")
        message_cols = _table_columns(conn, "session_message")
        part_cols = _table_columns(conn, "session_part")
        legacy_cols = _table_columns(conn, "session") | _table_columns(conn, "message") | _table_columns(conn, "part")
        if (
            not _OPENCODE_SESSION_COLUMNS <= session_cols
            or not _OPENCODE_MESSAGE_COLUMNS <= message_cols
            or not _OPENCODE_PART_COLUMNS <= part_cols
        ):
            src["status"] = "unsupported"
            src["reason_code"] = "unknown-schema"
            src["complete"] = False
            if legacy_cols:
                src["flags"].add("legacy-schema-unverified")
            return False
        if legacy_cols:
            src["flags"].add("legacy-schema-unverified")
        return True

    def discover(self, ctx: ScanContext) -> dict:
        src = _source(self.name)
        db = self._db(ctx)
        anchor = self._anchor(ctx)
        if db.is_symlink():
            src["flags"].add("untrusted-file")
            return _finish({**src, "status": "unreadable", "reason_code": "untrusted-store"})
        if not db.exists():
            return _finish({**src, "status": "absent", "reason_code": "store-missing"})
        if not trusted_regular_file(db, anchor):
            src["flags"].add("untrusted-file")
            return _finish({**src, "status": "unreadable", "reason_code": "untrusted-store"})
        guard, conn = self._open(ctx)
        try:
            if not self._schema_ok(conn, src):
                return _finish(src)
            anchors = list(dict.fromkeys(ctx.bucket_paths))[:4]
            excluded_id = next(iter(ctx.excluded), None)
            sql = _opencode_metadata_query(len(anchors), excluded=excluded_id is not None)
            binds = []
            for anchor in anchors:
                binds.extend([anchor, _like_descendant(anchor)])
            if excluded_id is not None:
                binds.append(excluded_id)
            limit = _metadata_limit(ctx, OPENCODE_METADATA_ROW_MAX, src["flags"])
            rows = []
            if limit:
                binds.append(limit)
                try:
                    rows = conn.execute(sql, binds).fetchall()
                except sqlite3.Error:
                    src["flags"].add("sql-aborted")
            if limit and len(rows) >= limit:
                src["flags"].add("row-cap")
            for row in rows:
                if not _charge_row(row, ctx, src["flags"]):
                    break
            rows = rows[:METADATA_ROW_CAP]
            seen_ids = set()
            for row in rows:
                if ctx.expired():
                    src["flags"].add("deadline")
                    break
                if isinstance(row[0], str):
                    seen_ids.add(row[0])
                candidate = self._row_candidate(conn, row, ctx, src)
                if candidate is not None:
                    src["candidates"].append(candidate)
            self._alias_candidates(conn, seen_ids, ctx, src)
            conn.rollback()
            if not guard.recheck():
                src["candidates"] = []
                src["flags"].add("concurrent-change")
        finally:
            conn.close()
        return _finish(src)

    def _alias_candidates(self, conn, seen_ids: set, ctx, src):
        limit = _metadata_limit(ctx, OPENCODE_METADATA_ROW_MAX, src["flags"])
        if not limit:
            return
        excluded_id = next(iter(ctx.excluded), None)
        sql = _opencode_cwd_fallback_query(excluded=excluded_id is not None)
        binds = [excluded_id] if excluded_id is not None else []
        binds.append(limit)
        try:
            rows = conn.execute(sql, binds).fetchall()
        except sqlite3.Error:
            src["flags"].add("sql-aborted")
            return
        if len(rows) >= limit:
            src["flags"].add("row-cap")
        for row in rows:
            if not _charge_row(row, ctx, src["flags"]):
                break
        for row in rows:
            if ctx.expired():
                src["flags"].add("deadline")
                break
            session_id, cwd, _act, _clipped = row
            if not valid_session_id(session_id) or session_id in seen_ids or not isinstance(cwd, str):
                continue
            if not ctx.accepts(cwd):
                continue
            if ctx.budget.remaining < OPENCODE_METADATA_ROW_MAX:
                src["flags"].add("byte-cap")
                break
            sql = _opencode_metadata_query(0, selected=True)
            binds = [session_id, 1]
            try:
                selected = conn.execute(sql, binds).fetchall()
            except sqlite3.Error:
                src["flags"].add("sql-aborted")
                break
            if not selected:
                continue
            seen_ids.add(session_id)
            row = selected[0]
            if not _charge_row(row, ctx, src["flags"]):
                break
            candidate = self._row_candidate(conn, row, ctx, src)
            if candidate is not None:
                src["candidates"].append(candidate)

    def _row_candidate(self, conn, row, ctx, src):
        session_id, cwd, title, _ver, _created, activity, has_fork, has_revert, clipped = row
        if clipped:
            src["flags"].add("metadata-clipped")
        if not valid_session_id(session_id) or not isinstance(cwd, str):
            return None
        if not ctx.accepts(cwd):
            return None
        if has_fork or has_revert:
            src["flags"].add("fork-context-unverified" if has_fork else "revert-context-unverified")
            return {
                "agent": self.name,
                "session_id": session_id,
                "title": sanitize_title(title, ctx),
                "updated_at": _norm_ts(activity),
                "branch": None,
                "head": None,
                "relation": ctx.relation(cwd),
                "has_user": None,
                "complete": False,
                "_locator": {"session_id": session_id, "blocked": "fork" if has_fork else "revert"},
                **_provenance(ctx, cwd, self.name),
            }
        if self._location_switched(conn, session_id, ctx):
            src["flags"].add("location-context-unverified")
            return {
                "agent": self.name,
                "session_id": session_id,
                "title": sanitize_title(title, ctx),
                "updated_at": _norm_ts(activity),
                "branch": None,
                "head": None,
                "relation": ctx.relation(cwd),
                "has_user": None,
                "complete": False,
                "_locator": {"session_id": session_id, "blocked": "location"},
                **_provenance(ctx, cwd, self.name),
            }
        messages, flags = self._messages(conn, session_id, ctx, DISCOVERY_NODE_COUNT)
        if clipped:
            flags.add("metadata-clipped")
        if "legacy-schema-unverified" in src["flags"]:
            flags.add("legacy-schema-unverified")
        src["flags"] |= flags
        has_user = any(m["role"] == "user" for m in messages)
        meaningful = bool(messages)
        if not meaningful and flags:
            has_user = None
        elif not meaningful:
            return None
        return {
            "agent": self.name,
            "session_id": session_id,
            "title": sanitize_title(title, ctx),
            "updated_at": _norm_ts(activity),
            "branch": None,
            "head": None,
            "relation": ctx.relation(cwd),
            "has_user": has_user or _has_user_finalize(False, flags),
            "complete": not flags,
            "_locator": {"session_id": session_id},
            **_provenance(ctx, cwd, self.name),
        }

    @staticmethod
    def _location_switched(conn, session_id, ctx) -> bool:
        if ctx.expired():
            raise AdapterError("unavailable", "deadline")
        if ctx.budget.take(16) < 16:
            raise AdapterError("unavailable", "byte-cap")
        try:
            row = conn.execute(OPENCODE_LOCATION_SWITCH_SQL, (session_id,)).fetchone()
        except sqlite3.Error as exc:
            raise AdapterError("unavailable", "sql-aborted") from exc
        return row is not None

    def locate(self, ctx: ScanContext, session_id: str, src: dict):
        db = self._db(ctx)
        anchor = self._anchor(ctx)
        if not db.exists() or not trusted_regular_file(db, anchor):
            return None
        guard, conn = self._open(ctx)
        try:
            if not self._schema_ok(conn, src):
                return None
            if ctx.budget.remaining < OPENCODE_METADATA_ROW_MAX:
                src["flags"].add("byte-cap")
                return None
            sql = _opencode_metadata_query(0, selected=True)
            try:
                rows = conn.execute(sql, [session_id, 1]).fetchall()
            except sqlite3.Error:
                src["flags"].add("sql-aborted")
                return None
            if not rows:
                return None
            row = rows[0]
            _charge_row(row, ctx, src["flags"])
            candidate = self._row_candidate(conn, row, ctx, src)
            conn.rollback()
            if not guard.recheck():
                src["flags"].add("concurrent-change")
                return None
            return candidate
        finally:
            conn.close()

    def _fetch_message(self, conn, session_id, cursor, ctx):
        if ctx.budget.remaining < OPENCODE_MESSAGE_RESERVE:
            return "exhausted"
        ctx.budget.take(OPENCODE_MESSAGE_RESERVE)
        data_cap = min(JSONL_RECORD_CAP, max(0, ctx.budget.remaining))
        row = conn.execute(OPENCODE_MESSAGE_SQL, (data_cap, session_id, cursor, cursor)).fetchone()
        if row is None:
            return None
        charge = len(row[3].encode("utf-8")) if isinstance(row[3], str) else 0
        if charge:
            granted = ctx.budget.take(charge)
            if granted < charge:
                return "exhausted"
        if row[5]:
            return "clipped"
        try:
            dup = conn.execute(OPENCODE_MESSAGE_DUPLICATE_SQL, (session_id, row[2])).fetchone()
        except sqlite3.Error:
            return "clipped"
        if dup is not None:
            return "duplicate"
        return row

    def _fetch_part(self, conn, session_id, message_id, ordinal, ctx):
        if ctx.budget.remaining < OPENCODE_PART_RESERVE:
            return "exhausted"
        ctx.budget.take(OPENCODE_PART_RESERVE)
        data_cap = min(JSONL_RECORD_CAP, max(0, ctx.budget.remaining))
        row = conn.execute(OPENCODE_TEXT_PART_SQL, (data_cap, session_id, message_id, ordinal, ordinal)).fetchone()
        if row is None:
            return None
        if row[1] is not None and ctx.budget.take(len(row[1].encode("utf-8"))) < len(row[1].encode("utf-8")):
            return "exhausted"
        if row[3]:
            return "clipped"
        try:
            dup = conn.execute(OPENCODE_TEXT_PART_DUPLICATE_SQL, (session_id, message_id, row[0])).fetchone()
        except sqlite3.Error:
            return "clipped"
        if dup is not None:
            return "duplicate"
        return row

    def _messages(self, conn, session_id, ctx, cap):
        flags: set = set()
        rows = []
        cursor = None
        while len(rows) < cap:
            if ctx.expired():
                flags.add("deadline")
                break
            try:
                row = self._fetch_message(conn, session_id, cursor, ctx)
            except sqlite3.Error:
                flags.add("sql-aborted")
                break
            if row == "exhausted":
                flags.add("byte-cap")
                break
            if row == "clipped":
                flags.add("record-cap")
                break
            if row == "duplicate":
                flags.add("duplicate-cursor")
                break
            if row is None:
                break
            rows.append(row)
            if row[2] is None:
                flags.add("pointer-clipped")
                break
            cursor = row[2]
        if len(rows) >= cap:
            flags.add("node-cap")
        rows.reverse()
        messages = []
        for row in rows:
            msg_id, mtype, _seq, data, _dlen, _clip = row
            if mtype == "user":
                text = None
                if data is not None:
                    try:
                        parsed = json.loads(data)
                    except (ValueError, TypeError):
                        flags.add("record-parse")
                        parsed = None
                    if isinstance(parsed, dict) and isinstance(parsed.get("text"), str):
                        text = parsed["text"]
                    else:
                        flags.add("record-shape")
                else:
                    flags.add("record-clipped")
                if text:
                    messages.append({"role": "user", "text": text, "record_id": str(msg_id)})
            elif mtype == "assistant":
                ordinal = None
                while True:
                    if ctx.expired():
                        flags.add("deadline")
                        break
                    try:
                        part = self._fetch_part(conn, session_id, msg_id, ordinal, ctx)
                    except sqlite3.Error:
                        flags.add("sql-aborted")
                        break
                    if part in ("exhausted", "clipped", "duplicate"):
                        flags.add(
                            "byte-cap"
                            if part == "exhausted"
                            else "record-cap"
                            if part == "clipped"
                            else "duplicate-cursor"
                        )
                        break
                    if part is None:
                        break
                    ordinal = part[0]
                    text = None
                    if part[1] is not None:
                        try:
                            parsed = json.loads(part[1])
                        except (ValueError, TypeError):
                            flags.add("record-parse")
                            parsed = None
                        if isinstance(parsed, dict) and isinstance(parsed.get("text"), str):
                            text = parsed["text"]
                        else:
                            flags.add("record-shape")
                    else:
                        flags.add("record-clipped")
                    if text:
                        messages.append({"role": "assistant", "text": text, "record_id": str(msg_id)})
            elif mtype == "compaction":
                if data is not None:
                    try:
                        parsed = json.loads(data)
                    except (ValueError, TypeError):
                        parsed = None
                else:
                    parsed = None
                if not isinstance(parsed, dict):
                    flags.add("compaction-shape")
                    messages = []
                    continue
                native = parsed.get("native")
                if "messages" in parsed or (
                    "native" in parsed and (not isinstance(native, dict) or "messages" in native)
                ):
                    flags.add("native-replacement-unverified")
                    messages = []
                    continue
                state = parsed.get("status")
                if state == "completed" and isinstance(parsed.get("summary"), str) and parsed["summary"].strip():
                    messages = [{"role": "summary", "text": parsed["summary"], "record_id": str(msg_id)}]
                elif state == "failed":
                    continue
                else:
                    flags.add("compaction-shape")
                    messages = []
            elif mtype in _OPENCODE_NONTEXT:
                continue
            else:
                flags.add("unhandled-record")
                messages = []
        return messages, flags

    def read_messages(self, ctx: ScanContext, candidate: dict) -> dict:
        locator = candidate["_locator"]
        if locator.get("blocked") == "fork":
            raise AdapterError("unavailable", "fork-context-unverified")
        if locator.get("blocked") == "revert":
            raise AdapterError("unavailable", "revert-context-unverified")
        if locator.get("blocked") == "location":
            raise AdapterError("unavailable", "location-context-unverified")
        db = self._db(ctx)
        anchor = self._anchor(ctx)
        if not trusted_regular_file(db, anchor):
            raise AdapterError("unavailable", "transcript-untrusted")
        guard, conn = self._open(ctx)
        flags: set = set()
        try:
            schema_src = _source(self.name)
            if not self._schema_ok(conn, schema_src):
                raise AdapterError("unavailable", "unknown-schema")
            flags |= schema_src["flags"]
            if ctx.budget.remaining < OPENCODE_METADATA_ROW_MAX:
                raise AdapterError("unavailable", "byte-cap")
            sql = _opencode_metadata_query(0, selected=True)
            try:
                rows = conn.execute(sql, [candidate["session_id"], 1]).fetchall()
            except sqlite3.Error as exc:
                raise AdapterError("unavailable", "sql-aborted") from exc
            if not rows:
                raise AdapterError("refused", "scope-mismatch")
            row = rows[0]
            _charge_row(row, ctx, flags)
            _sid, cwd, _t, _v, _c, _a, has_fork, has_revert, clipped = row
            if clipped or not isinstance(cwd, str) or not ctx.accepts(cwd):
                raise AdapterError("refused", "scope-mismatch")
            if has_fork:
                raise AdapterError("unavailable", "fork-context-unverified")
            if has_revert:
                raise AdapterError("unavailable", "revert-context-unverified")
            if self._location_switched(conn, candidate["session_id"], ctx):
                raise AdapterError("unavailable", "location-context-unverified")
            messages, mflags = self._messages(conn, candidate["session_id"], ctx, PARENT_TRAVERSAL_CAP)
            flags |= mflags
            conn.rollback()
            if not guard.recheck():
                raise AdapterError("unavailable", "concurrent-change")
        finally:
            conn.close()
        for message in messages:
            message["text"] = sanitize_text(message["text"])
        truncation = sorted(flags)[0] if flags else None
        return {
            "messages": messages,
            "complete": not flags,
            "truncation": truncation,
            "truncation_reasons": sorted(flags),
        }


class GeminiAdapter:
    name = "gemini"

    def _stores(self, ctx: ScanContext) -> list:
        if ctx.explicit_home:
            parent = ctx.home
        else:
            override = os.environ.get("GEMINI_CLI_HOME")
            parent = Path(override) if override else ctx.home
        return [
            parent / ".gemini" / "tmp",
            parent / ".cache" / ".gemini" / "tmp",
        ]

    def _anchor(self, ctx: ScanContext) -> Path:
        if not ctx.explicit_home and os.environ.get("GEMINI_CLI_HOME"):
            return Path(os.environ["GEMINI_CLI_HOME"])
        return _anchor(ctx, ctx.home)

    @staticmethod
    def _bucket_hash(path_str: str) -> str:
        import hashlib

        return hashlib.sha256(path_str.encode("utf-8")).hexdigest()

    def _scope_bucket(self, bucket: Path, ctx: ScanContext, anchor: Path, flags):
        marker = bucket / ".project_root"
        try:
            before = marker.lstat()
        except FileNotFoundError:
            before = None
        except OSError:
            flags.add("unreadable-marker")
            return None
        if before is not None:
            if not trusted_regular_file(marker, anchor):
                flags.add("untrusted-marker")
                return None
            if not 0 < before.st_size <= 4096:
                flags.add("marker-oversized" if before.st_size > 4096 else "marker-invalid")
                return None
            if ctx.budget.take(before.st_size) < before.st_size:
                flags.add("byte-cap")
                return None
            try:
                with open_regular_read(marker, max_bytes=4096) as descriptor:
                    raw = os.pread(descriptor, before.st_size, 0)
                    after = os.fstat(descriptor)
            except (OSError, SafeOpenError):
                flags.add("unreadable-marker")
                return None
            identity_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            if len(raw) != before.st_size or any(
                getattr(before, key) != getattr(after, key) for key in identity_fields
            ):
                flags.add("concurrent-change")
                return None
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                flags.add("marker-invalid")
                return None
            text = text.strip()
            if not ctx.accepts(text):
                return None
            return (text, self._bucket_hash(text))
        known = None
        for base in ctx.bucket_paths:
            if bucket.name == self._bucket_hash(base):
                known = base
                break
        if known is None:
            return None
        return (known, bucket.name)

    def discover(self, ctx: ScanContext) -> dict:
        src = _source(self.name)
        anchor = self._anchor(ctx)
        seen_store = False
        seen_ids = {}
        for store in self._stores(ctx):
            if not store.exists():
                continue
            if store.is_symlink() or not trusted_directory(store, anchor):
                src["flags"].add("untrusted-dir")
                continue
            seen_store = True
            self._scan_store(store, ctx, src, anchor, seen_ids)
        if not seen_store:
            if src["flags"]:
                return _finish({**src, "status": "unreadable", "reason_code": "untrusted-store"})
            return _finish({**src, "status": "absent", "reason_code": "store-missing"})
        return _finish(src)

    def _scan_store(self, store, ctx, src, anchor, seen_ids):
        for entry in _iter_entries(store, DIR_LISTING_CAP, ctx, src["flags"], anchor):
            bucket = Path(entry.path)
            if not entry.is_dir(follow_symlinks=False) or bucket.is_symlink():
                continue
            scope = self._scope_bucket(bucket, ctx, anchor, src["flags"])
            if scope is None:
                continue
            marker_cwd, expected_hash = scope
            chats = bucket / "chats"
            if not chats.is_dir():
                continue
            for file_entry in _iter_entries(chats, DIR_LISTING_CAP, ctx, src["flags"], anchor):
                path = Path(file_entry.path)
                if ctx.expired():
                    src["flags"].add("deadline")
                    break
                if path.suffix == ".json":
                    src["flags"].add("legacy-json-unverified")
                    continue
                if path.suffix != ".jsonl" or not (path.name.startswith("session-") or valid_session_id(path.stem)):
                    continue
                if not trusted_regular_file(path, anchor):
                    src["flags"].add("untrusted-file")
                    continue
                candidate = self._probe_file(path, marker_cwd, expected_hash, ctx, src)
                if candidate is None:
                    continue
                sid = candidate["session_id"]
                if sid in seen_ids:
                    if seen_ids[sid] != candidate["_locator"]["path"]:
                        src["flags"].add("duplicate-session-conflict")
                        for prior in src["candidates"]:
                            if prior["session_id"] == sid:
                                prior["complete"] = False
                    continue
                seen_ids[sid] = candidate["_locator"]["path"]
                src["candidates"].append(candidate)

    @staticmethod
    def _part_text(content):
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            if (
                isinstance(content.get("text"), str)
                and not content.get("thought")
                and not any(
                    key in content
                    for key in (
                        "functionCall",
                        "functionResponse",
                        "inlineData",
                        "fileData",
                        "executableCode",
                        "codeExecutionResult",
                    )
                )
            ):
                return content["text"]
            return ""
        if isinstance(content, list):
            return "".join(GeminiAdapter._part_text(part) for part in content if isinstance(part, (str, dict)))
        return ""

    def _export(self, record, flags):
        kind = record.get("type")
        if kind == "user":
            role = "user"
        elif kind == "gemini":
            role = "assistant"
        else:
            return None
        if record.get("thought") is True:
            return None
        text = ""
        display = record.get("displayContent")
        if "displayContent" in record:
            text = self._part_text(display)
            if not isinstance(display, (str, dict, list)) or (not text and display):
                flags.add("display-invalid")
            return (role, text) if text else None
        content = record.get("content")
        if not isinstance(content, (str, dict, list)):
            flags.add("record-shape")
        text = self._part_text(content)
        return (role, text) if text else None

    def _metadata_and_messages(self, records, flags, ctx):
        identity = {}
        observed = []
        messages = {}
        order = []
        windowed = any(r.get("_seg") == "tail" for r in records)
        metadata_keys = ("sessionId", "projectHash", "kind", "summary", "startTime", "lastUpdated", "directories")

        def metadata(value):
            selected = {key: value[key] for key in metadata_keys if key in value}
            observed.append(selected)
            identity.update(selected)

        def merge(value, replace_all=False):
            nonlocal messages, order
            if replace_all:
                messages, order = {}, []
            if not isinstance(value, list):
                flags.add("record-shape")
                messages, order = {}, []
                return
            for entry in value:
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                    flags.add("record-shape")
                    continue
                mid = entry["id"]
                if mid not in messages:
                    order.append(mid)
                messages[mid] = dict(entry)

        for record in records:
            apply_body = not windowed or record.get("_seg") == "tail"
            controls = {key for key in record if key.startswith("$")}
            if controls - {"$set", "$patch", "$rewindTo"} or len(controls) > 1:
                flags.add("unsupported-control")
                messages, order = {}, []
                continue
            if "$set" in record:
                data = record["$set"]
                if not isinstance(data, dict):
                    flags.add("record-shape")
                    messages, order = {}, []
                    continue
                metadata(data)
                if apply_body and "messages" in data:
                    merge(data["messages"], replace_all=True)
                continue
            is_metadata = (
                "sessionId" in record
                or "projectHash" in record
                or ("id" not in record and any(key in record for key in metadata_keys + ("messages",)))
            )
            if is_metadata:
                metadata(record)
                if apply_body and "messages" in record:
                    merge(record["messages"])
                continue
            if not apply_body:
                continue
            if "$rewindTo" in record:
                target = record["$rewindTo"]
                if isinstance(target, str) and target in messages:
                    cut = order.index(target)
                    for mid in order[cut:]:
                        messages.pop(mid, None)
                    order = order[:cut]
                else:
                    flags.add("missing-boundary")
                    messages = {}
                    order = []
                continue
            if "$patch" in record:
                patch = record["$patch"]
                if not isinstance(patch, dict):
                    flags.add("edit-unknown-shape")
                    messages = {}
                    order = []
                    continue
                applied = self._apply_patch(patch, messages, order, flags)
                if not applied:
                    messages = {}
                    order = []
                continue
            if any(key.startswith("$") for key in record):
                flags.add("unsupported-control")
                messages, order = {}, []
                continue
            if isinstance(record.get("id"), str):
                mid = record["id"]
                if mid not in messages:
                    order.append(mid)
                messages[mid] = dict(record)
        return identity, observed, [(mid, messages[mid]) for mid in order if mid in messages]

    def _apply_patch(self, patch, messages, order, flags):
        target = patch.get("id")
        if target is not None:
            if not isinstance(target, str) or target not in messages:
                flags.add("missing-boundary")
                return False
            if "content" in patch:
                messages[target]["content"] = patch["content"]
                messages[target].pop("displayContent", None)
        updates = patch.get("updates")
        if updates is not None:
            if not isinstance(updates, list):
                flags.add("edit-unknown-shape")
                return False
            for update in updates:
                if (
                    not isinstance(update, dict)
                    or not isinstance(update.get("id"), str)
                    or update["id"] not in messages
                ):
                    flags.add("missing-boundary")
                    return False
                uid = update["id"]
                if "content" in update:
                    messages[uid]["content"] = update["content"]
                    messages[uid].pop("displayContent", None)
        removes = patch.get("removeIds")
        if removes is not None:
            if not isinstance(removes, list) or any(not isinstance(mid, str) for mid in removes):
                flags.add("edit-unknown-shape")
                return False
            for mid in removes:
                if isinstance(mid, str) and mid in messages:
                    messages.pop(mid, None)
                    order.remove(mid)
        orderids = patch.get("orderIds")
        if orderids is not None:
            if not isinstance(orderids, list) or any(not isinstance(mid, str) for mid in orderids):
                flags.add("edit-unknown-shape")
                return False
            known = list(dict.fromkeys(mid for mid in orderids if mid in messages))
            rest = [mid for mid in order if mid not in known]
            order[:] = rest + known
        if target is None and updates is None and removes is None and orderids is None:
            flags.add("edit-unknown-shape")
            return False
        return True

    def _validate_identity(self, identity, observed, expected_hash, ctx, flags=None):
        session_id = identity.get("sessionId")
        if not valid_session_id(session_id):
            return False
        kind = identity.get("kind")
        if kind == "subagent" or (kind is not None and kind != "main"):
            return False
        for meta in observed:
            if "sessionId" in meta and not valid_session_id(meta["sessionId"]):
                return False
            if "projectHash" in meta and meta["projectHash"] != expected_hash:
                return False
            meta_kind = meta.get("kind")
            if meta_kind not in (None, "main"):
                return False
            if "directories" in meta:
                directories = meta["directories"]
                if not isinstance(directories, list):
                    return False
                for entry in directories:
                    if not isinstance(entry, str) or not ctx.accepts(entry):
                        return False
        if identity.get("projectHash") != expected_hash:
            return False
        return True

    def _probe_file(self, path, marker_cwd, expected_hash, ctx, src, target_session_id=None):
        try:
            records, flags = _probe_records(path, ctx)
        except SafeOpenError:
            src["flags"].add("unreadable-file")
            return None
        src["flags"] |= flags
        identity, observed, ordered = self._metadata_and_messages(records, flags, ctx)
        session_id = identity.get("sessionId")
        if not self._validate_identity(identity, observed, expected_hash, ctx, flags):
            src["flags"].add("meta-mismatch")
            return None
        if session_id in ctx.excluded:
            return None
        if target_session_id is not None and session_id != target_session_id:
            return None
        cwd = marker_cwd
        has_user = False
        meaningful = False
        last_ts = None
        for mid, record in ordered:
            ts = record.get("timestamp")
            if isinstance(ts, str) and (last_ts is None or ts > last_ts):
                last_ts = ts
            exported = self._export(record, flags)
            if exported is not None:
                role, text = exported
                if role == "user":
                    has_user = True
                if text:
                    meaningful = True
        src["flags"] |= flags
        if not meaningful and flags:
            has_user = None
        elif not meaningful:
            return None
        updated = _norm_ts(identity.get("lastUpdated") or last_ts or identity.get("startTime"))
        base = _provenance(ctx, cwd, self.name)
        base["association"] = "native-project-hash"
        return {
            "agent": self.name,
            "session_id": session_id,
            "title": sanitize_title(identity.get("summary"), ctx),
            "updated_at": updated,
            "branch": None,
            "head": None,
            "relation": ctx.relation(cwd),
            "has_user": has_user or _has_user_finalize(False, flags),
            "complete": not flags,
            "_locator": {
                "path": str(path),
                "expected_hash": expected_hash,
                "marker_cwd": marker_cwd,
            },
            **base,
        }

    def locate(self, ctx: ScanContext, session_id: str, src: dict):
        anchor = self._anchor(ctx)
        found = None
        for store in self._stores(ctx):
            if not store.exists() or store.is_symlink() or not trusted_directory(store, anchor):
                continue
            for entry in _iter_entries(store, DIR_LISTING_CAP, ctx, src["flags"], anchor):
                bucket = Path(entry.path)
                if not entry.is_dir(follow_symlinks=False) or bucket.is_symlink():
                    continue
                scope = self._scope_bucket(bucket, ctx, anchor, src["flags"])
                if scope is None:
                    continue
                marker_cwd, expected_hash = scope
                chats = bucket / "chats"
                if not chats.is_dir():
                    continue
                for file_entry in _iter_entries(chats, DIR_LISTING_CAP, ctx, src["flags"], anchor):
                    path = Path(file_entry.path)
                    if path.suffix != ".jsonl" or not (path.name.startswith("session-") or valid_session_id(path.stem)):
                        continue
                    if not trusted_regular_file(path, anchor):
                        continue
                    candidate = self._probe_file(
                        path, marker_cwd, expected_hash, ctx, src, target_session_id=session_id
                    )
                    if candidate is not None:
                        if found is not None and found["_locator"]["path"] != candidate["_locator"]["path"]:
                            raise AdapterError("refused", "duplicate-session-conflict")
                        found = candidate
        return found

    def read_messages(self, ctx: ScanContext, candidate: dict) -> dict:
        locator = candidate["_locator"]
        path = Path(locator["path"])
        anchor = self._anchor(ctx)
        if not trusted_regular_file(path, anchor):
            raise AdapterError("unavailable", "transcript-untrusted")
        scope = self._scope_bucket(path.parent.parent, ctx, anchor, set())
        expected_scope = (locator["marker_cwd"], locator["expected_hash"])
        if scope != expected_scope:
            raise AdapterError("refused", "scope-mismatch")
        try:
            records, flags = _jsonl_records(path, ctx)
        except SafeOpenError as exc:
            raise AdapterError("unavailable", "transcript-untrusted") from exc
        identity, observed, ordered = self._metadata_and_messages(records, flags, ctx)
        if identity.get("sessionId") != candidate["session_id"]:
            raise AdapterError("refused", "meta-mismatch")
        if not self._validate_identity(identity, observed, locator["expected_hash"], ctx, flags):
            raise AdapterError("refused", "scope-mismatch")
        if self._scope_bucket(path.parent.parent, ctx, anchor, flags) != expected_scope:
            raise AdapterError("refused", "scope-mismatch")
        messages = []
        for mid, record in ordered:
            exported = self._export(record, flags)
            if exported is None:
                continue
            role, text = exported
            if text:
                messages.append({"role": role, "text": sanitize_text(text), "record_id": str(mid)})
        truncation = sorted(flags)[0] if flags else None
        return {
            "messages": messages,
            "complete": not flags,
            "truncation": truncation,
            "truncation_reasons": sorted(flags),
        }


_COPILOT_SESSION_TYPES = {"session.start", "session.resume", "session.context_changed"}
_COPILOT_CLEAR_TYPES = {
    "session.truncation",
    "session.snapshot_rewind",
    "journal.link",
    "session.journal_link",
}
_COPILOT_METADATA_TYPES = {
    "session.shutdown",
    "session.idle",
    "session.error",
    "session.warning",
    "session.title_changed",
    "session.model_change",
    "session.model_deselected",
    "session.mode_changed",
    "session.mode_notice_delivered",
    "session.usage_info",
    "session.usage_checkpoint",
    "session.info",
    "session.skills_loaded",
    "session.tools_updated",
    "session.custom_agents_updated",
    "session.mcp_servers_loaded",
    "session.mcp_server_status_changed",
    "session.mcp_server_tools_loaded",
    "session.mcp_server_removed",
    "session.mcp_server_needs_reconnect",
    "session.managed_settings_enforced",
    "session.managed_settings_resolved",
    "session.permissions_changed",
    "session.quota_observation",
}


class CopilotAdapter:
    name = "copilot"

    def _store(self, ctx: ScanContext) -> Path:
        if not ctx.explicit_home:
            override = os.environ.get("COPILOT_HOME")
            if override:
                return Path(override) / "session-state"
        return ctx.home / ".copilot" / "session-state"

    def _anchor(self, ctx: ScanContext) -> Path:
        if not ctx.explicit_home and os.environ.get("COPILOT_HOME"):
            return Path(os.environ["COPILOT_HOME"])
        return _anchor(ctx, ctx.home)

    def discover(self, ctx: ScanContext) -> dict:
        src = _source(self.name)
        store = self._store(ctx)
        anchor = self._anchor(ctx)
        if store.is_symlink() or not store.is_dir() or not trusted_directory(store, anchor):
            if store.is_symlink() or store.exists():
                src["flags"].add("untrusted-dir")
                return _finish({**src, "status": "unreadable", "reason_code": "untrusted-store"})
            return _finish({**src, "status": "absent", "reason_code": "store-missing"})
        for entry in _iter_entries(store, DIR_LISTING_CAP, ctx, src["flags"], anchor):
            directory = Path(entry.path)
            if not entry.is_dir(follow_symlinks=False) or directory.is_symlink():
                continue
            events = directory / "events.jsonl"
            if not events.exists():
                continue
            if not trusted_regular_file(events, anchor):
                src["flags"].add("untrusted-file")
                continue
            candidate = self._probe_file(events, directory.name, ctx, src)
            if candidate is not None:
                src["candidates"].append(candidate)
        return _finish(src)

    def _identity(self, records, ctx):
        info = {"session_id": None, "cwd": None, "worker": False, "valid": False, "updated_at": None}
        seen = False
        for record in records:
            kind = record.get("type")
            data = record.get("data")
            ts = record.get("timestamp")
            if isinstance(ts, str) and (info["updated_at"] is None or ts > info["updated_at"]):
                info["updated_at"] = ts
            if kind in _COPILOT_SESSION_TYPES and not isinstance(data, dict):
                return False, info
            if kind == "session.start":
                context = data.get("context")
                if not isinstance(context, dict):
                    return False, info
                cwd = context.get("cwd")
                git_root = context.get("gitRoot")
                sid = data.get("sessionId")
                version = data.get("version")
                if (
                    not isinstance(cwd, str)
                    or not ctx.accepts(cwd)
                    or (git_root is not None and (not isinstance(git_root, str) or not ctx.accepts(git_root)))
                    or not valid_session_id(sid)
                    or isinstance(version, bool)
                    or not isinstance(version, int)
                    or version <= 0
                    or (seen and sid != info["session_id"])
                ):
                    return False, info
                info["session_id"] = sid
                info["cwd"] = cwd
                info["worker"] = info["worker"] or bool(data.get("detachedFromSpawningParentSessionId"))
                info["valid"] = True
                seen = True
            elif kind == "session.resume":
                if not seen:
                    return False, info
                if "context" in data:
                    context = data["context"]
                    if not isinstance(context, dict):
                        return False, info
                    cwd = context.get("cwd")
                    git_root = context.get("gitRoot")
                    if (
                        not isinstance(cwd, str)
                        or not ctx.accepts(cwd)
                        or (git_root is not None and (not isinstance(git_root, str) or not ctx.accepts(git_root)))
                    ):
                        return False, info
            elif kind == "session.context_changed":
                cwd = data.get("cwd")
                git_root = data.get("gitRoot")
                if (
                    not seen
                    or not isinstance(cwd, str)
                    or not ctx.accepts(cwd)
                    or (git_root is not None and (not isinstance(git_root, str) or not ctx.accepts(git_root)))
                ):
                    return False, info
            elif not seen and kind in ("user.message", "assistant.message"):
                return False, info
        return seen and info["valid"], info

    def _project(self, records, ctx):
        flags = {"native-version-unpinned"}
        nodes = {}
        positions = {}
        leaf = None
        seen_meta = False

        def worker(record):
            data = record.get("data") if isinstance(record.get("data"), dict) else {}
            return bool(record.get("agentId") or data.get("parentAgentTaskId") or data.get("parentToolCallId"))

        def rewrite(kind):
            return kind in _COPILOT_CLEAR_TYPES or (
                isinstance(kind, str)
                and kind.startswith("session.")
                and kind not in _COPILOT_SESSION_TYPES
                and kind not in _COPILOT_METADATA_TYPES
            )

        for index, record in enumerate(records):
            kind = record.get("type")
            if kind in _COPILOT_SESSION_TYPES:
                seen_meta = True
            data = record.get("data") if isinstance(record.get("data"), dict) else {}
            ephemeral = bool(record.get("ephemeral") or data.get("ephemeral"))
            if (
                not worker(record)
                and kind in ("user.message", "assistant.message")
                and (not isinstance(record.get("id"), str) or not record["id"] or "parentId" not in record)
            ):
                flags.add("record-shape")
            if isinstance(record.get("id"), str) and "parentId" in record:
                if record["id"] in nodes:
                    flags.add("duplicate-event-id")
                nodes[record["id"]] = record
                positions[record["id"]] = index
                if not worker(record) and not ephemeral:
                    leaf = record["id"]
        chain = _trace_chain(nodes, leaf, lambda r: r.get("parentId"), flags, ctx, PARENT_TRAVERSAL_CAP)
        chain_ids = {record["id"] for record in chain}
        stream = [(positions[record["id"]], record, True) for record in chain]
        for index, record in enumerate(records):
            record_id = record.get("id")
            anchored = isinstance(record_id, str) and record_id in chain_ids
            if not anchored and not worker(record) and rewrite(record.get("type")):
                stream.append((index, record, False))
        stream.sort(key=lambda item: item[0])
        messages = []
        for _index, record, anchored in stream:
            kind = record.get("type")
            data = record.get("data") if isinstance(record.get("data"), dict) else {}
            if worker(record) or data.get("hidden") or data.get("isHidden"):
                continue
            if not anchored:
                flags.add("unsupported-control")
                messages = []
                continue
            if kind == "user.message":
                source = data.get("source")
                if source not in (None, ""):
                    flags.add("unknown-source")
                    continue
                content = data.get("content")
                if isinstance(content, str) and content:
                    messages.append({"role": "user", "text": content, "record_id": str(record["id"])})
            elif kind == "assistant.message":
                phase = data.get("phase")
                if phase in ("analysis", "thinking"):
                    continue
                content = data.get("content")
                if isinstance(content, str) and content:
                    messages.append({"role": "assistant", "text": content, "record_id": str(record["id"])})
            elif kind == "session.compaction_complete":
                success = data.get("success")
                summary = data.get("summaryContent")
                if success is False:
                    continue
                messages = []
                if success is True and isinstance(summary, str) and summary.strip():
                    messages.append({"role": "summary", "text": summary, "record_id": str(record["id"])})
                    flags.add("compaction-boundary-unverified")
                else:
                    flags.add("compaction-shape")
            elif kind == "session.context_cleared":
                messages = []
            elif kind == "session.handoff":
                summary = data.get("summary")
                if isinstance(summary, str) and summary.strip():
                    messages.append({"role": "summary", "text": summary, "record_id": str(record["id"])})
            elif rewrite(kind):
                flags.add("unsupported-control")
                messages = []
        return messages, flags, seen_meta

    def _probe_file(self, events, basename, ctx, src):
        try:
            records, flags = _probe_records(events, ctx)
        except SafeOpenError:
            src["flags"].add("unreadable-file")
            return None
        src["flags"] |= flags
        valid, info = self._identity(records, ctx)
        if not valid:
            src["flags"].add("meta-unverified")
            return None
        session_id = info["session_id"]
        if basename != session_id or session_id in ctx.excluded:
            return None
        if info["worker"]:
            return None
        messages, mflags, _meta = self._project(records, ctx)
        local = flags | mflags
        src["flags"] |= local
        has_user = any(m["role"] == "user" for m in messages)
        meaningful = bool(messages)
        if not meaningful and local <= {"native-version-unpinned"}:
            return None
        if not meaningful and local:
            has_user = None
        elif not meaningful:
            return None
        return {
            "agent": self.name,
            "session_id": session_id,
            "title": None,
            "updated_at": _norm_ts(info["updated_at"]),
            "branch": None,
            "head": None,
            "relation": ctx.relation(info["cwd"]),
            "has_user": has_user or _has_user_finalize(False, local),
            "complete": not local,
            "_locator": {"path": str(events), "session_id": session_id},
            **_provenance(ctx, info["cwd"], self.name),
        }

    def locate(self, ctx: ScanContext, session_id: str, src: dict):
        store = self._store(ctx)
        anchor = self._anchor(ctx)
        if not trusted_directory(store, anchor):
            return None
        directory = store / session_id
        events = directory / "events.jsonl"
        if not trusted_regular_file(events, anchor):
            return None
        return self._probe_file(events, session_id, ctx, src)

    def read_messages(self, ctx: ScanContext, candidate: dict) -> dict:
        locator = candidate["_locator"]
        path = Path(locator["path"])
        anchor = self._anchor(ctx)
        if not trusted_regular_file(path, anchor):
            raise AdapterError("unavailable", "transcript-untrusted")
        try:
            records, flags = _jsonl_records(path, ctx)
        except SafeOpenError as exc:
            raise AdapterError("unavailable", "transcript-untrusted") from exc
        valid, info = self._identity(records, ctx)
        if not valid or info["session_id"] != locator["session_id"] or info["worker"]:
            raise AdapterError("refused", "meta-mismatch")
        messages, mflags, _meta = self._project(records, ctx)
        flags |= mflags
        for message in messages:
            message["text"] = sanitize_text(message["text"])
        truncation = sorted(flags)[0] if flags else None
        return {
            "messages": messages,
            "complete": not flags,
            "truncation": truncation,
            "truncation_reasons": sorted(flags),
        }


ADAPTERS = {
    "claude": ClaudeAdapter(),
    "codex": CodexAdapter(),
    "devin": DevinAdapter(),
    "pi": PiAdapter(),
    "opencode": OpencodeAdapter(),
    "gemini": GeminiAdapter(),
    "copilot": CopilotAdapter(),
}
