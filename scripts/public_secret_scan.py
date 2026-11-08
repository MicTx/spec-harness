#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Bounded credential scanning without returning secret values or excerpts.

Inspect ZIP/TAR and gzip/bzip2/xz in memory, including commonly encoded inputs.
This gate cannot prove arbitrary encrypted or steganographic content safe.
Unsupported, malformed or over-budget containers fail closed.
"""

from __future__ import annotations

import base64
import binascii
import bz2
import hashlib
import io
import lzma
import re
import stat
import tarfile
import zipfile
import zlib
from dataclasses import dataclass, field

# The left boundary avoids matching ordinary identifiers such as task-... .
RULES = (
    ("api-key", re.compile(r"(?<![A-Za-z0-9_])sk-(?:proj-|ant-api\d+-)?[A-Za-z0-9_-]{20,}")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}")),
    ("private-key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
)
BASE64_CANDIDATE = re.compile(rb"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{28,}={0,2}(?![A-Za-z0-9+/_=-])")
ESCAPED_ASCII = re.compile(rb"\\(?:u00([0-9a-fA-F]{2})|x([0-9a-fA-F]{2}))")
PERCENT_ASCII = re.compile(rb"%([0-9a-fA-F]{2})")
PAX_RECORD_LENGTH = re.compile(rb"([0-9]{1,20}) ")
ARCHIVE_SUFFIXES = (".zip", ".tar", ".tgz", ".gz", ".tbz", ".tbz2", ".bz2", ".txz", ".xz")
UNSUPPORTED_SUFFIXES = (".7z", ".rar", ".zst", ".zstd")
UNSUPPORTED_MAGIC = (b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07", b"\x28\xb5\x2f\xfd")
MAX_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 50_000
MAX_VARIANTS = 50_000
MAX_DEPTH = 4
MAX_ENCODING_DEPTH = 4


class SecretScanError(ValueError):
    """Inspection incomplete. Partial findings are safe, redacted records only."""

    def __init__(self, message: str, *, findings: tuple[SecretFinding, ...] = ()):
        super().__init__(message)
        self.findings = findings


@dataclass(frozen=True)
class SecretFinding:
    rule: str
    fingerprint: str
    location: str


@dataclass
class _Budget:
    bytes_left: int
    members_left: int
    variants_left: int
    extra_patterns: tuple[re.Pattern[str], ...] = ()
    findings: set[SecretFinding] = field(default_factory=set)

    def consume(self, size: int) -> None:
        self.bytes_left -= size
        if self.bytes_left < 0:
            raise SecretScanError("credential scan resource limit exceeded")

    def member(self) -> None:
        self.members_left -= 1
        if self.members_left < 0:
            raise SecretScanError("credential scan archive member limit exceeded")

    def variant(self) -> None:
        self.variants_left -= 1
        if self.variants_left < 0:
            raise SecretScanError("credential scan encoding variant limit exceeded")


def _decoded_payloads(data: bytes, *, utf16: bool = True):
    unescaped = ESCAPED_ASCII.sub(lambda match: bytes([int(match[1] or match[2], 16)]), data)
    if unescaped != data:
        yield "escape", unescaped
    unquoted = PERCENT_ASCII.sub(lambda match: bytes([int(match[1], 16)]), data)
    if unquoted != data:
        yield "percent", unquoted
    # A lone NUL in binary data is not evidence of UTF-16 text. Repeated
    # replacement decoding otherwise creates spurious recursive encodings.
    if utf16 and (
        data.startswith((b"\xff\xfe", b"\xfe\xff"))
        or (len(data) >= 4 and max(data[::2].count(0), data[1::2].count(0)) >= len(data) // 4)
    ):
        for encoding in ("utf-16-le", "utf-16-be"):
            try:
                decoded = data.decode(encoding)
            except UnicodeError:
                continue
            yield encoding, decoded.encode("utf-8")
    for match in BASE64_CANDIDATE.finditer(data):
        candidate = match[0]
        try:
            decoded = base64.b64decode(candidate + b"=" * (-len(candidate) % 4), altchars=b"-_", validate=True)
        except (ValueError, binascii.Error):
            continue
        yield "base64", decoded


def _variants(data: bytes, budget: _Budget, depth: int = 0, seen: set[bytes] | None = None):
    """Decode in bounded depth; a still-encoded frontier is incomplete, not clean."""
    if seen is None:
        seen = set()
    digest = hashlib.sha256(data).digest()
    if digest in seen:
        return
    seen.add(digest)
    budget.consume(len(data))
    yield data
    for _kind, decoded in _decoded_payloads(data):
        if hashlib.sha256(decoded).digest() in seen:
            continue
        if depth >= MAX_ENCODING_DEPTH:
            raise SecretScanError("credential scan encoding nesting limit exceeded")
        budget.variant()
        yield from _variants(decoded, budget, depth + 1, seen)


def redact(text: str, *, extra_patterns: tuple[re.Pattern[str], ...] = ()) -> str:
    """Suppress recognized credentials, private patterns and encoded labels."""
    for _rule, pattern in RULES:
        text = pattern.sub("[REDACTED]", text)
    try:
        budget = _Budget(MAX_BYTES, MAX_MEMBERS, MAX_VARIANTS)
        patterns = tuple(pattern for _rule, pattern in RULES) + extra_patterns
        for data in _variants(text.encode("utf-8", "replace"), budget):
            if _opaque_label(data) or any(pattern.search(data.decode("utf-8", "replace")) for pattern in patterns):
                return "[REDACTED]"
    except SecretScanError:
        return "[REDACTED]"
    return text[:512]


def _record_matches(data: bytes, location: str, budget: _Budget) -> None:
    text = data.decode("utf-8", "replace")
    patterns = (*RULES, *(("private-data", pattern) for pattern in budget.extra_patterns))
    for rule, pattern in patterns:
        for match in pattern.finditer(text):
            fingerprint = hashlib.sha256(match[0].encode("utf-8")).hexdigest()[:16]
            budget.findings.add(SecretFinding(rule, fingerprint, location))


def _scan_label(label: str, budget: _Budget) -> None:
    location = redact(label, extra_patterns=budget.extra_patterns)
    for data in _variants(label.encode("utf-8", "replace"), budget):
        _record_matches(data, location, budget)
        if _opaque_label(data):
            raise SecretScanError("credential scan container-encoded label unsupported")


def _read_member(stream, size: int, budget: _Budget) -> bytes:
    if size < 0 or size > budget.bytes_left:
        raise SecretScanError("credential scan member size exceeds limit")
    data = stream.read(size + 1)
    if len(data) != size:
        raise SecretScanError("credential scan member size mismatch")
    return data


def _is_tar(data: bytes) -> bool:
    if len(data) < 512:
        return False
    if not data[:512].strip(b"\0"):
        # Empty TARs have no named header; their zero records must not route
        # through text decoding and hide a later compressed carrier.
        return True
    if data[257:262] == b"ustar":
        return True
    try:
        # V7 TAR has no ustar magic; validate its header checksum instead.
        tarfile.TarInfo.frombuf(data[:512], "utf-8", "surrogateescape")
    except (tarfile.HeaderError, ValueError):
        return False
    return True


def _opaque_label(data: bytes) -> bool:
    # Encoded binary containers do not belong in a printable diagnostic/path.
    # Refuse them rather than reproducing a reversible carrier in the report.
    return (
        data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08", b"\x1f\x8b", b"BZh", b"\xfd7zXZ\x00"))
        or data.startswith(UNSUPPORTED_MAGIC)
        or _is_tar(data)
        or zipfile.is_zipfile(io.BytesIO(data))
    )


def _extra_fields(data: bytes):
    """Parse ZIP and gzip's shared two-byte ID/two-byte length framing."""
    cursor = 0
    while cursor < len(data):
        if cursor + 4 > len(data):
            raise SecretScanError("credential scan archive extra field header truncated")
        kind = int.from_bytes(data[cursor : cursor + 2], "little")
        size = int.from_bytes(data[cursor + 2 : cursor + 4], "little")
        cursor += 4
        end = cursor + size
        if end > len(data):
            raise SecretScanError("credential scan archive extra field value truncated")
        yield kind, data[cursor:end]
        cursor = end


def _scan_extra_fields(data: bytes, location: str, depth: int, budget: _Budget) -> None:
    # Whole-field scanning alone leaves compressed values behind their TLV
    # headers opaque. Inspect every declared value with the same shared budget.
    _scan(data, location, depth, budget)
    for kind, value in _extra_fields(data):
        budget.member()
        _scan(value, location + f"-{kind:04x}", depth, budget)


def _gzip_metadata(data: bytes) -> list[tuple[str, bytes]]:
    """Return optional gzip header fields; body validation remains with gzip."""
    if len(data) < 10:
        raise SecretScanError("credential scan gzip archive header unreadable")
    flags = data[3]
    offset = 10
    fields = []
    if flags & 4:
        if offset + 2 > len(data):
            raise SecretScanError("credential scan gzip archive header unreadable")
        size = int.from_bytes(data[offset : offset + 2], "little")
        offset += 2
        if offset + size > len(data):
            raise SecretScanError("credential scan gzip archive header unreadable")
        fields.append(("extra", data[offset : offset + size]))
        offset += size
    for flag, name in ((8, "filename"), (16, "comment")):
        if flags & flag:
            end = data.find(b"\0", offset)
            if end < 0:
                raise SecretScanError("credential scan gzip archive header unreadable")
            fields.append((name, data[offset:end]))
            offset = end + 1
    if flags & 2 and offset + 2 > len(data):
        raise SecretScanError("credential scan gzip archive header unreadable")
    return fields


def _zip_directory_layout(data: bytes, archive: zipfile.ZipFile) -> None:
    """Reject bytes omitted by ZipFile's permissive end-record parser."""
    end = len(data) - 22 - len(archive.comment)
    if (
        end < 0
        or data[end : end + 4] != b"PK\x05\x06"
        or int.from_bytes(data[end + 20 : end + 22], "little") != len(archive.comment)
    ):
        raise SecretScanError("credential scan ZIP archive has uninspected trailing bytes")
    disk = int.from_bytes(data[end + 4 : end + 6], "little")
    directory_disk = int.from_bytes(data[end + 6 : end + 8], "little")
    disk_entries = int.from_bytes(data[end + 8 : end + 10], "little")
    total_entries = int.from_bytes(data[end + 10 : end + 12], "little")
    directory_size = int.from_bytes(data[end + 12 : end + 16], "little")
    directory_offset = int.from_bytes(data[end + 16 : end + 20], "little")
    if (
        disk
        or directory_disk
        or disk_entries != total_entries
        or total_entries != len(archive.infolist())
        or total_entries == 0xFFFF
        or directory_size == 0xFFFFFFFF
        or directory_offset == 0xFFFFFFFF
    ):
        raise SecretScanError("credential scan ZIP archive layout unsupported")
    if archive.start_dir < 0 or archive.start_dir + directory_size != end:
        raise SecretScanError("credential scan ZIP archive directory layout unsupported")
    cursor = archive.start_dir
    for _entry in archive.infolist():
        if cursor + 46 > end or data[cursor : cursor + 4] != b"PK\x01\x02":
            raise SecretScanError("credential scan ZIP archive central header unreadable")
        sizes = [int.from_bytes(data[cursor + field : cursor + field + 2], "little") for field in (28, 30, 32)]
        cursor += 46 + sum(sizes)
        if cursor > end:
            raise SecretScanError("credential scan ZIP archive central metadata truncated")
    if cursor != end:
        raise SecretScanError("credential scan ZIP archive has uninspected directory bytes")


def _zip_has_zip64_extra(extra: bytes) -> bool:
    zip64 = False
    for kind, _value in _extra_fields(extra):
        zip64 |= kind == 1
    return zip64


def _zip_descriptor_end(data: bytes, offset: int, entry: zipfile.ZipInfo, *, zip64: bool, end: int) -> int:
    """Validate an optional-signature data descriptor, including forced ZIP64."""
    width = 8 if zip64 else 4
    for signature_size in (4, 0):
        if signature_size and data[offset : offset + 4] != b"PK\x07\x08":
            continue
        start = offset + signature_size
        stop = start + 4 + 2 * width
        if stop > end:
            continue
        if (
            int.from_bytes(data[start : start + 4], "little") == entry.CRC
            and int.from_bytes(data[start + 4 : start + 4 + width], "little") == entry.compress_size
            and int.from_bytes(data[start + 4 + width : stop], "little") == entry.file_size
        ):
            return stop
    raise SecretScanError("credential scan ZIP archive data descriptor unreadable")


def _read_zip_member(data: bytes, entry: zipfile.ZipInfo, budget: _Budget) -> bytes:
    """Validate the entire compressed extent, not only ZipExtFile's output.

    ZipExtFile stops at the first codec EOF or declared output size, silently
    discarding unused compressed bytes. Validate EOF, size and CRC ourselves.
    """
    if entry.file_size < 0 or entry.file_size > budget.bytes_left:
        raise SecretScanError("credential scan archive member size exceeds limit")
    if entry.compress_type == zipfile.ZIP_STORED:
        payload = data
    else:
        if entry.compress_type == zipfile.ZIP_DEFLATED:
            decoder = zlib.decompressobj(-zlib.MAX_WBITS)
        elif entry.compress_type == zipfile.ZIP_BZIP2:
            decoder = bz2.BZ2Decompressor()
        elif entry.compress_type == zipfile.ZIP_LZMA:
            if len(data) < 9 or int.from_bytes(data[2:4], "little") != 5:
                raise SecretScanError("credential scan ZIP archive LZMA properties unsupported")
            # ZIP's five LZMA1 property bytes plus raw stream can be decoded
            # with a bounded FORMAT_ALONE decoder by adding its size field.
            data = data[4:9] + b"\xff" * 8 + data[9:]
            decoder = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE, memlimit=MAX_BYTES)
        else:
            raise SecretScanError("credential scan ZIP archive compression unsupported")
        payload = decoder.decompress(data, max_length=entry.file_size + 1)
        if not decoder.eof or decoder.unused_data:
            raise SecretScanError("credential scan ZIP archive compressed member has uninspected bytes")
    if len(payload) != entry.file_size or zlib.crc32(payload) & 0xFFFFFFFF != entry.CRC:
        raise SecretScanError("credential scan ZIP archive member size or checksum mismatch")
    return payload


def _pax_records(data: bytes):
    """Read every physical PAX record, including subsequently replaced values."""
    cursor = 0
    while cursor < len(data):
        match = PAX_RECORD_LENGTH.match(data, cursor)
        if match is None:
            raise SecretScanError("credential scan TAR archive PAX record unreadable")
        size = int(match[1])
        end = cursor + size
        if size < 5 or end > len(data) or data[end - 1 : end] != b"\n":
            raise SecretScanError("credential scan TAR archive PAX record truncated")
        name, separator, value = data[match.end() : end - 1].partition(b"=")
        if not separator or not name or b"\0" in name:
            raise SecretScanError("credential scan TAR archive PAX record invalid")
        yield name, value
        cursor = end


def _tar_padding_layout(data: bytes, end: int, location: str, depth: int, budget: _Budget) -> None:
    """Check physical records and inspect metadata hidden by TarInfo merging."""
    cursor = 0
    while cursor < end:
        raw_header = data[cursor : cursor + 512]
        header = tarfile.TarInfo.frombuf(raw_header, "utf-8", "surrogateescape")
        string_fields = [(0, 100), (157, 100), (265, 32), (297, 32)]
        if raw_header[257:265] == tarfile.GNU_MAGIC:
            # The GNU tail holds extension fields, not a ustar path prefix.
            # Nonzero extensions are unsupported rather than silently skipped.
            if raw_header[345:].strip(b"\0"):
                raise SecretScanError("credential scan GNU TAR archive extended header unsupported")
        else:
            string_fields.append((345, 155))
            if raw_header[500:].strip(b"\0"):
                raise SecretScanError("credential scan TAR archive has uninspected reserved header bytes")
        for start, size in string_fields:
            value = raw_header[start : start + size]
            _text, separator, padding = value.partition(b"\0")
            if separator and padding.strip(b"\0"):
                raise SecretScanError("credential scan TAR archive has uninspected header padding")
        if header.size < 0 or header.type == tarfile.GNUTYPE_SPARSE:
            raise SecretScanError("credential scan TAR archive layout unsupported")
        body_end = cursor + 512 + header.size
        next_header = (body_end + 511) // 512 * 512
        if next_header > end or data[body_end:next_header].strip(b"\0"):
            raise SecretScanError("credential scan TAR archive has uninspected alignment padding")
        body = data[cursor + 512 : body_end]
        if header.type in (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE):
            budget.member()
            for name, value in _pax_records(body):
                budget.member()
                _scan(name, location + "!pax-key", depth + 1, budget)
                _scan(value, location + "!pax-value", depth + 1, budget)
        elif header.type in (tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK):
            budget.member()
            name, separator, padding = body.partition(b"\0")
            if separator and padding.strip(b"\0"):
                raise SecretScanError("credential scan TAR archive has uninspected long-name padding")
            _scan(name, location + "!gnu-longname", depth + 1, budget)
        cursor = next_header
    if cursor != end:
        raise SecretScanError("credential scan TAR archive layout inconsistent")


def _decompress_streams(data: bytes, compression: str, location: str, depth: int, budget: _Budget) -> None:
    """Inspect each member and require explicit EOF instead of dropping tails.

    GzipFile/BZ2File/LZMAFile may concatenate members or ignore arbitrary
    trailing bytes. Incremental decoders expose unused_data and never allocate
    more decoded output than the remaining shared budget plus one sentinel.
    """
    remaining = data
    payloads = []
    magic = {"gzip": b"\x1f\x8b", "bzip2": b"BZh", "xz": b"\xfd7zXZ\x00"}[compression]
    while remaining:
        if not remaining.startswith(magic):
            # Gzip allows zero padding; XZ allows multiples of four zero bytes.
            padding = len(remaining) - len(remaining.lstrip(b"\0"))
            if compression == "bzip2" or not padding or (compression == "xz" and padding % 4):
                raise SecretScanError("credential scan compressed archive has uninspected trailing bytes")
            remaining = remaining[padding:]
            if not remaining:
                break
            if not remaining.startswith(magic):
                raise SecretScanError("credential scan compressed archive has uninspected trailing bytes")
        budget.member()
        if compression == "gzip":
            for name, metadata in _gzip_metadata(remaining):
                if metadata:
                    metadata_location = location + "!gzip-" + name
                    if name == "extra":
                        _scan_extra_fields(metadata, metadata_location, depth + 1, budget)
                    else:
                        _scan(metadata, metadata_location, depth + 1, budget)
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif compression == "bzip2":
            decoder = bz2.BZ2Decompressor()
        else:
            decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=MAX_BYTES)
        room = budget.bytes_left
        member_payload = decoder.decompress(remaining, max_length=room + 1)
        if len(member_payload) > room:
            raise SecretScanError("credential scan archive expansion resource limit exceeded")
        if not decoder.eof or len(decoder.unused_data) >= len(remaining):
            raise SecretScanError("credential scan compressed archive truncated or unreadable")
        # Inspect independent stream boundaries as well as their concatenation:
        # an ordinary prefix must not turn a following key into an identifier,
        # nor hide a complete compressed container in a later member.
        _scan(member_payload, location + "!" + compression + "-member", depth + 1, budget)
        payloads.append(member_payload)
        remaining = decoder.unused_data
    if len(payloads) > 1:
        # Also catch credentials or encoded carriers split across members.
        _scan(b"".join(payloads), location + "!compressed", depth + 1, budget)


def _scan_encodings(
    data: bytes, location: str, depth: int, budget: _Budget, encoding_depth: int, *, container_text: bool = False
) -> None:
    seen = {hashlib.sha256(data).digest()}
    # Binary archive padding is not UTF-16 text. Actual member and metadata
    # values receive their own full encoding pass after structural parsing.
    for kind, decoded in _decoded_payloads(data, utf16=not container_text):
        digest = hashlib.sha256(decoded).digest()
        if digest in seen:
            continue
        seen.add(digest)
        if encoding_depth >= MAX_ENCODING_DEPTH:
            raise SecretScanError("credential scan encoding nesting limit exceeded")
        budget.variant()
        # Escaping substitutions across a binary container can invalidate its
        # headers/CRC. Inspect that text without reparsing the mutated archive;
        # a Base64 token is an independent payload and still gets full parsing.
        _scan(
            decoded,
            location + "!" + kind,
            depth,
            budget,
            encoding_depth + 1,
            parse_container=not container_text or kind == "base64",
        )


def _scan(
    data: bytes,
    location: str,
    depth: int,
    budget: _Budget,
    encoding_depth: int = 0,
    *,
    parse_container: bool = True,
) -> None:
    budget.consume(len(data))
    safe_location = redact(location, extra_patterns=budget.extra_patterns)
    _record_matches(data, safe_location, budget)
    if not parse_container:
        _scan_encodings(data, location, depth, budget, encoding_depth, container_text=True)
        return
    is_zip = zipfile.is_zipfile(io.BytesIO(data))
    leading_zip = data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))
    if is_zip and not leading_zip:
        try:
            # Python source can contain a quoted EOCD signature. Only treat an
            # embedded signature as an SFX ZIP when its directory actually parses.
            with zipfile.ZipFile(io.BytesIO(data)):
                pass
        except (OSError, ValueError, zipfile.BadZipFile):
            is_zip = False
    compression = (
        "gzip"
        if data.startswith(b"\x1f\x8b")
        else "bzip2"
        if data.startswith(b"BZh")
        else "xz"
        if data.startswith(b"\xfd7zXZ\x00")
        else None
    )
    is_tar = _is_tar(data)
    if data.startswith(UNSUPPORTED_MAGIC) or location.lower().endswith(UNSUPPORTED_SUFFIXES):
        raise SecretScanError("credential scan archive format unsupported")
    if leading_zip and not is_zip:
        raise SecretScanError("credential scan ZIP archive unreadable")
    if not (is_zip or compression or is_tar):
        if location.lower().endswith(ARCHIVE_SUFFIXES):
            raise SecretScanError("credential scan cannot parse declared archive")
        _scan_encodings(data, location, depth, budget, encoding_depth)
        return
    # Parsers omit some local headers, concatenated-member metadata and trailing
    # bytes. Raw encoded text in those regions must not become a blind spot.
    _scan_encodings(data, location, depth, budget, encoding_depth, container_text=True)
    if depth >= MAX_DEPTH:
        raise SecretScanError("credential scan archive nesting limit exceeded")
    try:
        if is_zip:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > budget.members_left:
                    raise SecretScanError("credential scan archive member limit exceeded")
                _zip_directory_layout(data, archive)
                # ZIP supports arbitrary executable prefixes. Inspect them too,
                # rather than silently overlooking a preceding container.
                prefix_size = min((entry.header_offset for entry in entries), default=archive.start_dir)
                if prefix_size:
                    _scan(data[:prefix_size], location + "!zip-prefix", depth + 1, budget)
                if archive.comment:
                    _scan(archive.comment, location + "!zip-comment", depth + 1, budget)
                cursor = prefix_size
                for entry in sorted(entries, key=lambda item: item.header_offset):
                    budget.member()
                    # ZipInfo truncates filenames at NUL while retaining the
                    # original name for local-header comparison. Never let the
                    # hidden suffix bypass the label inspection.
                    if "\0" in entry.orig_filename:
                        raise SecretScanError("credential scan ZIP archive contains NUL in member name")
                    if entry.header_offset != cursor:
                        raise SecretScanError("credential scan ZIP archive has uninspected gap or overlap")
                    member_location = location + "!" + entry.filename
                    _scan_label(member_location, budget)
                    if entry.comment:
                        _scan(entry.comment, member_location + "!comment", depth + 1, budget)
                    if entry.extra:
                        _scan_extra_fields(entry.extra, member_location + "!extra", depth + 1, budget)
                    # Local and central extra fields may legitimately differ;
                    # ZipFile exposes only the central copy on ZipInfo.
                    local = entry.header_offset
                    if local < 0 or local + 30 > len(data) or data[local : local + 4] != b"PK\x03\x04":
                        raise SecretScanError("credential scan ZIP archive local header unreadable")
                    name_size = int.from_bytes(data[local + 26 : local + 28], "little")
                    extra_size = int.from_bytes(data[local + 28 : local + 30], "little")
                    extra_start = local + 30 + name_size
                    extra_end = extra_start + extra_size
                    if extra_end > archive.start_dir:
                        raise SecretScanError("credential scan ZIP archive local metadata truncated")
                    if extra_size:
                        _scan_extra_fields(
                            data[extra_start:extra_end], member_location + "!local-extra", depth + 1, budget
                        )
                    local_flags = int.from_bytes(data[local + 6 : local + 8], "little")
                    if entry.flag_bits & 0x41 or local_flags & 0x41:
                        raise SecretScanError("credential scan encrypted archive unsupported")
                    if entry.flag_bits & 0x20 or local_flags & 0x20:
                        raise SecretScanError("credential scan ZIP archive patched data unsupported")
                    encoding = "utf-8" if local_flags & 0x800 else "cp437"
                    local_name = data[local + 30 : extra_start].decode(encoding)
                    if (
                        local_name != entry.orig_filename
                        or int.from_bytes(data[local + 8 : local + 10], "little") != entry.compress_type
                    ):
                        raise SecretScanError("credential scan ZIP archive local metadata inconsistent")
                    if bool(local_flags & 8) != bool(entry.flag_bits & 8):
                        raise SecretScanError("credential scan ZIP archive descriptor flags inconsistent")
                    cursor = extra_end + entry.compress_size
                    if entry.compress_size < 0 or cursor > archive.start_dir:
                        raise SecretScanError("credential scan ZIP archive member overlaps directory")
                    if local_flags & 8:
                        zip64 = _zip_has_zip64_extra(data[extra_start:extra_end]) or any(
                            int.from_bytes(data[local + field : local + field + 4], "little") == 0xFFFFFFFF
                            for field in (18, 22)
                        )
                        cursor = _zip_descriptor_end(data, cursor, entry, zip64=zip64, end=archive.start_dir)
                    file_type = stat.S_IFMT(entry.external_attr >> 16)
                    if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                        raise SecretScanError("credential scan archive contains special member")
                    if entry.is_dir() and entry.file_size:
                        raise SecretScanError("credential scan archive contains non-empty directory")
                    if file_type == stat.S_IFDIR and not entry.is_dir():
                        raise SecretScanError("credential scan archive has inconsistent directory metadata")
                    payload = _read_zip_member(data[extra_end : extra_end + entry.compress_size], entry, budget)
                    _scan(payload, member_location, depth + 1, budget)
                if cursor != archive.start_dir:
                    raise SecretScanError("credential scan ZIP archive has uninspected gap before directory")
        elif compression:
            _decompress_streams(data, compression, location, depth, budget)
        else:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
                for entry in archive:
                    budget.member()
                    member_location = location + "!" + entry.name
                    _scan_label(member_location, budget)
                    if entry.isdir():
                        if entry.size:
                            raise SecretScanError("credential scan archive contains non-empty directory")
                        continue
                    if not entry.isfile():
                        raise SecretScanError("credential scan archive contains special member")
                    member = archive.extractfile(entry)
                    if member is None:
                        raise SecretScanError("credential scan archive member unreadable")
                    with member:
                        payload = _read_member(member, entry.size, budget)
                    _scan(payload, member_location, depth + 1, budget)
                _tar_padding_layout(data, archive.offset, location, depth, budget)
                if archive.offset < 0 or archive.offset > len(data) or data[archive.offset :].strip(b"\0"):
                    raise SecretScanError("credential scan TAR archive has uninspected trailing bytes")
    except (
        OSError,
        EOFError,
        ValueError,
        RuntimeError,
        NotImplementedError,
        tarfile.TarError,
        lzma.LZMAError,
        zlib.error,
    ) as exc:
        if isinstance(exc, SecretScanError):
            raise
        raise SecretScanError("credential scan archive unreadable") from None


def scan_secrets(
    data: bytes, location: str = "payload", *, extra_patterns: tuple[re.Pattern[str], ...] = ()
) -> list[SecretFinding]:
    """Return rule, digest and safe location; no network or file extraction.

    Raises SecretScanError on incomplete inspection, retaining any partial safe
    findings in ``exc.findings``. Both findings and errors must block publication.
    Limits apply to the complete expanded/decoded input, not each member alone.
    """
    budget = _Budget(MAX_BYTES, MAX_MEMBERS, MAX_VARIANTS, extra_patterns=extra_patterns)
    try:
        _scan_label(location, budget)
        _scan(data, location, 0, budget)
    except SecretScanError as exc:
        raise SecretScanError(str(exc), findings=tuple(_sorted_findings(budget))) from None
    return _sorted_findings(budget)


def _sorted_findings(budget: _Budget) -> list[SecretFinding]:
    return sorted(budget.findings, key=lambda item: (item.location, item.rule, item.fingerprint))
