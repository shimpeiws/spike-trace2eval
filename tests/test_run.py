import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from t2e.records import write_record
from t2e.run import (
    STRIPPED_ENV,
    build_execution_record,
    harness_command,
    preflight,
    workspace_boundary_ok,
)


class TestPreflight(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t2e-run-"))

    def test_preflight_refuses_absent(self):
        pf = preflight(self.tmp)
        self.assertFalse(pf.ok)
        self.assertEqual(sorted(pf.missing), ["environment", "evidence", "leakage", "source"])

    def test_preflight_refuses_invalid(self):
        write_record(self.tmp, "evidence", "failed", diagnostics=["bad checksum"])
        for s in ("source", "environment", "leakage"):
            write_record(self.tmp, s, "passed")
        pf = preflight(self.tmp)
        self.assertFalse(pf.ok)
        self.assertEqual(pf.missing, [])
        self.assertEqual(len(pf.invalid), 1)

    def test_preflight_passes_all_valid(self):
        for s in ("evidence", "source", "environment", "leakage"):
            write_record(self.tmp, s, "passed")
        pf = preflight(self.tmp)
        self.assertTrue(pf.ok)


class TestHarness(unittest.TestCase):
    def test_harness_command_claude_disables_web(self):
        argv = harness_command("claude", Path("/tmp/ws"), "task")
        joined = " ".join(argv)
        self.assertIn("--disallowedTools", joined)
        self.assertIn("WebSearch", joined)
        self.assertIn("WebFetch", joined)

    def test_harness_command_opencode_pinned_model(self):
        argv = harness_command("opencode", Path("/tmp/ws"), "task")
        joined = " ".join(argv)
        self.assertIn("opencode-go/deepseek-v4.1-flash", joined)
        self.assertIn("--auto", argv)
        self.assertIn("--pure", argv)
        self.assertEqual(argv[-1], "task")

    def test_harness_command_rejects_unknown(self):
        with self.assertRaises(ValueError):
            harness_command("mystery", Path("/tmp/ws"), "task")


class TestExecutionClassification(unittest.TestCase):
    def _rec(self, termination: str) -> dict:
        return build_execution_record(
            harness="claude", model="m", task_sha256="t", pinned_sha="p",
            setup={}, boundary_problems=[], launch={"termination_reason": termination},
            patch=None, timeout_s=60, stripped_env=STRIPPED_ENV, env_keys=[],
        )

    def test_execution_classification(self):
        self.assertEqual(self._rec("completed")["status"], "passed")
        for t in ("timeout", "exit-1", "launch_failure", "setup_failure", "preflight_refused"):
            self.assertEqual(self._rec(t)["status"], "failed", t)

    def test_stripped_env_recorded(self):
        rec = self._rec("completed")
        self.assertIn("OPENCODE_SERVER_PASSWORD", rec["stripped_env"])


class TestBoundary(unittest.TestCase):
    def test_workspace_boundary_ok_clean(self):
        import subprocess
        tmp = Path(tempfile.mkdtemp(prefix="t2e-bnd-"))
        src = tmp / "workspace" / "source"
        src.mkdir(parents=True)
        subprocess.run(["git", "-C", str(src), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(src), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(src), "config", "user.name", "t"], check=True)
        (src / "a").write_text("a")
        subprocess.run(["git", "-C", str(src), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(src), "commit", "-qm", "x"], check=True)
        sha = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(workspace_boundary_ok(tmp / "workspace", sha), [])

    def test_workspace_boundary_flags_remote(self):
        import subprocess
        tmp = Path(tempfile.mkdtemp(prefix="t2e-bnd2-"))
        src = tmp / "workspace" / "source"
        src.mkdir(parents=True)
        subprocess.run(["git", "-C", str(src), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(src), "remote", "add", "origin", "https://x.invalid/y.git"], check=True)
        problems = workspace_boundary_ok(tmp / "workspace", "a" * 40)
        self.assertTrue(problems)


if __name__ == "__main__":
    unittest.main()
