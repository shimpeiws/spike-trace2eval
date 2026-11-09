# Input provenance, leakage controls, and behavioral evaluation

## Input package contract

The agent receives `case/agent/` only. `t2e check-inputs` enforces, before
any execution:

- **Allowlist** — top level is exactly `task.md`, `environment.json`,
  `setup.sh`, `source/`; nothing else may appear.
- **Hashes** — `task.md`, `environment.json`, and `setup.sh` all match
  `case.json.input_hashes` (pinned at generation time, so post-generation
  edits to any input are detected).
- **Provenance** — text files are scanned for retained-run markers
  (`patch.diff`, `events.jsonl`, `job.snapshot`, `devflow-submission`,
  credential variable names) and for the private evidence path.
- **Mount boundary** — no symlink escapes the package.
- **Git boundary** — `source/` is a depth-1 repository: exactly one
  commit, `HEAD` equal to the pinned SHA, no remotes, clean worktree.
- **Source file set** — every file on disk under `source/` (excluding
  `.git`) equals the pinned tree's tracked set. `git status` cannot see
  `.gitignore`'d files; the file-set comparison can, so an ignored file
  is not a covert channel for solution material.
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
  `boundary_refused`, `input_drift`) are recorded in
  `execution_refused.json` and do **not** consume the single experiment
  run; only a real launch attempt writes `execution.json`, guarded by a
  crash-safe marker.
- **Launch-time re-inspection** — immediately before the launch marker
  is written, the whole input package is re-fingerprinted
  (`inputs_fingerprint`: every input's bytes plus the source HEAD,
  index, status, and full on-disk file set) and compared with the
  fingerprint stored in the leakage record. Any drift — or a leakage
  record that predates fingerprinting — refuses the launch without
  consuming the run. The fingerprint is also stored in the execution
  record, binding the run to exactly what was inspected.
- The configured `--model` reaches the harness's own model flag in the
  launch argv; it is not merely recorded.

## Behavioral checks (evaluator-side)

`t2e evaluate --target <tree>` drives the target's own CLI with a fixture
runtime — the same shape as the target's test harness — and verifies
behavior derived from the initial requirements. The fixture shim records
the task body it actually received, so "the task ran" means the body —
not a lookalike — reached the runtime:

1. **Inline task execution** — a task held as a string runs without
   creating a task file in the project (stdin `--task -` first;
   equivalent inline forms are tried and the chosen strategy recorded).
2. **Body integrity** — the body the runtime received (captured by the
   shim) equals the body sent, for both file and inline forms.
3. **Digest is a content hash** — `task.digest` equals `sha256` of the
   task body, for both forms; and a second inline run with *different*
   content must produce a *different* digest, so a hardcoded digest
   cannot pass.
4. **Digest equality** — the inline form's `task.digest` equals the
   file-based form's for identical content.
5. **File provenance** — the file form's `task.source` is the file path
   actually passed.
6. **Honest provenance** — the inline form's `task.source` is a symbolic
   origin (e.g. `stdin`): no path separators, no filename-like suffix,
   no existing file, no whitespace.
7. **No side-effect files** — *any* new file inside the project (outside
   the evaluator-owned `bin/` and `.yuurei/runs/`) fails the run, not
   just task-shaped ones.

Fixture runtimes mean verification never needs real model inference.
All checks must pass for `satisfied`; every run uses the same selected
toolchain (`--node-bin` / `T2E_NODE_BIN`) as setup and controls.

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

## Scoring binding

`t2e score` never evaluates whatever `--workspace` happens to contain.
It reads the consumed execution record, verifies the recorded patch's
sha256, and replays **that** patch onto a fresh copy of the pinned base;
dependencies are reinstalled in the verification tree, then behavior and
declared checks run there. A patch that is absent, hash-mismatched, or
does not apply to the pinned base is an operational failure.

The verdict combines both surfaces:

- behavior `unsatisfied` → `unsatisfied`
- behavior `satisfied` + all required declared checks exit 0 → `satisfied`
- behavior `satisfied` + a required check failed → `unsatisfied:required-check-failed`
- behavior `satisfied` + declared checks not run → `inconclusive:declared-checks-not-run`

Unrun checks are therefore distinguishable from passing checks, and
`satisfied` is unreachable without them.
