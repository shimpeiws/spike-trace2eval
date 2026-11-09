# Private evidence input requirements

The extractor (`t2e extract`) validates retained R1 evidence and produces
the deterministic case inputs. The evidence itself is private: it lives in
the coding-worker repository and is **not** copied into this repository.
Supply its location as an explicit local path — nothing here enumerates or
guesses it.

## Required inputs

| Input | Location (coding-worker repo) | Required by |
| --- | --- | --- |
| Evidence directory | `eval/evidence/issue-34/` | `extract` (`--evidence`) |
| Prepared task record | `eval/jobs/issue-34/task.json` | `extract` (`--task-record`, recommended) |
| Pre-dispatch readiness record | `eval/jobs/issue-34/readiness.md` | `extract` (`--readiness`, recommended) |
| yuurei Git objects | any clone containing the pinned commit (read-only use) | `materialize` (`--repo`) |
| Original patch (positive control) | `…/runs/…/patch.diff` inside the evidence dir | `controls` (`--patch`), evaluator-side only |

## Command

```sh
PYTHONPATH=tools python3 -m t2e.cli extract \
  --evidence <path-to>/eval/evidence/issue-34 \
  --run 20261008T033410Z-7f2a94ec \
  --task-record <path-to>/eval/jobs/issue-34/task.json \
  --readiness <path-to>/eval/jobs/issue-34/readiness.md \
  --case out/case --records out/records
```

Only run `20261008T033410Z-7f2a94ec` is processed. The command:

- never executes commands recorded in the trace and never writes into the
  evidence directory,
- verifies all 33 `SHA256SUMS` entries and flags any unlisted file,
- recomputes job/task/manifest/start-event identity (canonical JSON
  hashes matching coding-worker's `jobHash`/`issueSnapshotHash`),
- recomputes the consecutive event sequence (1…6,588), the 85 distinct
  `tool.call`/`tool.result` pairs, the five check outcomes, and the
  historical v2 prompt match (byte-for-byte), and
- reports truncation markers explicitly.

Absent evidence, or evidence failing any check, exits nonzero with a
bounded diagnostic and produces no accepted case. Every stage record
follows the same contract: absent → reported missing; present and valid →
stage checks apply; present and invalid → diagnostic — and none of the
first or third ever establishes stage readiness.

## Determinism

Repeated extraction from identical inputs yields identical
`case/agent/task.md` bytes, `case.json`/`provenance.json` content, and
`case_id` (`r1-<hash16>` over the canonical identity core). Wall-clock
timestamps and local absolute paths never enter case identity.

## What the agent package contains

`out/case/agent/` holds only permitted initial inputs:

```text
task.md           # the recorded initial prompt (v2 template, job fields only)
environment.json  # observed vs selected versions, unknowns, deviations
setup.sh          # dependency setup only
source/           # pinned commit 99cd09ab…, depth-1, no remotes
```

Raw evidence, detailed provenance (`case/provenance.json`), leakage
reports, evaluator tests, and run results stay outside the agent package.
