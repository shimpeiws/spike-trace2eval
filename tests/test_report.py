import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from t2e.records import write_record
from t2e.report import generate_report, sanitize_scan


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


if __name__ == "__main__":
    unittest.main()
