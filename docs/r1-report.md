# R1 single-case Trace2Eval PoC — report

Generated from the stage records under `records/`. Contains no
private paths, raw trace bytes, full prompt text, patch content, or
credentials; all evidence references are record files plus hashes.

## Stage outcomes

- **evidence**: passed
- **source**: passed
- **environment**: passed
- **leakage**: passed
- **controls**: passed
- **execution**: passed
- **evaluation**: passed

## Answers to the PoC questions

1. *Can the saved job and pinned Git SHA recover the original requirements and starting source tree?* **Yes.** The evidence stage recomputed the checksum, identity, sequence, tool-pairing, and v2-prompt checks (run `20261008T033410Z-7f2a94ec`); the source stage materialized the pinned commit `99cd09ab71b0…` with 225 tracked files verified against git's content-addressed objects.
2. *Can evaluation inputs be restricted to initial information and checked for solution or outcome leakage?* **Yes.** The input package passed the allowlist, hash, provenance, mount-boundary, and git-history checks; contamination fixtures are rejected before execution.
3. *Can a runnable case be produced for an independent agent without changing coding-worker?* **Yes — the case generated, executed (opencode), and was scored (satisfied).**

## Independent run

- harness: `opencode`, model: `opencode-go/deepseek-v4.1-flash`
- termination: `completed` after 521.5s (timeout budget 2400s; step limits not enforceable in-process)
- os: Darwin 25.6.0 (arm64)
- produced patch: 13084 bytes across 8 files; sha256 `d65bbb014f2b8725…`
- behavior checks: baseline-file-run=ok, file-source-matches=ok, file-digest-is-content-hash=ok, file-body-received=ok, inline-task-executes=ok, inline-body-received=ok, no-task-file-created=ok, digest-equal=ok, inline-digest-is-content-hash=ok, digest-varies-with-input=ok, provenance-honest=ok
- declared repo checks: install-and-focused-tests=0, build=0, lint=0, format=0, knip=0
- recorded deviations:
  - harness: original used OpenCode 1.18.34 via a loopback server + dsh adapter; this run uses the configured independent harness directly
  - harness system prompt and injected context are unrecoverable; operator-level agent configuration may differ
  - network egress is restricted at the harness tool level (no web tools), not at OS level
  - run executed before the .git-hygiene fix (FIND-001): .git/FETCH_HEAD and a reflog naming the operator's local clone path and git identity were present in the agent workspace; agent tool trace shows no read of them and no access to the host clone
  - run executed with a blocklist-only env filter (FIND-002): nearly all operator env names were passed through (recorded in env_keys); agent tool trace shows no env reads beyond its workspace

## Limitations

- The original OS, PATH, locale, and internal harness instructions are unrecoverable; deviations are recorded in `environment.json` and the execution record.
- Network restriction is enforced at the harness tool level, not OS egress.
- These controls check case-construction contamination; they cannot prove the public solution is absent from model training data.
- Existing checks already passed on the starting commit; their success alone does not establish task completion.
