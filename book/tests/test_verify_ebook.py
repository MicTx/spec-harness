"""Tests of ebook gates, including negative cases and the actual EPUB artifact."""

import contextlib
import importlib.util
import io
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BOOK = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ebook_gate", BOOK / "verify_ebook.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class EbookGateTest(unittest.TestCase):
    def test_headings_in_code_do_not_satisfy_contract(self):
        text = "# Title\n```markdown\n## 场景\n```\n## 原则\n"
        self.assertEqual(gate.heading_names(text), ["Title", "原则"])

    def test_fence_kind_and_length(self):
        text = "# Title\n````\n```\n## Hidden\n````\n## Shown\n"
        self.assertEqual(gate.heading_names(text), ["Title", "Shown"])

    def test_bad_selection(self):
        for text in ("", ",", "00,00", "unknown"):
            with self.subTest(text=text), self.assertRaises(gate.CheckError):
                gate.parse_chapter_keys(text)

    def test_valid_selection(self):
        self.assertEqual(gate.parse_chapter_keys("00, 01"), ["00", "01"])

    def test_cjk_count(self):
        self.assertEqual(gate.cjk_count("中文abc 123!"), 2)

    def test_real_chapters(self):
        for key in gate.CHAPTERS:
            with self.subTest(key=key):
                self.assertEqual(gate.check_chapter(key), [])

    def test_embedded_source_matches_executable(self):
        appendix = (BOOK / "src/b-artifact-map.md").read_text()
        source = re.search(r"```python\n(.*?)```", appendix, re.S)
        self.assertIsNotNone(source)
        self.assertEqual(source.group(1), (BOOK / "examples/mini_harness.py").read_text())

    def test_real_epub(self):
        self.assertTrue(gate.EPUB.is_file(), "build the book before integration tests")
        self.assertEqual(gate.validate_epub(gate.EPUB), [])

    def test_corrupt_epub(self):
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary) / "bad.epub"
            p.write_bytes(b"not a zip")
            self.assertTrue(gate.validate_epub(p))

    def test_pandoc_missing_preserves_old_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            dist = Path(temporary)
            epub = dist / "book.epub"
            epub.write_bytes(b"existing artifact")
            with patch.object(gate, "DIST", dist), patch.object(gate, "EPUB", epub):
                with patch.object(gate.subprocess, "run", side_effect=FileNotFoundError):
                    self.assertTrue(gate.build_epub(BOOK / "src/book.md"))
            self.assertEqual(epub.read_bytes(), b"existing artifact")
            self.assertEqual(list(dist.iterdir()), [epub])

    def test_invalid_chapter_not_silently_skipped(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gate.main(["--chapters", " "]), 1)


if __name__ == "__main__":
    unittest.main()
