import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from t2e.records import write_record
from t2e.report import generate_report, mask_paths, sanitize_scan


class TestReport(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t2e-rep-"))

    def test_report_sanitized(self):
        for s in ("evidence", "source", "environment", "leakage"):
            write_record(self.tmp, s, "passed", run_id="r", pinned_sha="abc", file_count=10)
        write_record(self.tmp, "execution", "passed", harness="claude", launch={})
        write_record(self.tmp, "evaluation", "passed", verdict="satisfied")
        text = generate_report(self.tmp)
        self.assertEqual(sanitize_scan(text), [])
        self.assertIn("PoC", text)

    def test_sanitizer_catches_private_path(self):
        problems = sanitize_scan("see /Users/shin/coding-worker/eval for details")
        self.assertTrue(problems)

    def test_sanitizer_catches_prompt_text(self):
        problems = sanitize_scan("You are an autonomous coding worker. Implement...")
        self.assertTrue(problems)

    def test_sanitizer_catches_credential(self):
        problems = sanitize_scan("uses OPENCODE_SERVER_PASSWORD env")
        self.assertTrue(problems)

    def test_report_marks_absent_records(self):
        text = generate_report(self.tmp)
        self.assertIn("record absent", text)

    def test_report_masks_absolute_path_in_diagnostics(self):
        """FIND-006: invalid-record diagnostics never carry host paths."""
        write_record(self.tmp, "evidence", "failed",
                     diagnostics=["evidence-dir: not a directory: /home/op/private/evidence"])
        text = generate_report(self.tmp)
        self.assertNotIn("/home/op/private/evidence", text)
        self.assertEqual(sanitize_scan(text), [])

    def test_sanitizer_flags_any_host_path_shape(self):
        """FIND-006: non-/Users path shapes are also forbidden."""
        for bad in ("/home/op/x/y", "/var/secret/thing", "/private/tmp/ev",
                    "C:\\Users\\op\\secrets"):
            self.assertTrue(sanitize_scan(f"ref {bad} end"), bad)

    def test_extra_forbidden_is_literal(self):
        """FIND-006: caller-supplied forbidden strings are literal, not regex."""
        # Regex metachars (unclosed bracket) — must not raise and must match literally.
        tricky = "a.c["
        self.assertTrue(sanitize_scan(f"xx {tricky} yy", extra_forbidden=[tricky]))
        # Text that an un-escaped pattern would have matched stays clean.
        self.assertEqual(sanitize_scan("xx abc yy", extra_forbidden=[tricky]), [])

    def test_mask_paths(self):
        self.assertEqual(mask_paths("bad dir: /a/b/c done"), "bad dir: <path> done")
        self.assertEqual(mask_paths("rel/path stays"), "rel/path stays")


if __name__ == "__main__":
    unittest.main()
