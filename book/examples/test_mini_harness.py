"""Isolated regression tests; no remote services or real repository changes."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mini_harness import Invalid, parse_tasks

CLI = str(Path(__file__).with_name("mini_harness.py"))


class HarnessTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.pkg = self.root / ".mini-harness" / "photo-migration"
        self.assertEqual(self.run_cli("init").returncode, 0)

    def run_cli(self, action, *args):
        return subprocess.run(
            [sys.executable, CLI, action, "photo-migration", *args],
            cwd=self.root,
            text=True,
            capture_output=True,
            timeout=15,
        )

    def activate(self):
        p = self.pkg / "spec.md"
        p.write_text(p.read_text().replace("Status: draft", "Status: active"))

    def finish(self):
        self.activate()
        p = self.pkg / "tasks.md"
        p.write_text(p.read_text().replace("- [ ]", "- [x]") + "  - evidence: isolated fixture result\n")
        p = self.pkg / "checklist.md"
        p.write_text(
            p.read_text().replace("- [ ]", "- [x]").replace("pending", "passed")
            + "Evidence: isolated fixture reviewed\n"
        )

    def test_lifecycle(self):
        self.assertEqual(self.run_cli("route").stdout.strip(), "plan")
        self.assertEqual(self.run_cli("check").returncode, 1)
        self.activate()
        r = self.run_cli("route")
        self.assertIn("execute", r.stdout)
        self.assertIn("verify (not executed)", r.stdout)
        self.finish()
        self.assertEqual(self.run_cli("check").returncode, 0)
        self.assertEqual(self.run_cli("route").stdout.strip(), "ready-to-archive")

    def test_init_does_not_overwrite(self):
        original = (self.pkg / "spec.md").read_bytes()
        self.assertEqual(self.run_cli("init").returncode, 2)
        self.assertEqual(original, (self.pkg / "spec.md").read_bytes())

    def test_false_acceptance(self):
        p = self.pkg / "checklist.md"
        p.write_text(p.read_text().replace("pending", "passed"))
        self.assertEqual(self.run_cli("check").returncode, 2)

    def test_verify_success_and_failure(self):
        self.finish()
        self.assertEqual(self.run_cli("verify", "--", sys.executable, "-c", "print('ok')").returncode, 0)
        report = json.loads((self.pkg / "evidence.json").read_text())
        self.assertEqual(report["returncode"], 0)
        self.assertEqual(report["stdout"]["text"], "ok\n")
        self.assertTrue(report["documentsUnchanged"])
        self.assertEqual(self.run_cli("verify", "--", sys.executable, "-c", "raise SystemExit(7)").returncode, 1)
        self.assertEqual(json.loads((self.pkg / "evidence.json").read_text())["returncode"], 7)

    def test_verify_timeout(self):
        self.finish()
        r = self.run_cli("verify", "--timeout", "1", "--", sys.executable, "-c", "import time; time.sleep(10)")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(json.loads((self.pkg / "evidence.json").read_text())["timeout"])

    def test_argv_is_not_shell(self):
        self.finish()
        payload = "; touch should-not-exist"
        r = self.run_cli("verify", "--", sys.executable, "-c", "import sys; print(sys.argv[1])", payload)
        self.assertEqual(r.returncode, 0)
        self.assertFalse((self.root / "should-not-exist").exists())

    def test_missing_executable(self):
        self.finish()
        self.assertEqual(self.run_cli("verify", "--", "nonexistent-ebook-test-executable").returncode, 2)

    def test_documents_changed_during_verify(self):
        self.finish()
        code = (
            "from pathlib import Path; p=Path('.mini-harness/photo-migration/spec.md'); "
            "p.write_text(p.read_text()+'\\nchanged\\n')"
        )
        self.assertEqual(self.run_cli("verify", "--", sys.executable, "-c", code).returncode, 1)
        self.assertFalse(json.loads((self.pkg / "evidence.json").read_text())["documentsUnchanged"])

    def test_output_summary_bounded(self):
        self.finish()
        r = self.run_cli("verify", "--", sys.executable, "-c", "print('a'*100000)")
        self.assertEqual(r.returncode, 0)
        summary = json.loads((self.pkg / "evidence.json").read_text())["stdout"]
        self.assertTrue(summary["truncated"])
        self.assertEqual(len(summary["text"]), 65536)

    @unittest.skipUnless(os.name == "posix", "symlink test")
    def test_symlink_rejected(self):
        p = self.pkg / "tasks.md"
        real = self.root / "outside.md"
        real.write_bytes(p.read_bytes())
        p.unlink()
        p.symlink_to(real)
        self.assertEqual(self.run_cli("route").returncode, 2)

    def test_empty_or_missing_file(self):
        (self.pkg / "tasks.md").write_text("")
        self.assertEqual(self.run_cli("route").returncode, 2)
        (self.pkg / "spec.md").unlink()
        self.assertEqual(self.run_cli("route").returncode, 2)

    def test_path_traversal(self):
        r = subprocess.run([sys.executable, CLI, "init", "../escape"], cwd=self.root, capture_output=True)
        self.assertEqual(r.returncode, 2)
        self.assertFalse((self.root / "escape").exists())


class TaskParserTest(unittest.TestCase):
    def record(self, ident, dependencies="", checked=False):
        dependency = f"  - depends-on: {dependencies}\n" if dependencies else ""
        return (
            f"- [{'x' if checked else ' '}] Task\n"
            f"  - id: {ident}\n"
            f"{dependency}"
            "  - boundary: src/a.py\n"
            "  - verify: approved command\n"
        )

    def test_missing_dependency(self):
        with self.assertRaisesRegex(Invalid, "missing dependency"):
            parse_tasks(self.record("a", "b"))

    def test_cycle(self):
        with self.assertRaisesRegex(Invalid, "cycle"):
            parse_tasks(self.record("a", "b") + self.record("b", "a"))

    def test_duplicate_ids(self):
        with self.assertRaisesRegex(Invalid, "unique"):
            parse_tasks(self.record("a") * 2)

    def test_done_needs_evidence(self):
        with self.assertRaisesRegex(Invalid, "without evidence"):
            parse_tasks(self.record("a", checked=True))

    def test_unknown_field(self):
        with self.assertRaisesRegex(Invalid, "malformed"):
            parse_tasks(self.record("a") + "  - bogus: ignored?\n")

    def test_dependency_completion_order(self):
        with self.assertRaisesRegex(Invalid, "before its dependency"):
            parse_tasks(self.record("a", "b", True) + self.record("b"))


if __name__ == "__main__":
    unittest.main()
