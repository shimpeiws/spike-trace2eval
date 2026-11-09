import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from t2e.evaluate import (
    INLINE_STRATEGIES,
    _make_fixture_project,
    _run_id,
    _snapshot_files,
    classify_control,
)


class TestControlsClassification(unittest.TestCase):
    def test_controls_classification(self):
        self.assertEqual(classify_control("satisfied"), "satisfied")
        self.assertEqual(classify_control("unsatisfied"), "unsatisfied")
        self.assertEqual(classify_control("inconclusive:x"), "inconclusive")

    def test_strategy_order_prefers_stdin(self):
        # The documented primary form (stdin via `--task -`) is tried first.
        self.assertEqual(INLINE_STRATEGIES[0]["name"], "task-dash-stdin")
        self.assertEqual(INLINE_STRATEGIES[0]["via"], "stdin")
        vias = {s["via"] for s in INLINE_STRATEGIES}
        self.assertIn("stdin", vias)


class TestFixtureProject(unittest.TestCase):
    def test_fixture_project_shape(self):
        tmp = Path(tempfile.mkdtemp(prefix="t2e-fix-"))
        project = tmp / "project"
        project.mkdir()
        _make_fixture_project(project)
        self.assertTrue((project / "bin" / "claude").exists())
        self.assertTrue((project / ".yuurei" / "yuurei.yaml").is_file())
        self.assertTrue((project / ".yuurei" / "tasks" / "smoke.md").is_file())
        snap = _snapshot_files(project)
        self.assertIn(".yuurei/tasks/smoke.md", snap)
        self.assertNotIn("bin/claude", snap)  # harness shim isn't project content

    def test_run_id_parse(self):
        self.assertEqual(_run_id("noise\nrun abc-123 finished\n"), "abc-123")
        self.assertIsNone(_run_id("nothing here"))


if __name__ == "__main__":
    unittest.main()
