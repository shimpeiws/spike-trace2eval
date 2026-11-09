import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import EXPECTED, FIXTURE_RUN, write_evidence, write_readiness, write_task_record
from t2e.evidence import validate_evidence


def _expected(root: Path) -> dict:
    sums = (root / "SHA256SUMS").read_text().strip().splitlines()
    events = (root / "runs" / "fixture-batch" / FIXTURE_RUN / "events.jsonl").read_text().strip().splitlines()
    return {**EXPECTED, "events": len(events), "sha256sums_entries": len(sums)}


class TestEvidenceValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t2e-ev-"))
        self.evdir = self.tmp / "evidence"
        self.built = write_evidence(self.evdir)
        self.exp = _expected(self.evdir)

    def test_valid_evidence_passes_all_checks(self):
        res = validate_evidence(self.evdir, FIXTURE_RUN, expected=self.exp)
        self.assertTrue(res.ok, res.diagnostics)
        self.assertTrue(res.prompt_match)
        self.assertTrue(all(c.ok for c in res.checks))

    def test_supporting_records_verified(self):
        task = write_task_record(self.tmp / "jobs" / "task.json", self.built["job"])
        readiness = write_readiness(self.tmp / "jobs" / "readiness.md", task)
        res = validate_evidence(self.evdir, FIXTURE_RUN, task_record=task, readiness_record=readiness, expected=self.exp)
        self.assertTrue(res.ok, res.diagnostics)
        names = {c.name: c.ok for c in res.checks}
        self.assertTrue(names["task-issue-hash"])
        self.assertTrue(names["readiness-task-hash"])

    def test_checksum_mismatch_refuses(self):
        target = self.built["run_dir"] / "result.json"
        target.write_text('{"tampered": true}\n')
        res = validate_evidence(self.evdir, FIXTURE_RUN, expected=self.exp)
        self.assertFalse(res.ok)
        names = {c.name: c.ok for c in res.checks}
        self.assertFalse(names["sha256sums-match"])

    def test_unlisted_file_refuses(self):
        (self.evdir / "stray.txt").write_text("stray")
        res = validate_evidence(self.evdir, FIXTURE_RUN, expected=self.exp)
        self.assertFalse(res.ok)
        names = {c.name: c.ok for c in res.checks}
        self.assertFalse(names["sha256sums-coverage"])

    def test_identity_mismatch_refuses(self):
        run_dir = self.built["run_dir"]
        import json
        man = json.loads((run_dir / "manifest.json").read_text())
        man["job_sha256"] = "0" * 64
        (run_dir / "manifest.json").write_text(json.dumps(man))
        # Fix sums so only the identity check trips.
        entries = []
        for p in sorted(self.evdir.rglob("*")):
            rel = str(p.relative_to(self.evdir))
            if p.is_file() and rel not in ("README.md", "SHA256SUMS"):
                entries.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {rel}")
        (self.evdir / "SHA256SUMS").write_text("\n".join(entries) + "\n")
        res = validate_evidence(self.evdir, FIXTURE_RUN, expected=self.exp)
        self.assertFalse(res.ok)
        names = {c.name: c.ok for c in res.checks}
        self.assertFalse(names["identity-job-hash"])

    def test_prompt_match_and_mismatch(self):
        res = validate_evidence(self.evdir, FIXTURE_RUN, expected=self.exp)
        self.assertTrue(res.prompt_match)
        # Flip one byte of the recorded prompt; regenerate sums.
        run_dir = self.built["run_dir"]
        lines = (run_dir / "events.jsonl").read_text().splitlines()
        import json as _j
        evs = [_j.loads(l) for l in lines]
        evs[2]["data"]["text"] = evs[2]["data"]["text"] + " "
        (run_dir / "events.jsonl").write_text("".join(_j.dumps(e) + "\n" for e in evs))
        entries = []
        for p in sorted(self.evdir.rglob("*")):
            rel = str(p.relative_to(self.evdir))
            if p.is_file() and rel not in ("README.md", "SHA256SUMS"):
                entries.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {rel}")
        (self.evdir / "SHA256SUMS").write_text("\n".join(entries) + "\n")
        res2 = validate_evidence(self.evdir, FIXTURE_RUN, expected=self.exp)
        self.assertFalse(res2.ok)
        self.assertFalse(res2.prompt_match)

    def test_absent_evidence_dir_refuses(self):
        res = validate_evidence(self.tmp / "nonexistent", FIXTURE_RUN, expected=self.exp)
        self.assertFalse(res.ok)

    def test_wrong_run_id_refuses(self):
        res = validate_evidence(self.evdir, "20990101T000000Z-other000", expected=self.exp)
        self.assertFalse(res.ok)
        self.assertFalse(res.checks[0].ok)

    def test_extraction_does_not_modify_evidence(self):
        def tree_hash(root: Path) -> str:
            h = hashlib.sha256()
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    h.update(str(p.relative_to(root)).encode())
                    h.update(p.read_bytes())
            return h.hexdigest()
        before = tree_hash(self.evdir)
        validate_evidence(self.evdir, FIXTURE_RUN, expected=self.exp)
        self.assertEqual(before, tree_hash(self.evdir))

    def test_sequence_gap_refuses(self):
        run_dir = self.built["run_dir"]
        import json as _j
        evs = [_j.loads(l) for l in (run_dir / "events.jsonl").read_text().splitlines()]
        evs = [e for e in evs if e["seq"] != 4]
        (run_dir / "events.jsonl").write_text("".join(_j.dumps(e) + "\n" for e in evs))
        entries = []
        for p in sorted(self.evdir.rglob("*")):
            rel = str(p.relative_to(self.evdir))
            if p.is_file() and rel not in ("README.md", "SHA256SUMS"):
                entries.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {rel}")
        (self.evdir / "SHA256SUMS").write_text("\n".join(entries) + "\n")
        res = validate_evidence(self.evdir, FIXTURE_RUN, expected=self.exp)
        self.assertFalse(res.ok)
        names = {c.name: c.ok for c in res.checks}
        self.assertFalse(names["events-sequence"])


if __name__ == "__main__":
    unittest.main()
