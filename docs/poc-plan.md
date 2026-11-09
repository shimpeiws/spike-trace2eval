# Single-case Trace2Eval PoC plan for R1

Created: 2026-11-09. Status: planned. Implementation, environment setup, and an independent agent run have not started.

## 1. Objective and decision criteria

Generate one evaluation case for an independent coding agent from retained coding-worker execution evidence. Use R1 only.

The experiment answers whether:

1. The original task and Git-tracked starting state can be recovered.
2. Input provenance checks can prevent solutions and outcomes discovered during execution from entering the evaluated agent's input.
3. Case generation, independent execution, and artifact evaluation can work without changing coding-worker.

The experiment does not require a complete replica of the original machine or proof that the public solution is absent from model training data. It verifies the recovered source and a runnable approximation of the execution environment.

## 2. Relationship to the paper

The [Trace2Eval paper and Appendix A.2](https://arxiv.org/html/2611.06506v1) extract objectives and explicit requirements from interactions. File reads and pre-edit contents recover initial files; inference or public resources fill gaps. Session-generated artifacts are excluded, and dependencies are separated into initial conditions and later installations using temporal evidence.

Recovery is checked by replaying original tool calls, comparing results, and conducting human verification. This does not prove complete environment identity. Artifact evaluation uses human-calibrated rubrics.

This PoC makes the following independent simplifications. It is not a full reproduction of the paper.

| Paper component | Treatment in R1 |
| --- | --- |
| Recover requirements from conversation | Use the issue snapshot in the saved job directly |
| Infer or generate missing files | Use the pinned Git tree; do not synthesize missing source |
| Replay all tool calls and repair the environment | Check the starting state, setup, and required verification only |
| Use multiple recovery agents | Use deterministic extraction and human review |
| Select many cases and calibrate rubrics | Check one case for requirement satisfaction and reproducibility |

The separation between initial state and session-generated information follows the paper. Input provenance restrictions and mechanical checks are PoC-specific design choices. The paper's implementation could not be retrieved during investigation, so implementation-level correspondence remains unverified.

## 3. Selected case: R1

| Item | Observed evidence |
| --- | --- |
| Target | [shimpeiws/yuurei #195](https://github.com/shimpeiws/yuurei/issues/195) |
| Original requirement | Execute a task held as a string without creating a task file in the project; preserve the task digest of identical file input |
| Starting SHA | `99cd09ab71b0ba2807f1d39d0a26dfe042e89bac` |
| Worker run ID | `20261008T033410Z-7f2a94ec` |
| Execution date | 2026-10-08; approximately seven minutes |
| Agent and harness | OpenCode, legacy adapter name `dsh`, OpenCode version `1.18.34` |
| Sessions | One; excludes planner and converge runs |
| Trace | `events.jsonl`: 6,588 events, 2,254,759 bytes |
| Tool operations | 85 distinct operations: 50 bash, 21 edit, 13 read, and one write |
| Checks | All five worker checks exited with code 0 |
| Independent references | Original issue, [PR #223](https://github.com/shimpeiws/yuurei/pull/223), target Git history, and a pre-dispatch readiness record |

The retained evidence is in a private coding-worker repository. An implementer with access supplies the following repository-relative directory as input:

```text
eval/evidence/issue-34/
  README.md
  SHA256SUMS
  runs/v03-bounded-devflow-run-yuurei-195/20261008T033410Z-7f2a94ec/
```

Supporting records are `eval/jobs/issue-34/task.json` and `eval/jobs/issue-34/readiness.md`. The evidence-recording commit is `28a21f6ffc7de906ceeda8100fbd6a2ae1239bac`. Do not copy those records into this repository.

### Verified consistency

- JSONL sequence numbers are consecutive from 1 through 6,588.
- There are 328 `tool.call` events, including state updates. Grouping by distinct IDs yields 85 operations, each with a matching `tool.result`.
- The starting SHA agrees across the job, manifest, and `checkout.ready` event.
- The `git status` output at sequence 94 records a detached, clean checkout.
- Regenerating the prompt from the saved job and historical v2 template exactly matches the 4,066-byte prompt at sequence 9.
- The prepared task and job contain the same issue snapshot.
- All 33 files listed in `SHA256SUMS` match their recorded checksums.

These are findings from the read-only investigation on 2026-11-09. The extractor must repeat the checks rather than accept this report as proof for a later input.

## 4. Storage architecture and reconstruction limits

coding-worker maps a prepared task to a job, clones the repository, and checks out the pinned SHA in detached mode. The disposable checkout and retained evidence occupy separate directories. Evidence includes the job, manifest, normalized events, result, patch, and check output.

OpenCode tool events record file operations and commands. They are not an OS-wide audit log. Redaction and size limits can remove payloads, so evidence presence and completeness require separate checks.

Current coding-worker code has terminal paths that delete sessions and managed runtime directories. Do not assume historical sessions remain available through an API or database. Use R1's retained files.

In this matrix, `observed` means directly recorded in events or the same run's job and manifest. Git and pre-dispatch records are identified separately when they supplement the trace.

| Information | Classification | Evidence and limits |
| --- | --- | --- |
| Worker task, acceptance criteria, and constraints | observed | `job.snapshot.json` and sequence 9 |
| Human conversation preceding the issue | missing | Not included in retained evidence |
| Initial task prompt | observed | Sequence 9 matches the historical v2 template |
| Harness system prompt and all automatically injected context | missing | The complete final model input is not retained |
| Repository instruction files | observed, supplemented by Git | Read output at sequence 89 and files at the starting SHA |
| Instructions added during execution | missing | No identified additional human instruction; coarse role metadata prevents proof of absence |
| Repository and starting SHA | observed | Job, manifest, and sequence 2 |
| Detached, clean starting checkout | observed | Sequence 94 |
| Complete Git-tracked starting source | missing from trace alone | Recoverable from Git objects at the specified SHA |
| OS family | inferred | Paths suggest macOS; the OS version is unknown |
| Node and pnpm | observed | Sequence 2103: Node 26.8.2 and pnpm 11.6.0 |
| Dependencies and setup | partly observed, supplemented by Git | Install output at sequence 2156 and the starting lockfile |
| Test commands | observed | Job checks and sequences 6576 onward |
| Environment variables, complete PATH, and locale | missing | No complete environment snapshot |
| Inference service dependency | observed | Original run used OpenCode Go; do not recover credentials |

The starting `mise.toml` specifies Node 24, while the observed executable was 26.8.2. Prefer the observed version when approximating the original execution and record the discrepancy. Package installation occurred during the session; do not include `node_modules` in the starting source tree.

## 5. Leakage controls

### Observed sources of leakage

| Source | Information learned after task dispatch |
| --- | --- |
| Edit result at sequence 3544 | Concrete implementation changes |
| Test failure at sequence 4145 | A failure discovered after implementation |
| Dependency CLI inspection at sequence 4802 | Understanding obtained through investigation |
| Passing tests at sequence 5231 | Successful post-fix outcome |
| Diff near sequence 5852 | Modified source code |
| Final response at sequence 6564 | Solution and verification summary |
| Original patch, PR, and subsequent Git history | Original implementation and later fixes |

Even the initial prompt is normalized with `role: assistant`. Role filtering alone cannot separate input from solution. The standard export retains events and results, so it is not a safe evaluated-agent input.

### Allowed input sources

Generate evaluated-agent inputs only from:

- The saved job's issue, acceptance criteria, task constraints, checks, and budgets.
- Source files at the pinned starting SHA.
- Explicitly selected environment versions and setup instructions that contain no solution information.

The evaluator may inspect the full trace, but must not add a free-form summary of it to agent input. Additional prose must cite its initial-information source. Reject unsupported additions.

Keep the original trace, patch, result, review, and plan inaccessible to the evaluated agent. Materialize only the specified commit's files; do not provide later commits, remotes, or the original working directory. After dependency acquisition, restrict external search and solution-retrieval paths during execution.

Check the input file allowlist, provenance, and hashes. Treat string overlap with the original patch as a supplementary check, since common code is not proof of leakage.

These controls check contamination introduced during case construction. They cannot prove that a model has not learned the publicly available solution.

## 6. Implementation approach and input/output

| Approach | Decision |
| --- | --- |
| A: Parse existing evidence directly | Adopt. The job and Git provide the main inputs without changing coding-worker |
| B: Use existing export or session APIs | Reuse job structures and integrity metadata. Do not rely on standard exports, which retain solutions, or on historical sessions remaining available |
| C: Instrument future executions | Unnecessary for R1. Reconsider only when future cases lack essential information |

Inputs are the retained evidence directory, target Git objects, and an environment policy. Supply local absolute paths as arguments rather than publishing machine-specific configuration.

The output structure is provisional. Keep the implementation limited to what this single case needs.

```text
case/
  case.json
  task.md
  environment.json
  setup.sh
  source/
```

`case.json` records the case ID, repository, starting SHA, requirements, acceptance criteria, constraints, checks, budgets, and input hashes. `environment.json` records observed versions, selected versions, unknowns, and deviations. `setup.sh` performs dependency setup only.

Keep raw evidence, detailed provenance, leakage reports, evaluator tests, and run results outside the directory provided to the evaluated agent. Separate public planning documents from private experiment data.

## 7. Implementation steps and acceptance criteria

### Step 1: Validate evidence and extract initial conditions

Check the job, run ID, starting SHA, checksums, event sequence, call/result correspondence, and truncation. Generate the task exclusively from the job's initial information, and compare the historical v2 prompt.

Accept this step when identical inputs produce identical case content and hashes, and insufficient evidence or inconsistencies cause an explicit refusal.

### Step 2: Prepare starting source and an approximate environment

Materialize files from the starting commit only. Use Node 26.8.2, pnpm 11.6.0, and that commit's lockfile. Separate initial source from files generated by dependency setup.

Accept this step when the source matches the specified commit and setup plus existing checks execute in isolation. Record differences such as OS and cache state. Full trajectory replay is not required.

### Step 3: Check leakage and evaluate requirements

Inspect the evaluated agent's mounts, input files, Git history, and network access. Verify behavior derived from the initial requirements independently of the original patch.

The pre-dispatch record reports that the starting SHA already passed all five existing checks. Passing those checks alone cannot establish task completion. Add behavior checks for:

- Executing a string task without creating a task file in the project.
- Matching the task digest of identical file-based input.
- Recording task provenance honestly.

The issue permits stdin or an equivalent inline form. Do not require the original implementation's internal structure or arbitrary string representation. Existing target tests use fixture runtimes, so verification itself does not require real model inference.

Accept this step when the evaluator distinguishes the unsolved starting state from an artifact satisfying the requirements, without including later discoveries in agent input. If the original patch is used to validate a positive control, keep it on the evaluator side only.

### Step 4: Run an independent agent once

Provide only the initial task and starting source. Record the artifact, checks, environment, budgets, and failure reasons. Record changes to harness-specific conditions such as the original fixed-model instruction.

Success does not require the independent agent to solve the task. It requires case generation, execution, and scoring to work, with agent failures distinguished from environment failures.

The unvalidated estimate is a few hundred lines and one to three days for this case. Dependency acquisition and OS differences can change that estimate. Do not build a general-purpose framework.

## 8. Decisions to finalize during implementation

| Decision | Recommended starting policy |
| --- | --- |
| OS | Prefer the original macOS family; validate Linux separately |
| Node | Prefer observed 26.8.2 over declared 24 |
| Dependencies | Pin the lockfile; allow a prewarmed cache and record it |
| Harness conditions | Separate them from task requirements and document changes for the independent agent |
| Evaluation criteria | Derive them from the initial issue's allowed behavior, not the original implementation |
| Data publication | Documents only for now. Review contents and rights separately before publishing traces or generated cases |

## 9. Out of scope

- Changes to coding-worker, operational configuration, or active workers.
- New collection runs or instrumentation.
- Cases other than R1, or reconstruction spanning planner and converge runs.
- Full tool replay or the paper's complete rubric implementation.
- Broad model comparisons, rankings, or claims of training-data contamination resistance.
- Publishing raw traces, full initial prompts, original patches, or credentials.

## 10. Public references

- [Trace2Eval paper](https://arxiv.org/abs/2611.06506)
- [yuurei issue #195](https://github.com/shimpeiws/yuurei/issues/195)
- [yuurei starting commit](https://github.com/shimpeiws/yuurei/tree/99cd09ab71b0ba2807f1d39d0a26dfe042e89bac)
- [yuurei PR #223](https://github.com/shimpeiws/yuurei/pull/223)

The PR is an independent reference but contains the solution; do not expose it to the evaluated agent. Inspecting the original trace and readiness record requires separate access to the private evidence source.
