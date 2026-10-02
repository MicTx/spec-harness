#!/usr/bin/env python3
"""Invisible attribution mark for distributed skill copies.

The mark is a zero-width ciphertext appended to an entry document. The key
and the identity file stay in the authoring tree; neither is copied into an
install or export. This raises the cost of casual recovery. It does not make
the mark impossible to strip or cryptanalyze.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
from pathlib import Path

MARK = "\u2063"
PAD = "\u2060\u200b"
PAYLOAD_KEYS = ("holder", "contact", "channel", "commercial")


def load_identity(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("identity file must be a JSON object")
    missing = [key for key in PAYLOAD_KEYS if not str(data.get(key, "")).strip()]
    if missing:
        raise ValueError("identity file missing: " + ", ".join(missing))
    return {key: str(data[key]).strip() for key in PAYLOAD_KEYS}


def load_key(path: Path) -> bytes:
    raw = path.read_bytes().strip()
    if len(raw) < 32:
        raise ValueError("watermark key must be at least 32 bytes")
    return raw


def _visible(text: str) -> str:
    return "".join(ch for ch in text if ch not in MARK + PAD)


def _pack(identity: dict[str, str], key: bytes) -> str:
    body = json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    digest = hmac.new(key, body, hashlib.sha256).digest()
    blob = body + b"\x1e" + digest
    stream = hashlib.sha256(key).digest()
    mixed = bytes(byte ^ stream[index % len(stream)] for index, byte in enumerate(blob))
    bits = "".join(f"{byte:08b}" for byte in mixed)
    return MARK + "".join(PAD[int(bit)] for bit in bits) + MARK


def stamp(text: str, identity: dict[str, str], key: bytes) -> str:
    """Return text with one invisible mark, replacing any earlier mark."""
    base = strip(text)
    if not base.endswith("\n"):
        base += "\n"
    return base + _pack(identity, key)


def strip(text: str) -> str:
    start = text.find(MARK)
    if start < 0:
        return text
    end = text.find(MARK, start + 1)
    if end < 0:
        return text
    return text[:start] + text[end + len(MARK) :]


def verify(text: str, key: bytes) -> dict[str, str]:
    """Return the payload when the mark is intact, else raise ValueError."""
    start = text.find(MARK)
    end = text.find(MARK, start + 1) if start >= 0 else -1
    if start < 0 or end < 0:
        raise ValueError("watermark missing")
    hidden = text[start + len(MARK) : end]
    if not hidden or any(ch not in PAD for ch in hidden):
        raise ValueError("watermark malformed")
    bits = "".join("0" if ch == PAD[0] else "1" for ch in hidden)
    if len(bits) % 8:
        raise ValueError("watermark truncated")
    mixed = bytes(int(bits[index : index + 8], 2) for index in range(0, len(bits), 8))
    stream = hashlib.sha256(key).digest()
    blob = bytes(byte ^ stream[index % len(stream)] for index, byte in enumerate(mixed))
    if len(blob) < 33 or blob[-33:-32] != b"\x1e":
        raise ValueError("watermark failed integrity check")
    body, digest = blob[:-33], blob[-32:]
    expected = hmac.new(key, body, hashlib.sha256).digest()
    if not hmac.compare_digest(digest, expected):
        raise ValueError("watermark failed integrity check")
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("watermark payload is not an object")
    return {key_name: str(payload[key_name]) for key_name in PAYLOAD_KEYS}


def stamp_tree(root: Path, filenames: tuple[str, ...], identity: dict[str, str], key: bytes) -> list[str]:
    stamped: list[str] = []
    for name in filenames:
        path = root / name
        if not path.is_file():
            continue
        path.write_text(stamp(path.read_text(encoding="utf-8"), identity, key), encoding="utf-8")
        stamped.append(name)
    if not stamped:
        raise ValueError("no watermark target found under " + str(root))
    return stamped


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stamp or verify an invisible skill watermark.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    stamp_cmd = sub.add_parser("stamp-tree")
    stamp_cmd.add_argument("--root", required=True)
    stamp_cmd.add_argument("--identity", required=True)
    stamp_cmd.add_argument("--key", required=True)
    stamp_cmd.add_argument("--file", action="append", dest="files", required=True)
    verify_cmd = sub.add_parser("verify")
    verify_cmd.add_argument("--file", required=True)
    verify_cmd.add_argument("--key", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    key = load_key(Path(args.key))
    if args.cmd == "stamp-tree":
        stamped = stamp_tree(Path(args.root), tuple(args.files), load_identity(Path(args.identity)), key)
        print("stamped: " + ", ".join(stamped))
        return 0
    payload = verify(Path(args.file).read_text(encoding="utf-8"), key)
    print("verified: " + " ".join(f"{name}={payload[name]}" for name in PAYLOAD_KEYS))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
