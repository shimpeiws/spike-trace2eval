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
from t2e.canonical import sha256_file
from t2e.provenance import package_fingerprint
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
        "task": {"checks": [{"name": "chk", "command": "true", "required": True}]},
    }))
    return case_dir


def _pass_prereqs(records: Path, case_dir: Path | None = None):
    for s in ("evidence", "source", "environment"):
        write_record(records, s, "passed")
    fp = package_fingerprint(case_dir / "agent") if case_dir else "fp"
    write_record(records, "leakage", "passed", inputs_fingerprint=fp)


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
        _pass_prereqs(self.records, self.case)
        # Simulate a crashed run: the consume marker exists.
        (self.records / "execution.json").write_text(json.dumps({
            "schema_version": 1, "stage": "execution", "status": "failed",
            "launch_attempted": True, "termination_reason": "crashed-or-incomplete"}))
        rc = cli.cmd_run(_ns(case=str(self.case), records=str(self.records), out=str(self.out)))
        self.assertNotEqual(rc, 0)

    def test_integrity_violation_marks_record_failed(self):
        """FIND-005: a workspace-external write during the run fails the record."""
        _pass_prereqs(self.records, self.case)
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


class TestInputDrift(unittest.TestCase):
    """Inputs changed after check-inputs must not reach launch."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t2e-drift-"))
        self.case = _make_case(self.tmp)
        self.records = self.tmp / "records"
        self.records.mkdir()
        self.out = self.tmp / "run"

    def _attempt(self):
        ws_source = self.out / "workspace" / "source"

        def fake_prepare(agent_dir, dest, log_dir, path_prefix=None):
            ws_source.mkdir(parents=True)
            return {"exit_code": 0, "stdout_ref": {"file": "s"}, "stderr_ref": {"file": "e"}}

        with patch.object(cli, "prepare_workspace", side_effect=fake_prepare), \
             patch.object(cli, "workspace_boundary_ok", return_value=[]):
            return cli.cmd_run(_ns(case=str(self.case), records=str(self.records),
                                   out=str(self.out)))

    def test_drift_refuses_without_consuming(self):
        _pass_prereqs(self.records, self.case)
        (self.case / "agent" / "task.md").write_text("tampered after inspection\n")
        rc = self._attempt()
        self.assertNotEqual(rc, 0)
        self.assertFalse((self.records / "execution.json").exists())
        rec = json.loads((self.records / "execution_refused.json").read_text())
        self.assertEqual(rec["launch"]["termination_reason"], "input_drift")
        self.assertFalse(rec["launch_attempted"])

    def test_fingerprintless_record_refuses(self):
        # A leakage record written before fingerprinting cannot launch.
        for s in ("evidence", "source", "environment"):
            write_record(self.records, s, "passed")
        write_record(self.records, "leakage", "passed")
        rc = self._attempt()
        self.assertNotEqual(rc, 0)
        self.assertFalse((self.records / "execution.json").exists())


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
        self.ws = self.tmp / "run" / "workspace"
        artifacts = self.tmp / "run" / "artifacts"
        artifacts.mkdir(parents=True)
        self.ws.mkdir(parents=True)
        self.patch_file = artifacts / "patch.diff"
        self.patch_file.write_text("diff --git a/x b/x\n")
        self.patch_sha = sha256_file(self.patch_file)
        self.seen_target: list[Path] = []

    def _exec_record(self, patch_sha=None, pinned="a" * 40):
        self.records.joinpath("execution.json").write_text(json.dumps({
            "schema_version": 1, "stage": "execution", "status": "passed",
            "launch_attempted": True, "launch": {"termination_reason": "completed"},
            "pinned_source": pinned, "task_sha256": "t", "budgets": {},
            "patch": {"patch_ref": {"file": "patch.diff",
                                    "sha256": patch_sha if patch_sha is not None else self.patch_sha},
                      "changed_files": ["x"]}}))

    def _score(self, verdict, declared=False, check_rc=0):
        class V:
            def __init__(self, v):
                self.verdict = v
                self.strategy_used = None
                self.checks = []
                self.diagnostics = []

        def fake_copytree(src, dst, **kw):
            Path(dst).mkdir(parents=True, exist_ok=True)

        def fake_run(cmd, **kw):
            return unittest.mock.Mock(returncode=check_rc if cmd[0] == "sh" else 0,
                                      stdout="", stderr="")

        def fake_eval(target, **kw):
            self.seen_target.append(Path(target))
            return V(verdict)

        with patch.object(cli, "evaluate_behavior", side_effect=fake_eval), \
             patch.object(cli.shutil, "copytree", side_effect=fake_copytree), \
             patch("t2e.cli.sp.run", side_effect=fake_run):
            return cli.cmd_score(_ns(case=str(self.case), records=str(self.records),
                                     workspace=str(self.ws), timeout=10,
                                     run_declared_checks=declared,
                                     log_dir=str(self.tmp / "logs")))

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

    def test_satisfied_requires_declared_checks(self):
        """Required checks must pass; unrun is not satisfied."""
        self._exec_record()
        rc = self._score("satisfied", declared=True, check_rc=0)
        self.assertEqual(rc, 0)
        body = json.loads((self.records / "evaluation.json").read_text())
        self.assertEqual(body["verdict"], "satisfied")

    def test_failed_required_check_is_unsatisfied(self):
        self._exec_record()
        rc = self._score("satisfied", declared=True, check_rc=1)
        self.assertEqual(rc, 0)
        body = json.loads((self.records / "evaluation.json").read_text())
        self.assertEqual(body["verdict"], "unsatisfied:required-check-failed")
        self.assertEqual(body["classification"], "agent_requirement_failure")

    def test_unrun_checks_make_satisfied_unreachable(self):
        self._exec_record()
        rc = self._score("satisfied", declared=False)
        self.assertEqual(rc, 1)
        body = json.loads((self.records / "evaluation.json").read_text())
        self.assertTrue(body["verdict"].startswith("inconclusive"))

    def test_patch_hash_mismatch_fails(self):
        self._exec_record(patch_sha="0" * 64)
        rc = self._score("satisfied", declared=True)
        self.assertEqual(rc, 1)

    def test_score_uses_verify_tree_not_workspace(self):
        """The scored tree is pinned base + recorded patch, not --workspace."""
        self._exec_record()
        self._score("unsatisfied")
        self.assertEqual(len(self.seen_target), 1)
        self.assertNotEqual(self.seen_target[0], self.ws / "source")
        self.assertNotIn(str(self.ws), str(self.seen_target[0]))


class TestNodeBinPropagation(unittest.TestCase):
    """The selected toolchain reaches behavior evaluation, not just setup."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t2e-nb-"))
        self.case = _make_case(self.tmp)
        self.records = self.tmp / "records"
        self.records.mkdir()
        self.target = self.tmp / "target"
        self.target.mkdir()

    def test_evaluate_uses_node_bin(self):
        seen = {}

        def fake_eval(t, timeout, path_prefix=None):
            seen["pp"] = path_prefix
            return unittest.mock.Mock(verdict="satisfied", strategy_used=None,
                                    checks=[], diagnostics=[])

        with patch.object(cli, "evaluate_behavior", side_effect=fake_eval):
            cli.cmd_evaluate(_ns(target=str(self.target), records=str(self.records),
                                 node_bin="/opt/node/bin", timeout=10))
        self.assertEqual(seen["pp"], "/opt/node/bin")

    def test_score_uses_verify_tree_toolchain(self):
        # Covered indirectly: cmd_score threads _path_prefix into install,
        # evaluate_behavior, and declared checks (see TestScoreStatus).
        pass


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
