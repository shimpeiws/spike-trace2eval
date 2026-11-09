# Overall assessment of the R1 spike

Assessment date: 2026-11-10

This assessment covers [PR #5](https://github.com/shimpeiws/spike-trace2eval/pull/5) at commit `69f6f24e20c04d3a7681c8778a62061c50e5a799`. It evaluates the implementation and report at that revision and does not incorporate subsequent fixes. PR #5 was unmerged at the time of assessment, so links to its implementation documents use the fixed commit.

## Overall judgment

The spike establishes a concrete path for converting one well-preserved real task from existing coding-worker records into a rerunnable evaluation case. It implements the stages needed to extract the initial requirements and starting SHA, retrieve the source, prepare an approximate environment, run an agent, and evaluate the artifact. This supports the feasibility of using existing execution history as evaluation material without changing coding-worker.

Successful execution and assurance against leakage require separate judgments. The public report describes one completed run with a `satisfied` result, but this review did not independently verify its primary evidence. That run also predates some of the safeguards. The outcome is therefore an implementation of single-case conversion and re-execution, accompanied by a report of a real run. It does not establish complete environment reconstruction, independent solution without leakage, or general trace reconstruction.

This document assesses accomplishments, design choices, experimental validity, and decisions for further validation. Individual defects and code fixes from the code review are outside its scope. The assessment does not imply that those defects have been resolved.

## Direct observations and reported results

The review examined the public code, tests, documentation, and [R1 plan](poc-plan.md). Running `./run_tests.sh` at the reviewed commit passed all 90 tests using synthetic data. This is a directly observed result.

The review did not inspect the private original trace, stage records from the real run, the agent's tool trace, the generated patch, or the run's verification logs. Run measurements and outcomes below are attributed to the [R1 report](https://github.com/shimpeiws/spike-trace2eval/blob/69f6f24e20c04d3a7681c8778a62061c50e5a799/docs/r1-report.md) at the reviewed commit. This assessment also does not independently establish equivalence to the paper's implementation.

| Area | Evidence available in this review | Assessment |
| --- | --- | --- |
| Initial requirement extraction | Code that constructs the case from saved job fields and compares the recorded prompt with the historical v2 template | Implements a traceable recovery method that avoids freely rewriting the requirements after execution |
| Starting source recovery | Code and synthetic tests for fetching a specified SHA and restricting history and remotes | Makes source recovery through Git references concrete, even when the trace does not contain every source file |
| Environment preparation | Code that records observed and selected values, unknowns, and deviations, then installs dependencies and runs existing checks | Provides an explicit approximation of the original environment |
| Evaluation criteria and controls | Requirement-derived behavior checks and controls using the unchanged tree and original patch | Adopts a design that distinguishes existing test success from completion of the new task |
| Independent re-execution | The report describes completion in 521.5 seconds and a 13,084-byte patch across eight files | A completed real run is reported. This review did not independently verify the artifact's correctness |
| Evaluation of the real run | The report states that behavior checks and all five existing checks passed | Useful as a pipeline execution report. The reported values alone are not independent proof of requirement satisfaction |
| Automated tests | All 90 tests passed during review | Provides stages that can be checked with synthetic data. Fidelity to the real evidence and validity of scoring need separate verification |

## Valuable design choices and accomplishments

### Initial information is separated from information learned during execution

The central choice is to construct inputs from the saved job's initial requirements rather than summarize the entire trace into a new task. This reduces the routes through which intermediate failures, investigation results, modified code, and final answers can enter the new agent's input. It also makes the origins of the requirements and starting source easier to explain.

The [evidence input contract](https://github.com/shimpeiws/spike-trace2eval/blob/69f6f24e20c04d3a7681c8778a62061c50e5a799/docs/evidence-input.md) and [evaluation contract](https://github.com/shimpeiws/spike-trace2eval/blob/69f6f24e20c04d3a7681c8778a62061c50e5a799/docs/evaluation.md) reflect this distinction. Keeping the original patch on the evaluator side as a positive control also fits this goal. Design intent and actual isolation guarantees remain separate, but documenting the information boundary is an accomplishment.

### Recovery has mechanically checkable evidence

Checksums, job-to-manifest identity, event sequences, tool call and result pairing, and prompt equality make recovery easier to verify than an operator's narrative alone. Source references attached to extracted fields allow later inspection of where each requirement came from.

Matching hashes establish consistency among the evidence at hand. They do not establish that the evidence has remained unchanged since collection or that no unrecorded input existed. Within that scope, the provenance mechanism can support future cases.

### Starting source and dependency acquisition are treated separately

The implementation distinguishes source at the starting SHA from dependencies produced by later setup. This separates files that existed initially from material acquired to make the case runnable.

The [environment documentation](https://github.com/shimpeiws/spike-trace2eval/blob/69f6f24e20c04d3a7681c8778a62061c50e5a799/docs/environment.md) also records the difference between declared Node 24 and observed Node 26.8.2. Treating the result as an approximation based on observations, with the discrepancy visible, is appropriate.

### Existing checks and task-specific evaluation have distinct roles

The starting commit already passed the five existing checks. The evaluation therefore targets string task execution, digest equality, and task provenance. This separates the ability to build the project from satisfaction of the new feature requirements.

Using the unchanged tree as a negative control and the original patch as a positive control is useful. Distinguishing those two states provides a basic check of evaluator behavior. It does not establish that the evaluator rejects every incorrect implementation, so control success and evaluator sufficiency remain separate questions.

### One complete workflow is implemented without changing the operational system

Recovery, preparation, input checking, execution, scoring, and reporting are implemented as separate tools. This makes it possible to investigate the value of existing records before changing coding-worker or collecting new traces.

The plan also keeps the experiment to one case and does not require a general framework or performance ranking. Allowing a failed agent attempt to remain a valid experimental outcome is appropriate. Whether a case can be constructed is a useful question independent of whether the agent solves it.

## Constraints on the experimental conclusions

### Saved initial requirements leave harder reconstruction questions untested

R1 retains the requirements and starting SHA in its initial job. The main operation is to validate those records and convert them into a case. The spike does not test integration of requirements scattered across a conversation, recovery from incomplete traces, or reconstruction across multiple sessions.

It therefore does not establish that an arbitrary execution trace can recover the original task. This limitation does not remove R1's value, but it defines the boundary for judging whether the method transfers to another case.

### The environment is runnable, but equivalence to the original remains unverified

The original OS version, complete PATH and environment variables, locale, internal instructions, and automatically injected context are unavailable. Replaying all original tool operations and comparing their results is also outside this spike's scope.

Successful setup and checks support the claim that those operations work in the selected environment. They do not establish that the original agent saw the same environment or that original failures would recur for the same reasons. Differences between the original run and re-execution cannot be attributed solely to model ability.

### The historical run and later safeguards provide different evidence

The R1 report explicitly states that the real run included Git metadata and broad environment-variable inheritance that were removed later. Subsequent safeguards and tests improve conditions for future runs. They do not change the conditions under which the historical run occurred.

That single run cannot establish an independent evaluation with all current safeguards applied. Its results can remain useful if the historical conditions and the current safeguards are interpreted separately.

### Possible reads and observed reads are different claims

The setup does not isolate filesystem access and network traffic at the OS level. Keeping host evidence and the original solution outside the working directory does not establish that they were inaccessible. Post-run hash comparisons cannot show whether files were read.

The report's statement that no reads appear in the agent's tool trace is an observation about the inspected record. It is not a guarantee that the agent could not read those files. This assessment does not conclude that leakage actually occurred.

### Public solutions and case selection limit what the result can establish

The original solution is public. Restrictions on input construction and runtime retrieval cannot exclude the possibility that the model learned the solution during training. R1 was also selected as a tractable case with retained initial requirements and source identity, limited external dependencies, and testable behavior.

One case and one attempt cannot estimate general reconstruction success, agent success rates, or relative model performance. A separate run of the same model creates a new attempt, but does not establish independence from prior knowledge of the solution.

### Public evidence supports only partial reproduction

Keeping the original trace and generated patch private protects sensitive information and solution content. It also limits a public review to the implementation, synthetic tests, and published experimental report.

A separate reviewer with access to the private material could strengthen verification of the real-run report. An evidence hash inventory and audit record could also help without disclosing the contents. The public artifacts alone do not support complete reproduction.

## Answers to the original questions

| Question | Assessment |
| --- | --- |
| Can the original requirements and starting source be recovered? | Extraction and source retrieval based on R1's saved job and Git references are implemented. Agreement with the real evidence is reported, but this review did not revalidate the private material |
| Can inputs be restricted to initial information and checked for solution or outcome leakage? | The source categories and checking stages are concrete. Historical run conditions and limits of the records prevent a conclusion that leakage prevention was established |
| Can re-execution and evaluation proceed without changing coding-worker? | The workflow is implemented, and completion of one real run and scoring is reported. This supports pipeline feasibility |

This assessment recognizes progress against the original single-case PoC plan. It does not infer reconstruction quality or independence from the `satisfied` value alone.

## Useful questions for the next experiment

Beyond code fixes, the following experimental decisions would add evidence:

- Treat any additional run under current conditions as a new experiment, separate from the historical attempt. Freeze its inputs and evaluation criteria before launch, and retain the outcome even if it fails.
- Ask a separate reviewer with access to the original trace to compare the initial requirements with the generated case. Check for added, missing, or altered requirements to obtain evidence about reconstruction fidelity itself.
- Choose a next case with one reconstruction difficulty absent from R1. Record candidates and exclusion reasons to avoid retaining only successful cases.
- Use information that could not be recovered here to inform future trace collection. Prioritize initial inputs, added instructions, and environment identity, with sensitive information stored separately.

The spike's clearest contribution is an implementation and reviewable documentation of the inputs and stages needed to convert existing history into an evaluation case. The next experimental decision is which evidence to add about case fidelity and independence, beyond further reports that the workflow ran.
