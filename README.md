# spike-trace2eval

A single-case proof of concept for turning an existing coding-worker execution trace into an evaluation case for an independent coding agent, inspired by [Trace2Eval](https://arxiv.org/abs/2611.06506).

**Planning only. The extractor, execution environment, and evaluator are not implemented. No independent agent replay has been validated.**

## First case

R1 is an existing coding-worker execution for [yuurei issue #195](https://github.com/shimpeiws/yuurei/issues/195).

- Task: accept task content through stdin or an equivalent inline form, preserving the digest of identical file-based input.
- Starting commit: `99cd09ab71b0ba2807f1d39d0a26dfe042e89bac`.
- Retained evidence: one session, 6,588 events, the initial job, starting SHA, 85 tool operations, and five check results.
- Known limits: the complete original environment, including the OS and internal harness instructions, cannot be recovered. The original solution is already public in [PR #223](https://github.com/shimpeiws/yuurei/pull/223).

The source trace is stored privately. This repository does not include the trace, full initial prompt, original patch, or credentials. These findings summarize a read-only investigation conducted on 2026-11-09; not all findings can be independently reproduced from public material alone.

## Questions this experiment answers

1. Can the saved job and pinned Git SHA recover the original requirements and starting source tree?
2. Can evaluation inputs be restricted to initial information and checked for solution or outcome leakage?
3. Can a runnable case be produced for an independent agent without changing coding-worker?

Generate initial inputs deterministically from the job and starting commit. Do not summarize the full trace into a task. Keep the original trace and artifacts outside the evaluated agent's environment as evaluator-side evidence.

## Plan

The [R1 PoC plan](docs/poc-plan.md) records the evidence, reconstruction limits, differences from the paper, leakage controls, implementation steps, and acceptance criteria.

The current deliverable consists of this README and the plan. Multiple cases, a general-purpose framework, new coding-worker instrumentation, and performance rankings are out of scope.
