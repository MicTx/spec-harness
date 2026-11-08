"""Publication credential scanning uses synthetic tokens, never live credentials."""

from __future__ import annotations

import base64
import bz2
import gzip
import io
import lzma
import re
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import public_secret_scan as scanner  # noqa: E402
from export_public_repo import PublicExportError, _scan  # noqa: E402


def synthetic_key() -> str:
    return "sk-" + "0123456789abcdef" * 2


def zip_payload(name: str, data: bytes) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, data)
    return stream.getvalue()


def tar_payload(name: str, data: bytes) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        entry = tarfile.TarInfo(name)
        entry.size = len(data)
        archive.addfile(entry, io.BytesIO(data))
    return stream.getvalue()


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16-le", "utf-16-be"])
def test_credentials_detected_without_returning_values(encoding):
    key = synthetic_key()
    findings = scanner.scan_secrets(("credential=" + key).encode(encoding), "example.txt")
    assert len(findings) == 1
    assert findings[0].rule == "api-key"
    assert len(findings[0].fingerprint) == 16
    assert key not in repr(findings)


@pytest.mark.parametrize("variant", ["base64", "json-escape", "hex-escape", "percent"])
def test_common_encoding_does_not_hide_credentials(variant):
    key = synthetic_key()
    if variant == "base64":
        payload = base64.b64encode(key.encode())
    elif variant == "json-escape":
        payload = "".join(f"\\u{ord(char):04x}" for char in key).encode()
    elif variant == "hex-escape":
        payload = "".join(f"\\x{ord(char):02x}" for char in key).encode()
    else:
        payload = "".join(f"%{ord(char):02X}" for char in key).encode()
    assert scanner.scan_secrets(payload)


@pytest.mark.parametrize("pack", [zip_payload, tar_payload, lambda name, data: gzip.compress(data)])
def test_archive_members_are_inspected(pack):
    key = synthetic_key()
    findings = scanner.scan_secrets(pack("config.txt", key.encode()), "download")
    assert findings
    assert key not in repr(findings)


def test_nested_archive_is_inspected_without_extracting(tmp_path):
    payload = zip_payload("../outside.zip", zip_payload("config.txt", synthetic_key().encode()))
    assert scanner.scan_secrets(payload, "payload.zip")
    assert list(tmp_path.iterdir()) == []


def test_archive_names_and_diagnostic_labels_are_redacted():
    key = synthetic_key()
    findings = scanner.scan_secrets(zip_payload(key + ".txt", b"safe"), key + ".zip")
    assert findings
    assert key not in repr(findings)
    assert "[REDACTED]" in repr(findings)


@pytest.mark.parametrize(
    "payload,label",
    [
        (b"PK\x03\x04broken", "payload.zip"),
        (b"\x1f\x8bbroken", "payload.gz"),
        (b"BZhopaque", "payload.bz2"),
        (b"\xfd7zXZ\x00opaque", "payload.xz"),
    ],
)
def test_malformed_archive_fails_closed_without_echo(payload, label):
    with pytest.raises(scanner.SecretScanError, match="archive"):
        scanner.scan_secrets(payload, label)


def test_archive_expansion_limit_fails_closed(monkeypatch):
    monkeypatch.setattr(scanner, "MAX_BYTES", 1024)
    with pytest.raises(scanner.SecretScanError, match="limit"):
        scanner.scan_secrets(gzip.compress(b"a" * 4096), "payload.gz")


def test_nesting_limit_fails_closed():
    data = b"safe"
    for _ in range(scanner.MAX_DEPTH + 1):
        data = zip_payload("nested.zip", data)
    with pytest.raises(scanner.SecretScanError, match="nesting limit"):
        scanner.scan_secrets(data, "payload.zip")


def test_long_base64_values_are_not_silently_skipped():
    payload = b"a" * 1024 + b" " + synthetic_key().encode() + b" " + b"b" * 1024
    assert scanner.scan_secrets(base64.b64encode(payload))


def test_encoded_labels_are_not_written_to_findings():
    label = base64.b64encode(synthetic_key().encode()).decode()
    findings = scanner.scan_secrets(b"safe", label)
    assert findings
    assert label not in repr(findings)


def test_corrupted_renamed_zip_fails_closed():
    with pytest.raises(scanner.SecretScanError, match="archive"):
        scanner.scan_secrets(b"PK\x03\x04broken", "renamed.bin")


def test_tar_directory_count_is_bounded(monkeypatch):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for index in range(4):
            entry = tarfile.TarInfo(f"dir-{index}")
            entry.type = tarfile.DIRTYPE
            archive.addfile(entry)
    monkeypatch.setattr(scanner, "MAX_MEMBERS", 2)
    with pytest.raises(scanner.SecretScanError, match="member limit"):
        scanner.scan_secrets(stream.getvalue())


def test_ordinary_task_identifiers_and_environment_references_are_not_keys():
    assert scanner.scan_secrets(b"task-do-something-with-a-long-name DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}") == []


@pytest.mark.parametrize("pack", [zip_payload, tar_payload])
def test_existing_export_scan_blocks_archived_credentials(pack):
    key = synthetic_key()
    with pytest.raises(PublicExportError) as error:
        _scan("fixture.bin", pack("settings.json", key.encode()))
    assert key not in str(error.value)


def test_export_scan_blocks_encoded_credentials():
    key = synthetic_key()
    with pytest.raises(PublicExportError) as error:
        _scan("settings.json", base64.b64encode(key.encode()))
    assert key not in str(error.value)


@pytest.mark.parametrize("compress", [bz2.compress, lzma.compress])
def test_other_standard_library_compression_is_inspected(compress):
    assert scanner.scan_secrets(compress(synthetic_key().encode()), "renamed.bin")
    assert scanner.scan_secrets(compress(tar_payload("settings", synthetic_key().encode())))


def test_v7_tar_without_ustar_magic_is_inspected():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        entry = tarfile.TarInfo("settings")
        entry.size = len(synthetic_key())
        archive.addfile(entry, io.BytesIO(synthetic_key().encode()))
    data = bytearray(stream.getvalue())
    data[257:265] = b"\0" * 8
    data[148:156] = b" " * 8
    data[148:156] = f"{sum(data[:512]):06o}\0 ".encode()
    assert scanner.scan_secrets(bytes(data), "renamed.bin")


@pytest.mark.parametrize("payload", [b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07", b"\x28\xb5\x2f\xfd"])
def test_unsupported_container_magic_fails_closed(payload):
    with pytest.raises(scanner.SecretScanError, match="unsupported"):
        scanner.scan_secrets(payload + b"opaque", "renamed.bin")


@pytest.mark.parametrize("name", ["opaque.7z", "opaque.rar", "opaque.zst", "opaque.bz2", "opaque.xz"])
def test_declared_uninspectable_containers_fail_closed(name):
    with pytest.raises(scanner.SecretScanError, match="archive"):
        scanner.scan_secrets(b"opaque", name)


def test_multiple_and_composed_encodings_are_inspected():
    key = synthetic_key().encode()
    assert scanner.scan_secrets(base64.b64encode(base64.b64encode(key)))
    assert scanner.scan_secrets(base64.b64encode(zip_payload("settings", key)))
    assert scanner.scan_secrets(base64.b64encode(key.decode().encode("utf-16-le")))
    percent = "".join(f"%{char:02X}" for char in key)
    assert scanner.scan_secrets("".join(f"\\u{ord(char):04x}" for char in percent).encode())


def test_over_nested_encoding_is_incomplete_not_clean():
    data = synthetic_key().encode()
    for _ in range(scanner.MAX_ENCODING_DEPTH + 2):
        data = base64.b64encode(data)
    with pytest.raises(scanner.SecretScanError, match="encoding nesting limit"):
        scanner.scan_secrets(data)


def test_encoding_expansion_budget_is_shared(monkeypatch):
    payload = base64.b64encode(b"!" * 500)
    monkeypatch.setattr(scanner, "MAX_BYTES", len(payload) + 30)
    with pytest.raises(scanner.SecretScanError, match="resource limit"):
        scanner.scan_secrets(payload)


def test_zip_directory_budget_is_shared_across_nested_archives(monkeypatch):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("one/", b"")
        archive.writestr("two/", b"")
        archive.writestr("inner.zip", zip_payload("settings", b"safe"))
    monkeypatch.setattr(scanner, "MAX_MEMBERS", 3)
    with pytest.raises(scanner.SecretScanError, match="member limit"):
        scanner.scan_secrets(stream.getvalue())


def test_partial_safe_findings_survive_an_uninspectable_member():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("first", synthetic_key())
        archive.writestr("second.zip", b"PK\x03\x04broken")
    with pytest.raises(scanner.SecretScanError) as error:
        scanner.scan_secrets(stream.getvalue())
    assert error.value.findings
    assert synthetic_key() not in repr(error.value.findings)
    assert synthetic_key() not in str(error.value)


def test_nested_encoded_label_is_suppressed():
    label = base64.b64encode(base64.b64encode(synthetic_key().encode())).decode()
    findings = scanner.scan_secrets(b"safe", label)
    assert findings
    assert label not in repr(findings)
    assert scanner.redact(label) == "[REDACTED]"


def test_scanner_source_with_archive_signatures_is_not_an_archive():
    assert scanner.scan_secrets(Path(scanner.__file__).read_bytes()) == []


def test_legitimate_archive_and_escaped_source_are_clean():
    assert scanner.scan_secrets(zip_payload("example.txt", b"hello world")) == []
    assert scanner.scan_secrets(tar_payload("example.txt", b"hello world")) == []
    assert scanner.scan_secrets(b'pattern = r"\\x00"') == []


def test_export_errors_do_not_disclose_dynamic_pattern_or_secret_label():
    host = "private.example.invalid"
    with pytest.raises(PublicExportError) as error:
        _scan("README.md", host.encode(), (re.compile(re.escape(host)),))
    assert host not in str(error.value)
    key = synthetic_key()
    with pytest.raises(PublicExportError) as error:
        _scan(key + ".txt", key.encode())
    assert key not in str(error.value)


def test_export_gate_blocks_incomplete_inspection():
    with pytest.raises(PublicExportError, match="inspection incomplete"):
        _scan("archive.zip", b"PK\x03\x04broken")


def test_scanner_is_source_tooling_not_installed_runtime():
    from payload_contract import SCRIPT_BLACKLIST

    assert "public_secret_scan.py" in SCRIPT_BLACKLIST


def test_private_patterns_are_inspected_inside_archives_and_encodings():
    host = "private.example.invalid"
    patterns = (re.compile(re.escape(host)),)
    payloads = [
        host.encode(),
        base64.b64encode(host.encode()),
        zip_payload("example.txt", host.encode()),
        gzip.compress(host.encode()),
        zip_payload(base64.b64encode(host.encode()).decode(), b"safe"),
    ]
    for payload in payloads:
        with pytest.raises(PublicExportError) as error:
            _scan("archive.bin", payload, patterns)
        assert host not in str(error.value)


def test_private_pattern_in_path_is_not_echoed():
    host = "private.example.invalid"
    with pytest.raises(PublicExportError) as error:
        _scan(host + ".txt", b"safe", (re.compile(re.escape(host)),))
    assert host not in str(error.value)


def test_zip_global_and_member_metadata_are_inspected():
    key = synthetic_key().encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.comment = base64.b64encode(key)
        info = zipfile.ZipInfo("safe.txt")
        info.comment = base64.b64encode(key)
        archive.writestr(info, b"safe")
    findings = scanner.scan_secrets(stream.getvalue())
    assert any("zip-comment" in item.location for item in findings)
    assert any("safe.txt!comment" in item.location for item in findings)
    assert synthetic_key() not in repr(findings)


def test_self_extracting_zip_prefix_is_inspected():
    prefix = gzip.compress(synthetic_key().encode())
    payload = prefix + zip_payload("safe.txt", b"safe")
    assert scanner.scan_secrets(payload, "self-extracting.bin")


def test_gzip_header_metadata_is_inspected():
    stream = io.BytesIO()
    with gzip.GzipFile(filename=base64.b64encode(synthetic_key().encode()).decode(), mode="wb", fileobj=stream) as f:
        f.write(b"safe")
    findings = scanner.scan_secrets(stream.getvalue(), "safe.gz")
    assert any("gzip-filename" in item.location for item in findings)


def test_pax_metadata_is_inspected():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
        info = tarfile.TarInfo("safe.txt")
        info.pax_headers = {"comment": base64.b64encode(synthetic_key().encode()).decode()}
        info.size = 4
        archive.addfile(info, io.BytesIO(b"safe"))
    assert any("pax" in item.location for item in scanner.scan_secrets(stream.getvalue()))


@pytest.mark.parametrize("compress", [gzip.compress, bz2.compress, lzma.compress])
def test_compressed_tar_pax_values_are_inspected(compress):
    hidden = compress(synthetic_key().encode())
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
        info = tarfile.TarInfo("safe.txt")
        info.pax_headers = {"comment": hidden.decode("utf-8", "surrogateescape")}
        info.size = 4
        archive.addfile(info, io.BytesIO(b"safe"))
    findings = scanner.scan_secrets(stream.getvalue())
    assert findings, "a compressed PAX value was incorrectly reported clean"
    assert synthetic_key() not in repr(findings)


@pytest.mark.parametrize("header_type", [tarfile.XHDTYPE, tarfile.XGLTYPE])
def test_overwritten_pax_values_do_not_disappear_from_inspection(header_type):
    records = []
    for value in (gzip.compress(synthetic_key().encode()), b"safe"):
        body = b"comment=" + value + b"\n"
        size = len(body) + 2
        while size != len(body) + len(str(size)) + 1:
            size = len(body) + len(str(size)) + 1
        records.append(str(size).encode() + b" " + body)
    metadata = b"".join(records)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        header = tarfile.TarInfo("pax")
        header.type = header_type
        header.size = len(metadata)
        archive.addfile(header, io.BytesIO(metadata))
        entry = tarfile.TarInfo("safe.txt")
        entry.size = 4
        archive.addfile(entry, io.BytesIO(b"safe"))
    findings = scanner.scan_secrets(stream.getvalue())
    assert any("pax-value" in finding.location for finding in findings)
    assert synthetic_key() not in repr(findings)


@pytest.mark.parametrize("format,field", [(tarfile.GNU_FORMAT, (369, 143)), (tarfile.USTAR_FORMAT, (500, 12))])
def test_tar_reserved_header_bytes_are_not_an_uninspected_carrier(format, field):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=format) as archive:
        entry = tarfile.TarInfo("safe.txt")
        entry.size = 4
        archive.addfile(entry, io.BytesIO(b"safe"))
    payload = bytearray(stream.getvalue())
    start, size = field
    hidden = gzip.compress(synthetic_key().encode())[:size]
    payload[start : start + len(hidden)] = hidden
    payload[148:156] = b" " * 8
    payload[148:156] = f"{sum(payload[:512]):06o}\0 ".encode()
    with pytest.raises(scanner.SecretScanError, match="archive"):
        scanner.scan_secrets(bytes(payload))


def test_zip_nul_terminated_filename_does_not_hide_compressed_bytes():
    hidden = gzip.compress(synthetic_key().encode())
    payload = bytearray(zip_payload("x" * len(hidden), b"safe"))
    central = payload.index(b"PK\x01\x02")
    payload[30 : 30 + len(hidden)] = hidden
    payload[central + 46 : central + 46 + len(hidden)] = hidden
    with pytest.raises(scanner.SecretScanError, match="archive") as error:
        scanner.scan_secrets(bytes(payload))
    assert synthetic_key() not in str(error.value)


@pytest.mark.parametrize("field", [(0, 100), (157, 100), (265, 32), (297, 32), (345, 155)])
def test_tar_string_field_padding_is_not_an_uninspected_carrier(field):
    payload = bytearray(gzip.decompress(tar_payload("safe.txt", b"safe")))
    start, size = field
    # Short fields carry a truncated stream. Nonzero data hidden after a NUL
    # must fail closed whether or not a complete compressed value fits.
    hidden = gzip.compress(synthetic_key().encode())[: size - 1]
    payload[start : start + size] = (b"\0" + hidden).ljust(size, b"\0")
    payload[148:156] = b" " * 8
    payload[148:156] = f"{sum(payload[:512]):06o}\0 ".encode()
    with pytest.raises(scanner.SecretScanError, match="archive"):
        scanner.scan_secrets(bytes(payload))


def test_ordinary_gnu_long_names_remain_inspectable():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.GNU_FORMAT) as archive:
        info = tarfile.TarInfo("directory/" * 20 + "safe.txt")
        info.size = 4
        archive.addfile(info, io.BytesIO(b"safe"))
    assert scanner.scan_secrets(stream.getvalue()) == []


def test_zip_special_members_and_nonempty_directories_fail_closed():
    for name, mode, contents in (("link", 0o120777, b"target"), ("dir/", 0o40755, b"hidden")):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = mode << 16
            archive.writestr(info, contents)
        with pytest.raises(scanner.SecretScanError, match="archive"):
            scanner.scan_secrets(stream.getvalue())


def test_unsupported_zip_compression_fails_closed():
    payload = bytearray(zip_payload("safe.txt", b"safe"))
    local = payload.find(b"PK\x03\x04")
    central = payload.find(b"PK\x01\x02")
    payload[local + 8 : local + 10] = (99).to_bytes(2, "little")
    payload[central + 10 : central + 12] = (99).to_bytes(2, "little")
    with pytest.raises(scanner.SecretScanError, match="archive"):
        scanner.scan_secrets(bytes(payload))


def test_encoded_archive_trailers_are_inspected():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        info = tarfile.TarInfo("safe.txt")
        info.size = 4
        archive.addfile(info, io.BytesIO(b"safe"))
    for archive_data in (zip_payload("safe.txt", b"safe"), stream.getvalue()):
        payload = archive_data + b"\n" + base64.b64encode(synthetic_key().encode())
        with pytest.raises(scanner.SecretScanError, match="archive") as error:
            scanner.scan_secrets(payload)
        assert error.value.findings


def test_gzip_trailing_nonmember_bytes_fail_closed():
    payload = gzip.compress(b"safe") + b"\n" + base64.b64encode(synthetic_key().encode())
    with pytest.raises(scanner.SecretScanError, match="archive") as error:
        scanner.scan_secrets(payload)
    assert error.value.findings


def test_encoded_local_zip_metadata_is_inspected_when_central_metadata_differs():
    encoded = base64.b64encode(synthetic_key().encode())
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        info = zipfile.ZipInfo("safe.txt")
        info.extra = b"\xfe\xca" + len(encoded).to_bytes(2, "little") + encoded
        archive.writestr(info, b"safe")
    payload = bytearray(stream.getvalue())
    central = payload.find(b"PK\x01\x02")
    name_size = int.from_bytes(payload[central + 28 : central + 30], "little")
    extra_start = central + 46 + name_size
    payload[extra_start + 4 : extra_start + 4 + len(encoded)] = b"!" * len(encoded)
    assert scanner.scan_secrets(bytes(payload))


@pytest.mark.parametrize("compress", [gzip.compress, bz2.compress, lzma.compress])
@pytest.mark.parametrize("copy", ["both", "local", "central"])
def test_compressed_zip_extra_field_values_are_inspected(compress, copy):
    hidden = compress(synthetic_key().encode())
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        info = zipfile.ZipInfo("safe.txt")
        info.extra = b"\xfe\xca" + len(hidden).to_bytes(2, "little") + hidden
        archive.writestr(info, b"safe")
    payload = bytearray(stream.getvalue())
    if copy != "both":
        header = 0 if copy == "central" else payload.index(b"PK\x01\x02")
        name_field, header_size = (26, 30) if header == 0 else (28, 46)
        name_size = int.from_bytes(payload[header + name_field : header + name_field + 2], "little")
        value_start = header + header_size + name_size + 4
        payload[value_start : value_start + len(hidden)] = b"!" * len(hidden)
    findings = scanner.scan_secrets(bytes(payload))
    assert findings, "a compressed ZIP extra-field value was incorrectly reported clean"
    assert synthetic_key() not in repr(findings)


@pytest.mark.parametrize("compress", [gzip.compress, bz2.compress, lzma.compress])
def test_compressed_gzip_extra_subfield_values_are_inspected(compress):
    hidden = compress(synthetic_key().encode())
    extra = b"UT" + len(hidden).to_bytes(2, "little") + hidden
    payload = bytearray(gzip.compress(b"safe"))
    payload[3] |= 4
    payload[10:10] = len(extra).to_bytes(2, "little") + extra
    findings = scanner.scan_secrets(bytes(payload))
    assert findings, "a compressed gzip extra-subfield value was incorrectly reported clean"
    assert synthetic_key() not in repr(findings)


@pytest.mark.parametrize("extra", [b"", b"UT\x04\x00safe", b"UT\x00\x00ab\x04\x00safe"])
def test_ordinary_gzip_extra_subfields_remain_inspectable(extra):
    payload = bytearray(gzip.compress(b"safe"))
    payload[3] |= 4
    payload[10:10] = len(extra).to_bytes(2, "little") + extra
    assert scanner.scan_secrets(bytes(payload)) == []


@pytest.mark.parametrize("extra", [b"U", b"UT\x05\x00!", b"UT\x04\x00safe!"])
def test_malformed_gzip_extra_subfields_fail_closed(extra):
    payload = bytearray(gzip.compress(b"safe"))
    payload[3] |= 4
    payload[10:10] = len(extra).to_bytes(2, "little") + extra
    with pytest.raises(scanner.SecretScanError, match="archive extra field"):
        scanner.scan_secrets(bytes(payload))


def test_extra_subfields_share_the_archive_member_budget(monkeypatch):
    extra = b"UT\x04\x00safe"
    payload = bytearray(gzip.compress(b"safe"))
    payload[3] |= 4
    payload[10:10] = len(extra).to_bytes(2, "little") + extra
    monkeypatch.setattr(scanner, "MAX_MEMBERS", 1)
    with pytest.raises(scanner.SecretScanError, match="archive member limit"):
        scanner.scan_secrets(bytes(payload))


def test_concatenated_gzip_metadata_is_inspected():
    stream = io.BytesIO()
    with gzip.GzipFile(filename=base64.b64encode(synthetic_key().encode()).decode(), mode="wb", fileobj=stream) as f:
        f.write(b"safe")
    assert scanner.scan_secrets(gzip.compress(b"safe") + stream.getvalue())


def test_archive_with_ordinary_escaped_source_is_clean():
    assert scanner.scan_secrets(zip_payload("source.py", b'pattern = r"\\x00"')) == []


def test_compressed_encoded_labels_are_suppressed_and_fail_closed():
    encoded = base64.b64encode(gzip.compress(synthetic_key().encode())).decode()
    assert scanner.redact(encoded) == "[REDACTED]"
    with pytest.raises(scanner.SecretScanError) as error:
        scanner.scan_secrets(b"safe", encoded)
    assert encoded not in repr(error.value.findings)
    assert encoded not in str(error.value)
    with pytest.raises(PublicExportError) as export_error:
        _scan(encoded + ".txt", b"safe")
    assert encoded not in str(export_error.value)


@pytest.mark.parametrize("outer", ["zip", "tar", "gzip", "bzip2", "xz"])
@pytest.mark.parametrize("trailer", ["gzip", "bzip2", "xz", "zip", "tar"])
def test_unparsed_archive_trailers_never_count_as_clean(outer, trailer):
    packers = {
        "zip": lambda data: zip_payload("settings.txt", data),
        "tar": lambda data: gzip.decompress(tar_payload("settings.txt", data)),
        "gzip": gzip.compress,
        "bzip2": bz2.compress,
        "xz": lzma.compress,
    }
    payload = packers[outer](b"safe") + packers[trailer](synthetic_key().encode())
    try:
        findings = scanner.scan_secrets(payload)
    except scanner.SecretScanError as error:
        assert "archive" in str(error)
        assert synthetic_key() not in str(error)
    else:
        assert findings, "an unparsed compressed trailer was incorrectly reported clean"
        assert synthetic_key() not in repr(findings)


@pytest.mark.parametrize("pack", [gzip.compress, bz2.compress, lzma.compress])
def test_clean_concatenated_compression_streams_remain_inspectable(pack):
    assert scanner.scan_secrets(pack(b"hello ") + pack(b"world")) == []


@pytest.mark.parametrize("pack", [bz2.compress, lzma.compress])
def test_compression_trailing_opaque_bytes_fail_closed(pack):
    with pytest.raises(scanner.SecretScanError, match="archive"):
        scanner.scan_secrets(pack(b"safe") + b"\0opaque\xff\x01")


def test_zip_gap_before_central_directory_is_not_silently_ignored():
    payload = bytearray(zip_payload("safe.txt", b"safe"))
    central = payload.index(b"PK\x01\x02")
    end = payload.rindex(b"PK\x05\x06")
    gap = gzip.compress(synthetic_key().encode())
    payload[end + 16 : end + 20] = (central + len(gap)).to_bytes(4, "little")
    payload[central:central] = gap
    with pytest.raises(scanner.SecretScanError, match="archive"):
        scanner.scan_secrets(bytes(payload))


@pytest.mark.parametrize("pack", [gzip.compress, bz2.compress, lzma.compress])
def test_keys_spanning_concatenated_stream_boundaries_are_inspected(pack):
    key = synthetic_key().encode()
    assert scanner.scan_secrets(pack(key[:12]) + pack(key[12:]))


@pytest.mark.parametrize("pack,padding", [(gzip.compress, b"\0" * 3), (lzma.compress, b"\0" * 8)])
def test_legal_compression_padding_remains_inspectable(pack, padding):
    assert scanner.scan_secrets(pack(b"hello ") + padding + pack(b"world") + padding) == []


@pytest.mark.parametrize("force_zip64", [False, True])
def test_streaming_zip_data_descriptors_are_inspected(force_zip64):
    class Unseekable(io.BytesIO):
        def seekable(self):
            return False

        def seek(self, *args):
            raise OSError("unseekable")

    stream = Unseekable()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        with archive.open("safe.txt", "w", force_zip64=force_zip64) as member:
            member.write(b"hello world")
    assert scanner.scan_secrets(stream.getvalue()) == []
    corrupted = bytearray(stream.getvalue())
    descriptor = corrupted.index(b"PK\x07\x08")
    corrupted[descriptor + 4] ^= 1
    with pytest.raises(scanner.SecretScanError, match="archive"):
        scanner.scan_secrets(bytes(corrupted))


def test_empty_tar_with_a_compressed_trailer_never_counts_as_clean():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w"):
        pass
    assert scanner.scan_secrets(stream.getvalue()) == []
    with pytest.raises(scanner.SecretScanError, match="archive"):
        scanner.scan_secrets(stream.getvalue() + gzip.compress(synthetic_key().encode()))


def test_tar_member_alignment_padding_is_not_an_uninspected_carrier():
    payload = bytearray(gzip.decompress(tar_payload("safe.txt", b"safe")))
    hidden = gzip.compress(synthetic_key().encode())
    payload[516 : 516 + len(hidden)] = hidden
    with pytest.raises(scanner.SecretScanError, match="archive"):
        scanner.scan_secrets(bytes(payload))


@pytest.mark.parametrize("name,content", [("safe.txt", b"safe"), ("safe/", b"")])
def test_zip_compressed_member_tail_is_not_silently_discarded(name, content):
    payload = bytearray(zip_payload(name, content))
    central = payload.index(b"PK\x01\x02")
    end = payload.rindex(b"PK\x05\x06")
    hidden = gzip.compress(synthetic_key().encode())
    size = int.from_bytes(payload[18:22], "little") + len(hidden)
    payload[18:22] = size.to_bytes(4, "little")
    payload[central + 20 : central + 24] = size.to_bytes(4, "little")
    payload[end + 16 : end + 20] = (central + len(hidden)).to_bytes(4, "little")
    payload[central:central] = hidden
    with pytest.raises(scanner.SecretScanError, match="archive"):
        scanner.scan_secrets(bytes(payload))


@pytest.mark.parametrize("pack", [gzip.compress, bz2.compress])
def test_single_stream_does_not_charge_a_redundant_concatenation_pass(pack, monkeypatch):
    monkeypatch.setattr(scanner, "MAX_BYTES", 4096)
    assert scanner.scan_secrets(pack(b"!" * 3000)) == []
