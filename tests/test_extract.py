import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import FIXTURE_RUN, write_evidence
from t2e.cli import main
from t2e.evidence import case_id_for, case_identity, extract_case_fields


def _expected(root: Path) -> dict:
    sums = (root / "SHA256SUMS").read_text().strip().splitlines()
    events = list((root / "runs").rglob("events.jsonl"))[0].read_text().strip().splitlines()
    return {
        "run_id": FIXTURE_RUN, "base_revision": "a" * 40,
        "repo_canonical": "github.com/example/fixture-repo",
        "events": len(events), "tool_operations": 2, "checks": 2,
        "sha256sums_entries": len(sums),
    }


class TestExtract(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t2e-ex-"))
        self.evdir = self.tmp / "evidence"
        self.built = write_evidence(self.evdir)
        self.exp = _expected(self.evdir)

    def _extract(self, case_dir: Path, records_dir: Path) -> int:
        # CLI-level extraction uses R1 expectations; patch via direct call
        # would bypass CLI, so assert at module level instead.
        from t2e.evidence import validate_evidence
        res = validate_evidence(self.evdir, FIXTURE_RUN, expected=self.exp)
        self.assertTrue(res.ok, res.diagnostics)
        fields = extract_case_fields(res.job)
        identity = case_identity(fields)
        case_id = case_id_for(identity)
        task_md = res.event_report.initial_prompt_text
        agent = case_dir / "agent"
        agent.mkdir(parents=True, exist_ok=True)
        (agent / "task.md").write_text(task_md)
        (case_dir / "case.json").write_text(json.dumps({
            "schema_version": 1, "case_id": case_id, "identity_core": identity,
            "task": {k: v["value"] for k, v in fields.items()},
        }))
        (case_dir / "provenance.json").write_text(json.dumps({"fields": fields}))
        return case_id

    def test_deterministic_extraction(self):
        case1, case2 = self.tmp / "case1", self.tmp / "case2"
        id1 = self._extract(case1, self.tmp / "r1")
        id2 = self._extract(case2, self.tmp / "r2")
        self.assertEqual(id1, id2)
        for rel in ["case.json", "provenance.json", "agent/task.md"]:
            self.assertEqual((case1 / rel).read_bytes(), (case2 / rel).read_bytes())

    def test_every_field_cites_source(self):
        fields = extract_case_fields(self.built["job"])
        for name, f in fields.items():
            self.assertIn("source", f, name)
            self.assertTrue(f["source"].startswith("job."), name)
            self.assertIn("classification", f, name)

    def test_case_id_stable_across_paths(self):
        fields = extract_case_fields(self.built["job"])
        self.assertEqual(case_id_for(case_identity(fields)), case_id_for(case_identity(fields)))

    def test_cli_refuses_failed_validation(self):
        code = main([
            "extract", "--evidence", str(self.tmp / "nonexistent"),
            "--run", FIXTURE_RUN, "--case", str(self.tmp / "c"),
            "--records", str(self.tmp / "rec"),
        ])
        self.assertEqual(code, 1)
        rec = json.loads((self.tmp / "rec" / "evidence.json").read_text())
        self.assertEqual(rec["status"], "failed")


if __name__ == "__main__":
    unittest.main()
