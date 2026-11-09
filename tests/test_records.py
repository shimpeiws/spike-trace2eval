import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from t2e.records import RecordState, load_record, record_path, require_record, write_record


class TestRecordStates(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t2e-records-"))

    def test_absent_is_missing_not_ready(self):
        rec = require_record(self.tmp, "evidence")
        self.assertIs(rec.state, RecordState.ABSENT)
        self.assertFalse(rec.ready)

    def test_present_and_valid_is_ready(self):
        write_record(self.tmp, "evidence", "passed", run_id="r1")
        rec = require_record(self.tmp, "evidence")
        self.assertIs(rec.state, RecordState.VALID)
        self.assertTrue(rec.ready)
        self.assertEqual(rec.record["run_id"], "r1")

    def test_failed_record_is_invalid(self):
        write_record(self.tmp, "evidence", "failed", diagnostics=["boom"])
        rec = require_record(self.tmp, "evidence")
        self.assertIs(rec.state, RecordState.INVALID)
        self.assertFalse(rec.ready)

    def test_unparsable_record_is_invalid(self):
        p = record_path(self.tmp, "evidence")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json")
        rec = require_record(self.tmp, "evidence")
        self.assertIs(rec.state, RecordState.INVALID)

    def test_wrong_stage_is_invalid(self):
        write_record(self.tmp, "evidence", "passed")
        rec = load_record(record_path(self.tmp, "evidence"), "environment")
        self.assertIs(rec.state, RecordState.INVALID)

    def test_write_is_atomic_no_tmp_left(self):
        write_record(self.tmp, "environment", "passed")
        leftovers = [p.name for p in self.tmp.iterdir() if ".tmp-" in p.name]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
