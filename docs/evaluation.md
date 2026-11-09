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
- **Git hygiene** — `.git` carries no operator metadata: no `FETCH_HEAD`
  (which records the fetch source path), no reflogs (which record the
  operator's git identity), and no host paths or `file://` URLs in
  textual metadata. Content-addressed objects are exempt — the pinned
  commit's own authorship is legitimately part of the source.
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
  the harness tool level (web search/fetch tools and external-directory
  file access are denied) while the configured inference service remains
  usable. OS-level egress filtering is not enforced — a recorded
  approximation.
- The agent subprocess environment is **default-deny**: only a recorded
  allowlist of functional variables (PATH, HOME, TMPDIR, locale, proxy,
  toolchain) passes; any name resembling a credential is stripped
  unconditionally. Both lists are recorded in the execution record.
- The harness's `external_directory` deny does not cover `bash`: the
  agent can write outside its workspace. This is a recorded deviation;
  the pipeline compensates by hashing the case package before and after
  launch (`post_run_integrity`) — a changed input tree marks the
  execution record failed. Workspace-external writes beyond that watch
  set are not detectable and are a stated limitation.
- Refused launches (`preflight_refused`, `setup_failure`,
  `boundary_refused`) are recorded in `execution_refused.json` and do
  **not** consume the single experiment run; only a real launch attempt
  writes `execution.json`, guarded by a crash-safe marker.

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
