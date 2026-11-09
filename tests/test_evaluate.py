import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from t2e.evaluate import (
    INLINE_STRATEGIES,
    TASK_CONTENT,
    _make_fixture_project,
    _run_id,
    _snapshot_files,
    classify_control,
    evaluate_behavior,
    expected_task_digest,
    task_source_is_symbolic,
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


class TestSymbolicSource(unittest.TestCase):
    def test_symbolic_sources_accepted(self):
        tmp = Path(tempfile.mkdtemp())
        for s in ("stdin", "-", "inline", "argv", "task:<stdin>"):
            self.assertTrue(task_source_is_symbolic(s, tmp), s)

    def test_filenames_and_paths_rejected(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "task.txt").write_text("x")
        for s in ("task.txt", "./t.md", "/abs/t.md", "tasks/smoke.md",
                  "task.md", "", " "):
            self.assertFalse(task_source_is_symbolic(s, tmp), s)

    def test_existing_file_without_extension_rejected(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "taskfile").write_text("x")
        self.assertFalse(task_source_is_symbolic("taskfile", tmp))

    def test_digest_contract(self):
        self.assertEqual(
            expected_task_digest("abc"),
            "sha256:" + hashlib.sha256(b"abc").hexdigest())


class TestEvaluateBehavior(unittest.TestCase):
    """Behavior checks driven by a fake yuurei CLI — honest and dishonest
    implementations are distinguished."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t2e-eval-"))
        self.target = self.tmp / "target"
        (self.target / "node_modules" / "tsx" / "dist").mkdir(parents=True)
        (self.target / "node_modules" / "tsx" / "dist" / "loader.mjs").write_text("")
        (self.target / "src").mkdir(parents=True)
        (self.target / "src" / "index.ts").write_text("")

    def _evaluate(self, *, inline_source="stdin", digest_mode="content",
                  create_file=None, capture=True):
        """Fake CLI: resolves the task form, writes a trace, honours the
        shim capture contract. `digest_mode='fixed'` simulates a
        hardcoded digest."""
        calls = []

        def fake(target, project, args, stdin_text, timeout,
                 path_prefix=None):
            calls.append(args)
            content = source = None
            if "--task" in args:
                t = args[args.index("--task") + 1]
                if t == "-":
                    content, source = stdin_text, inline_source
                else:
                    content, source = Path(t).read_text(), t
            elif "--task-stdin" in args:
                content, source = stdin_text, inline_source
            elif "--task-text" in args:
                content = args[args.index("--task-text") + 1]
                source = inline_source
            if create_file:
                (project / create_file).write_text("planted\n")
            rid = f"r{len(calls)}"
            digest = (expected_task_digest(TASK_CONTENT) if digest_mode == "fixed"
                      else expected_task_digest(content))
            trace = {"task": {"source": source, "digest": digest}}
            run_dir = project / ".yuurei" / "runs" / rid
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "trace.json").write_text(json.dumps(trace))
            if capture:
                with open(project / "bin" / "shim-capture.jsonl", "a") as f:
                    f.write(json.dumps({"argv": ["x", content]}) + "\n")
            return {"code": 0, "stdout": f"run {rid} finished\n", "stderr": ""}

        with patch("t2e.evaluate._run_cli", side_effect=fake):
            return evaluate_behavior(self.target, timeout=5)

    def _check(self, res, name):
        return next(c for c in res.checks if c["name"] == name)

    def test_honest_impl_satisfied(self):
        res = self._evaluate()
        self.assertEqual(res.verdict, "satisfied",
                         [c for c in res.checks if not c["ok"]])

    def test_fake_filename_source_rejected(self):
        res = self._evaluate(inline_source="task.txt")
        self.assertEqual(res.verdict, "unsatisfied")
        self.assertFalse(self._check(res, "provenance-honest")["ok"])

    def test_fake_path_source_rejected(self):
        res = self._evaluate(inline_source="tasks/smoke.md")
        self.assertEqual(res.verdict, "unsatisfied")

    def test_hardcoded_digest_rejected(self):
        res = self._evaluate(digest_mode="fixed")
        self.assertEqual(res.verdict, "unsatisfied")
        self.assertFalse(self._check(res, "digest-varies-with-input")["ok"])

    def test_task_file_creation_detected(self):
        res = self._evaluate(create_file="task.txt")
        self.assertEqual(res.verdict, "unsatisfied")
        self.assertFalse(self._check(res, "no-task-file-created")["ok"])

    def test_uncaptured_body_rejected(self):
        res = self._evaluate(capture=False)
        self.assertEqual(res.verdict, "unsatisfied")
        self.assertFalse(self._check(res, "file-body-received")["ok"])

    def test_all_checks_named(self):
        res = self._evaluate()
        names = {c["name"] for c in res.checks}
        self.assertEqual(
            names,
            {"baseline-file-run", "file-source-matches", "file-digest-is-content-hash",
             "file-body-received", "inline-task-executes", "inline-body-received",
             "no-task-file-created", "digest-equal", "inline-digest-is-content-hash",
             "digest-varies-with-input", "provenance-honest"})


if __name__ == "__main__":
    unittest.main()
