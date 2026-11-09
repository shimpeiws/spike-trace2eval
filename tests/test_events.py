import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import make_events, make_job
from t2e.events import analyze_events
from t2e.prompt_v2 import build_prompt_v2


def _write_jsonl(path: Path, events) -> Path:
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return path


class TestEvents(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t2e-events-"))
        self.job = make_job()
        self.events = make_events(self.job, build_prompt_v2(self.job))

    def test_sequence_and_pairing(self):
        rep = analyze_events(_write_jsonl(self.tmp / "e.jsonl", self.events))
        self.assertTrue(rep.sequence_consecutive)
        self.assertEqual(rep.first_seq, 1)
        self.assertEqual(rep.last_seq, len(self.events))
        self.assertEqual(rep.tool_operations, 2)
        self.assertEqual(rep.tool_results, 2)
        self.assertEqual(rep.unpaired_call_ids, [])
        self.assertEqual(rep.orphan_result_ids, [])
        self.assertEqual(rep.result_statuses, {"completed": 2})

    def test_sequence_gap_detected(self):
        evs = [e for e in self.events if e["seq"] != 5]
        rep = analyze_events(_write_jsonl(self.tmp / "g.jsonl", evs))
        self.assertFalse(rep.sequence_consecutive)
        self.assertIn(6, rep.sequence_gaps)

    def test_orphan_result_detected(self):
        evs = self.events + [{"seq": len(self.events) + 1, "ts": "t", "actor": "a",
                              "type": "tool.result", "tool": {"name": "bash", "call_id": "ghost"},
                              "data": {"status": "completed"}}]
        rep = analyze_events(_write_jsonl(self.tmp / "o.jsonl", evs))
        self.assertIn("ghost", rep.orphan_result_ids)

    def test_unpaired_call_detected(self):
        evs = [e for e in self.events if not (e["type"] == "tool.result" and e.get("tool", {}).get("call_id") == "call-2")]
        rep = analyze_events(_write_jsonl(self.tmp / "u.jsonl", evs))
        self.assertIn("call-2", rep.unpaired_call_ids)

    def test_truncation_marker_reported(self):
        evs = self.events[:3] + [{"seq": 4, "ts": "t", "actor": "a", "type": "adapter.providers",
                                "data": {"truncated": True}}] + self.events[3:]
        for i, e in enumerate(evs, 1):
            e["seq"] = i
        rep = analyze_events(_write_jsonl(self.tmp / "t.jsonl", evs))
        self.assertEqual(len(rep.truncation_markers), 1)
        self.assertEqual(rep.truncation_markers[0]["type"], "adapter.providers")

    def test_initial_prompt_extracted(self):
        rep = analyze_events(_write_jsonl(self.tmp / "p.jsonl", self.events))
        self.assertEqual(rep.initial_prompt_seq, 3)
        self.assertEqual(rep.initial_prompt_text, build_prompt_v2(self.job))

    def test_corrupt_line_reported(self):
        path = self.tmp / "bad.jsonl"
        path.write_text(json.dumps(self.events[0]) + "\n{corrupt\n")
        rep = analyze_events(path)
        self.assertTrue(rep.parse_errors)


if __name__ == "__main__":
    unittest.main()
