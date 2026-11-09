import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from t2e.records import write_record
from t2e.run import (
    ENV_SECRET_RE,
    build_execution_record,
    collect_patch,
    harness_command,
    preflight,
    sanitized_env,
    tree_sha256,
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
            patch=None, timeout_s=60, stripped_env=["OPENCODE_SERVER_PASSWORD"], env_keys=[],
        )

    def test_execution_classification(self):
        self.assertEqual(self._rec("completed")["status"], "passed")
        for t in ("timeout", "exit-1", "launch_failure", "setup_failure", "preflight_refused"):
            self.assertEqual(self._rec(t)["status"], "failed", t)


class TestSanitizedEnv(unittest.TestCase):
    """FIND-002: agent subprocess env is default-deny; secrets never pass."""

    def _env(self, fake: dict):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, fake, clear=True):
            return sanitized_env()

    def test_common_secret_vars_stripped(self):
        fake = {
            "PATH": "/bin", "HOME": "/h", "LANG": "en_US.UTF-8",
            "GITHUB_TOKEN": "ghp_x", "GH_TOKEN": "y", "OPENAI_API_KEY": "sk-z",
            "ANTHROPIC_API_KEY": "k", "AWS_SECRET_ACCESS_KEY": "s",
            "OPENCODE_SERVER_PASSWORD": "p", "MY_CUSTOM_SECRET": "s",
            "ARBITRARY_OPERATOR_VAR": "v", "DB_PASSWORD": "w",
        }
        audit = self._env(fake)
        self.assertEqual(set(audit["env"]), {"PATH", "HOME", "LANG", "COREPACK_ENABLE_DOWNLOAD_PROMPT"})
        for name in fake:
            if name in ("PATH", "HOME", "LANG"):
                self.assertNotIn(name, audit["stripped_env"])
            else:
                self.assertIn(name, audit["stripped_env"])

    def test_allowlist_names_not_matching_secret_regex_pass(self):
        audit = self._env({"PATH": "/bin", "XDG_DATA_HOME": "/x", "MISE_DATA_DIR": "/m",
                           "npm_config_cache": "/n", "LC_ALL": "C", "HTTPS_PROXY": "http://p"})
        for k in ("XDG_DATA_HOME", "MISE_DATA_DIR", "npm_config_cache", "LC_ALL", "HTTPS_PROXY"):
            self.assertIn(k, audit["env"])

    def test_secret_name_wins_over_allowlist(self):
        # Even a allowlist-prefix name is stripped if it looks credential-bearing.
        self.assertTrue(ENV_SECRET_RE.search("npm_config__authToken"))
        audit = self._env({"npm_config__authToken": "t", "XDG_SECRET_THING": "s"})
        self.assertNotIn("npm_config__authToken", audit["env"])
        self.assertNotIn("XDG_SECRET_THING", audit["env"])

    def test_path_prefix_added(self):
        audit = self._env({"PATH": "/bin"})
        audit = sanitized_env(extra_path_prefix="/node26/bin")
        self.assertTrue(audit["env"]["PATH"].startswith("/node26/bin"))


class TestPostRunIntegrityHelpers(unittest.TestCase):
    """FIND-005: tree hashing detects workspace-external writes."""

    def test_tree_sha256_detects_external_write(self):
        tmp = Path(tempfile.mkdtemp(prefix="t2e-int-"))
        (tmp / "f.txt").write_text("one")
        before = tree_sha256(tmp)
        (tmp / "f.txt").write_text("tampered")
        self.assertNotEqual(before, tree_sha256(tmp))

    def test_tree_sha256_detects_new_file(self):
        tmp = Path(tempfile.mkdtemp(prefix="t2e-int2-"))
        before = tree_sha256(tmp)
        (tmp / "sneaky").write_text("x")
        self.assertNotEqual(before, tree_sha256(tmp))


class TestCollectPatchCommittedWork(unittest.TestCase):
    """FIND-007: committed agent work must still appear in the patch record."""

    def test_collect_patch_detects_committed_changes(self):
        import subprocess as sp
        tmp = Path(tempfile.mkdtemp(prefix="t2e-patch-"))
        src = tmp / "source"
        src.mkdir()
        sp.run(["git", "-C", str(src), "init", "-q"], check=True)
        sp.run(["git", "-C", str(src), "config", "user.email", "t@t"], check=True)
        sp.run(["git", "-C", str(src), "config", "user.name", "t"], check=True)
        (src / "a.txt").write_text("base\n")
        sp.run(["git", "-C", str(src), "add", "-A"], check=True)
        sp.run(["git", "-C", str(src), "commit", "-qm", "base"], check=True)
        pinned = sp.run(["git", "-C", str(src), "rev-parse", "HEAD"],
                        capture_output=True, text=True, check=True).stdout.strip()
        # Agent edits AND commits (against instructions) — must not vanish.
        (src / "a.txt").write_text("agent change\n")
        (src / "new.ts").write_text("export {};\n")
        sp.run(["git", "-C", str(src), "add", "-A"], check=True)
        sp.run(["git", "-C", str(src), "commit", "-qm", "agent work"], check=True)
        info = collect_patch(src, tmp / "artifacts", pinned)
        self.assertGreater(info["patch_ref"]["bytes"], 0)
        self.assertIn("a.txt", info["changed_files"])
        self.assertIn("new.ts", info["changed_files"])
        self.assertTrue(info["agent_committed"])
        self.assertEqual(info["agent_commit_count"], 1)


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
