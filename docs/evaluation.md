# Input provenance, leakage controls, and behavioral evaluation

## Input package contract

The agent receives `case/agent/` only. `t2e check-inputs` enforces, before
any execution:

- **Allowlist** — top level is exactly `task.md`, `environment.json`,
  `setup.sh`, `source/`; nothing else may appear.
- **Hashes** — `task.md` (and any future hashed inputs) match
  `case.json.input_hashes`.
- **Provenance** — text files are scanned for retained-run markers
  (`patch.diff`, `events.jsonl`, `job.snapshot`, `devflow-submission`,
  credential variable names) and for the private evidence path.
- **Mount boundary** — no symlink escapes the package.
- **Git boundary** — `source/` is a depth-1 repository: exactly one
  commit, `HEAD` equal to the pinned SHA, no remotes, clean worktree.
- **Forbidden filenames** — evidence artifact names never appear outside
  `source/`.

Any contamination introduced during case construction — later trace
content, patch/result/plan files, unsupported prose, future Git history —
is rejected before execution. Patch string overlap against agent inputs
is computed as supplementary information only: common code is not proof
of leakage.

## Agent boundary during the run

- The agent's working directory is a disposable copy of `case/agent/`;
  the evidence directory is never mounted or referenced.
- Dependencies are acquired by `setup.sh` **before** agent start. After
  that point, external search and solution retrieval are restricted at
  the harness tool level (web search/fetch tools are disabled) while the
  configured inference service remains usable. OS-level egress filtering
  is not enforced — a recorded approximation.
- The original run's server credential (`OPENCODE_SERVER_PASSWORD`) is
  stripped from the agent environment; the stripping is recorded.

## Behavioral checks (evaluator-side)

`t2e evaluate --target <tree>` drives the target's own CLI with a fixture
runtime — the same shape as the target's test harness — and verifies
behavior derived from the initial requirements:

1. **Inline task execution** — a task held as a string runs without
   creating a task file in the project (stdin `--task -` first;
   equivalent inline forms are tried and the chosen strategy recorded).
2. **Digest equality** — the inline form's `task.digest` equals the
   file-based form's for identical content.
3. **Honest provenance** — `task.source` records a symbolic origin
   (e.g. `stdin`), never a real or fabricated path.

Fixture runtimes mean verification never needs real model inference.

## Controls

`t2e controls` validates the evaluator itself:

- **Negative control** — the unmodified starting tree must NOT satisfy
  the requirement checks.
- **Positive control** — the original patch applied evaluator-side must
  satisfy them. The patch stays evaluator-side and is never published.

Absent control evidence, successful controls, and failed or inconsistent
controls are reported separately; unsupported controls never establish
evaluator readiness. Scoring outcomes, failure classifications, and the
model-training contamination limit are documented in the PoC report.
