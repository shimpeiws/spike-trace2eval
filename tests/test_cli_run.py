import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from t2e import cli
from t2e.records import RecordState, load_record, write_record
from t2e.run import tree_sha256


def _ns(**kw):
    base = dict(case=None, records=None, out=None, harness="opencode",
                model=None, timeout=60, force=False, node_bin=None,
                evidence=None, patch=None, target=None, stage=None,
                expect=None, run_declared_checks=False, log_dir=None,
                workspace=None, repo=None, sha=None, run=None,
                task_record=None, readiness=None, workdir=None,
                skip_checks=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _make_case(tmp: Path) -> Path:
    case_dir = tmp / "case"
    agent = case_dir / "agent"
    agent.mkdir(parents=True)
    (agent / "task.md").write_text("do the task\n")
    case_dir.joinpath("case.json").write_text(json.dumps({
        "identity_core": {"base_revision": "a" * 40},
        "task": {"checks": []},
    }))
    return case_dir


def _pass_prereqs(records: Path):
    for s in ("evidence", "source", "environment", "leakage"):
        write_record(records, s, "passed")


class TestPreflightRefusalDoesNotConsume(unittest.TestCase):
    """FIND-003: a refused launch must not consume the single experiment run."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t2e-cli-"))
        self.case = _make_case(self.tmp)
        self.records = self.tmp / "records"
        self.records.mkdir()
        self.out = self.tmp / "run"

    def test_refusal_leaves_run_unconsumed(self):
        args = _ns(case=str(self.case), records=str(self.records), out=str(self.out))
        rc1 = cli.cmd_run(args)
        self.assertNotEqual(rc1, 0)
        self.assertFalse((self.records / "execution.json").exists())
        self.assertTrue((self.records / "execution_refused.json").exists())
        # Second refusal still works — the run is still available.
        rc2 = cli.cmd_run(args)
        self.assertNotEqual(rc2, 0)
        self.assertFalse((self.records / "execution.json").exists())

    def test_refusal_record_is_not_a_consumption(self):
        rec = json.loads((self.records / "execution_refused.json").read_text()) \
            if (self.records / "execution_refused.json").exists() else None
        cli.cmd_run(_ns(case=str(self.case), records=str(self.records), out=str(self.out)))
        rec = json.loads((self.records / "execution_refused.json").read_text())
        self.assertFalse(rec["launch_attempted"])
        self.assertEqual(rec["launch"]["termination_reason"], "preflight_refused")

    def test_consumed_marker_blocks_relaunch(self):
        _pass_prereqs(self.records)
        # Simulate a crashed run: the consume marker exists.
        (self.records / "execution.json").write_text(json.dumps({
            "schema_version": 1, "stage": "execution", "status": "failed",
            "launch_attempted": True, "termination_reason": "crashed-or-incomplete"}))
        rc = cli.cmd_run(_ns(case=str(self.case), records=str(self.records), out=str(self.out)))
        self.assertNotEqual(rc, 0)

    def test_integrity_violation_marks_record_failed(self):
        """FIND-005: a workspace-external write during the run fails the record."""
        _pass_prereqs(self.records)
        ws_source = self.out / "workspace" / "source"

        def fake_prepare(agent_dir, dest, log_dir, path_prefix=None):
            ws_source.mkdir(parents=True)
            (ws_source / "x").write_text("x")
            subprocess.run(["git", "-C", str(ws_source), "init", "-q"], check=True)
            return {"exit_code": 0, "stdout_ref": {"file": "s"}, "stderr_ref": {"file": "e"}}

        def tamper(*a, **kw):
            # Simulated agent writing outside its workspace.
            (self.case / "agent" / "task.md").write_text("tampered\n")
            return {"termination_reason": "completed", "exit_code": 0,
                    "duration_s": 1.0, "stdout_ref": {"file": "o"},
                    "stderr_ref": {"file": "e"}, "final_answer_tail": ""}

        with patch.object(cli, "prepare_workspace", side_effect=fake_prepare), \
             patch.object(cli, "workspace_boundary_ok", return_value=[]), \
             patch.object(cli, "launch_agent", side_effect=tamper), \
             patch.object(cli, "collect_patch", return_value={"changed_files": [], "patch_ref": {}}):
            rc = cli.cmd_run(_ns(case=str(self.case), records=str(self.records),
                                 out=str(self.out), harness="claude"))
        self.assertNotEqual(rc, 0)
        rec = json.loads((self.records / "execution.json").read_text())
        self.assertFalse(rec["post_run_integrity"]["unchanged"])
        self.assertEqual(rec["status"], "failed")
        self.assertTrue(rec["launch_attempted"])


class TestControlsStrictness(unittest.TestCase):
    """FIND-004: an inconclusive negative control is a failure, not a pass."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t2e-ctl-"))
        self.case = _make_case(self.tmp)
        (self.case / "agent" / "source").mkdir(parents=True)
        self.records = self.tmp / "records"
        self.records.mkdir()
        self.patch = self.tmp / "p.diff"
        self.patch.write_text("diff --git a/x b/x\n")

    def _run_controls(self, neg_verdict, pos_verdict):
        class V:
            def __init__(self, v):
                self.verdict = v
                self.strategy_used = None
                self.checks = []
                self.diagnostics = []

        results = iter([V(neg_verdict), V(pos_verdict)])
        proc = unittest.mock.Mock(returncode=0)
        with patch.object(cli.shutil, "copytree"), \
             patch.object(cli.shutil, "rmtree"), \
             patch("t2e.cli.sp.run", return_value=proc), \
             patch.object(cli, "evaluate_behavior", side_effect=lambda *a, **k: next(results)), \
             patch.object(cli, "apply_patch_to_copy", return_value=None):
            return cli.cmd_controls(_ns(case=str(self.case), records=str(self.records),
                                        patch=str(self.patch), timeout=10))

    def test_negative_inconclusive_fails(self):
        rc = self._run_controls("inconclusive:boom", "satisfied")
        self.assertEqual(rc, 1)
        rec = json.loads((self.records / "controls.json").read_text())
        self.assertEqual(rec["status"], "failed")

    def test_normal_controls_pass(self):
        rc = self._run_controls("unsatisfied", "satisfied")
        self.assertEqual(rc, 0)


class TestScoreStatus(unittest.TestCase):
    """FIND-008: inconclusive evaluation writes a failed record."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t2e-score-"))
        self.case = _make_case(self.tmp)
        self.records = self.tmp / "records"
        self.records.mkdir()
        self.ws = self.tmp / "ws"
        (self.ws / "source").mkdir(parents=True)

    def _exec_record(self):
        self.records.joinpath("execution.json").write_text(json.dumps({
            "schema_version": 1, "stage": "execution", "status": "passed",
            "launch_attempted": True, "launch": {"termination_reason": "completed"},
            "pinned_source": "a" * 40, "task_sha256": "t", "budgets": {}}))

    def _score(self, verdict):
        class V:
            def __init__(self, v):
                self.verdict = v
                self.strategy_used = None
                self.checks = []
                self.diagnostics = []
        with patch.object(cli, "evaluate_behavior", return_value=V(verdict)):
            return cli.cmd_score(_ns(case=str(self.case), records=str(self.records),
                                     workspace=str(self.ws), timeout=10))

    def test_inconclusive_writes_failed_record(self):
        self._exec_record()
        rc = self._score("inconclusive:harness exploded")
        self.assertEqual(rc, 1)
        rec = load_record(self.records / "evaluation.json", "evaluation")
        self.assertIs(rec.state, RecordState.INVALID)
        body = json.loads((self.records / "evaluation.json").read_text())
        self.assertEqual(body["classification"], "operational_failure")

    def test_unsatisfied_is_a_verdict(self):
        self._exec_record()
        rc = self._score("unsatisfied")
        self.assertEqual(rc, 0)

    def test_absent_execution_record_fails(self):
        rc = self._score("satisfied")
        self.assertNotEqual(rc, 0)


class TestExecutionRecordSchema(unittest.TestCase):
    """要文脈: a passing execution record must carry its required fields."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t2e-sch-"))

    def test_incomplete_passing_record_is_invalid(self):
        p = self.tmp / "execution.json"
        p.write_text(json.dumps({"schema_version": 1, "stage": "execution",
                                 "status": "passed"}))
        rec = load_record(p, "execution")
        self.assertIs(rec.state, RecordState.INVALID)
        self.assertIn("missing required fields", rec.diagnostics[0])

    def test_complete_passing_record_is_valid(self):
        p = self.tmp / "execution.json"
        p.write_text(json.dumps({
            "schema_version": 1, "stage": "execution", "status": "passed",
            "launch": {}, "launch_attempted": True, "pinned_source": "p",
            "task_sha256": "t", "budgets": {}}))
        self.assertIs(load_record(p, "execution").state, RecordState.VALID)


if __name__ == "__main__":
    unittest.main()
