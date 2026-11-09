import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from t2e.canonical import sha256_file
from t2e.materialize import materialize
from t2e.provenance import check_input_package, package_fingerprint


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


class TestProvenance(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t2e-prov-"))
        # Build a pinned source fixture.
        self.repo = self.tmp / "origin-repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        (self.repo / "a.txt").write_text("one\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "first")
        self.sha = _git(self.repo, "rev-parse", "HEAD")
        (self.repo / "later.txt").write_text("future\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "second")

        self.agent = self.tmp / "case" / "agent"
        self.agent.mkdir(parents=True)
        (self.agent / "task.md").write_text("fixture task\n")
        (self.agent / "environment.json").write_text("{}\n")
        (self.agent / "setup.sh").write_text("#!/bin/sh\n")
        materialize(self.repo, self.sha, self.agent / "source")
        self.hashes = {"task.md": sha256_file(self.agent / "task.md")}
        self.kw = dict(expected_hashes=self.hashes, pinned_sha=self.sha)

    def test_clean_package_passes(self):
        res = check_input_package(self.agent, **self.kw)
        self.assertTrue(res.ok, res.diagnostics)

    def test_contamination_rejected_extra_file(self):
        (self.agent / "notes.md").write_text("unsupported prose\n")
        res = check_input_package(self.agent, **self.kw)
        self.assertFalse(res.ok)

    def test_contamination_rejected_forbidden_filename(self):
        (self.agent / "patch.diff").write_text("diff --git ...\n")
        res = check_input_package(self.agent, **self.kw)
        self.assertFalse(res.ok)

    def test_contamination_rejected_trace_bytes(self):
        (self.agent / "task.md").write_text("task\n\nsee events.jsonl for details\n")
        res = check_input_package(self.agent, **self.kw)
        self.assertFalse(res.ok)

    def test_contamination_rejected_private_path(self):
        (self.agent / "environment.json").write_text('{"note": "/private/evidence/dir"}\n')
        res = check_input_package(self.agent, forbidden_path_strings=["/private/evidence/dir"], **self.kw)
        self.assertFalse(res.ok)

    def test_contamination_rejected_future_commit(self):
        src = self.agent / "source"
        _git(src, "config", "user.email", "t@t")
        _git(src, "config", "user.name", "t")
        (src / "sneaky.txt").write_text("x")
        _git(src, "add", "-A")
        _git(src, "commit", "-qm", "future")
        res = check_input_package(self.agent, **self.kw)
        self.assertFalse(res.ok)

    def test_contamination_rejected_remote(self):
        _git(self.agent / "source", "remote", "add", "origin", "https://example.invalid/x.git")
        res = check_input_package(self.agent, **self.kw)
        self.assertFalse(res.ok)

    def test_hash_mismatch_rejected(self):
        (self.agent / "task.md").write_text("tampered task\n")
        res = check_input_package(self.agent, **self.kw)
        self.assertFalse(res.ok)

    def test_escaping_symlink_rejected(self):
        outside = self.tmp / "secret.txt"
        outside.write_text("secret")
        (self.agent / "leak.txt").symlink_to(outside)
        res = check_input_package(self.agent, **self.kw)
        self.assertFalse(res.ok)

    def test_no_secret_or_private_path_leak(self):
        (self.agent / "environment.json").write_text('{"cred": "env:OPENCODE_SERVER_PASSWORD"}\n')
        res = check_input_package(self.agent, **self.kw)
        self.assertFalse(res.ok)

    def test_patch_overlap_is_supplementary(self):
        patch = self.tmp / "p.diff"
        patch.write_text("+++ b/src/x.ts\n+const meaningfulLongLine = 1;\n")
        res = check_input_package(self.agent, patch_file=patch, **self.kw)
        self.assertTrue(res.ok, res.diagnostics)
        self.assertIn("patch_overlap", res.supplementary)
        self.assertIn("supplementary", res.supplementary["patch_overlap"]["note"])

    def test_environment_and_setup_tamper_rejected(self):
        """environment.json/setup.sh edits are caught when hashed."""
        hashes = {
            "task.md": sha256_file(self.agent / "task.md"),
            "environment.json": sha256_file(self.agent / "environment.json"),
            "setup.sh": sha256_file(self.agent / "setup.sh"),
        }
        (self.agent / "environment.json").write_text('{"node": {"version": "0.0.0"}}\n')
        res = check_input_package(self.agent, expected_hashes=hashes, pinned_sha=self.sha)
        self.assertFalse(res.ok)
        bad = next(c for c in res.checks if c["name"] == "input-hashes")
        self.assertIn("environment.json", bad["detail"])

    def test_ignored_file_in_source_rejected(self):
        """A git-ignored file inside source/ evades git status but not the
        on-disk file-set comparison."""
        repo = self.tmp / "repo-ignored"
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / ".gitignore").write_text("*.hidden\n")
        (repo / "a.txt").write_text("one\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "first")
        sha = _git(repo, "rev-parse", "HEAD")
        agent2 = self.tmp / "case2" / "agent"
        agent2.mkdir(parents=True)
        (agent2 / "task.md").write_text("t\n")
        materialize(repo, sha, agent2 / "source")
        (agent2 / "source" / "answer.hidden").write_text("the solution\n")
        res = check_input_package(agent2, pinned_sha=sha)
        self.assertFalse(res.ok)
        fileset = next(c for c in res.checks if c["name"] == "source-file-set")
        self.assertFalse(fileset["ok"])
        self.assertIn("answer.hidden", fileset["detail"])
        # git status stayed clean — the file set is what caught it.
        clean = next(c for c in res.checks if c["name"] == "git-clean")
        self.assertTrue(clean["ok"])

    def test_missing_source_file_rejected(self):
        (self.agent / "source" / "a.txt").unlink()
        res = check_input_package(self.agent, **self.kw)
        self.assertFalse(res.ok)


class TestPackageFingerprint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t2e-fp-"))
        self.repo = self.tmp / "origin"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        (self.repo / ".gitignore").write_text("*.hidden\n")
        (self.repo / "a.txt").write_text("one\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "first")
        self.sha = _git(self.repo, "rev-parse", "HEAD")
        self.agent = self.tmp / "case" / "agent"
        self.agent.mkdir(parents=True)
        (self.agent / "task.md").write_text("fixture task\n")
        (self.agent / "environment.json").write_text("{}\n")
        (self.agent / "setup.sh").write_text("#!/bin/sh\n")
        materialize(self.repo, self.sha, self.agent / "source")

    def test_fingerprint_stable(self):
        self.assertEqual(package_fingerprint(self.agent),
                         package_fingerprint(self.agent))

    def test_task_edit_changes_fingerprint(self):
        before = package_fingerprint(self.agent)
        (self.agent / "task.md").write_text("other\n")
        self.assertNotEqual(before, package_fingerprint(self.agent))

    def test_environment_edit_changes_fingerprint(self):
        before = package_fingerprint(self.agent)
        (self.agent / "environment.json").write_text('{"x":1}\n')
        self.assertNotEqual(before, package_fingerprint(self.agent))

    def test_ignored_file_changes_fingerprint(self):
        before = package_fingerprint(self.agent)
        (self.agent / "source" / "a.hidden").write_text("x\n")
        self.assertNotEqual(before, package_fingerprint(self.agent))

    def test_tracked_source_edit_changes_fingerprint(self):
        before = package_fingerprint(self.agent)
        (self.agent / "source" / "a.txt").write_text("edited\n")
        self.assertNotEqual(before, package_fingerprint(self.agent))


if __name__ == "__main__":
    unittest.main()
