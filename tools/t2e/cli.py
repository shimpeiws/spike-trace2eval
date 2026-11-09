"""t2e command surface.

Stage order:
  extract       evidence -> case inputs            (issue #1)
  materialize   pinned commit -> case/agent/source (issue #2)
  environment   env.json + setup.sh + isolated checks (issue #2)
  check-inputs  agent package leakage/provenance   (issue #3)
  evaluate      behavior checks on a target tree   (issue #3)
  controls      negative/positive control runs     (issue #3)
  run           one independent agent execution    (issue #4)
  score         score the produced workspace       (issue #4)
  report        sanitized report markdown          (issue #4)
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path

from . import __version__
from .canonical import (
    canonical_json,
    load_json,
    sha256_file,
    sha256_text,
    write_json_atomic,
    write_text_atomic,
)
from .environment import (
    build_environment_json,
    probe_environment,
    run_setup_and_checks,
    write_setup_sh,
)
from .evaluate import apply_patch_to_copy, classify_control, evaluate_behavior
from .evidence import (
    EXPECTED_BASE,
    case_id_for,
    case_identity,
    extract_case_fields,
    validate_evidence,
)
from .materialize import materialize
from .provenance import check_input_package
from .records import RecordState, record_path, require_record, write_record
from .report import generate_report, sanitize_scan
from .run import (
    STRIPPED_ENV,
    build_execution_record,
    collect_patch,
    launch_agent,
    preflight,
    prepare_workspace,
    workspace_boundary_ok,
)


def _fail(msg: str, code: int = 2) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return code


def _path_prefix(args: argparse.Namespace) -> str | None:
    """Selected toolchain bin dir (e.g. mise node@26.8.2) for subprocesses."""
    return getattr(args, "node_bin", None) or os.environ.get("T2E_NODE_BIN")


def _record_summary(rec) -> dict:
    return {
        "passed": sum(1 for c in rec.checks if c.ok),
        "failed": sum(1 for c in rec.checks if not c.ok),
        "diagnostics": rec.diagnostics[:40],
    }


def cmd_extract(args: argparse.Namespace) -> int:
    evidence_dir = Path(args.evidence).resolve()
    case_dir = Path(args.case).resolve()
    records_dir = Path(args.records).resolve()
    res = validate_evidence(
        evidence_dir,
        args.run,
        Path(args.task_record).resolve() if args.task_record else None,
        Path(args.readiness).resolve() if args.readiness else None,
    )
    rec_fields = {
        "run_id": args.run,
        "evidence": {"dir_name": evidence_dir.name, "sha256sums_entries": res.sha256sums_count},
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in res.checks],
        "diagnostics": res.diagnostics,
        "prompt_match": res.prompt_match,
        "prompt_bytes": res.prompt_bytes,
        "event_report": None if res.event_report is None else {
            "event_count": res.event_report.event_count,
            "sequence_consecutive": res.event_report.sequence_consecutive,
            "tool_operations": res.event_report.tool_operations,
            "tool_operations_by_name": res.event_report.tool_operations_by_name,
            "result_statuses": res.event_report.result_statuses,
            "truncation_markers": res.event_report.truncation_markers,
        },
    }
    if not res.ok or res.job is None:
        write_record(records_dir, "evidence", "failed", **rec_fields)
        print(json.dumps({"status": "failed", **_record_summary(res)}, indent=2))
        return 1
    write_record(records_dir, "evidence", "passed", **rec_fields)

    fields = extract_case_fields(res.job)
    identity_core = case_identity(fields)
    case_id = case_id_for(identity_core)
    agent_dir = case_dir / "agent"
    task_md = res.event_report.initial_prompt_text
    agent_dir.mkdir(parents=True, exist_ok=True)
    write_text_atomic(agent_dir / "task.md", task_md)
    input_hashes = {"task.md": sha256_file(agent_dir / "task.md")}
    write_json_atomic(case_dir / "provenance.json", {
        "schema_version": 1,
        "note": "evaluator-side: every extracted field cites its saved-job source; observed/inferred/missing stay distinguishable",
        "fields": fields,
    })
    write_json_atomic(case_dir / "case.json", {
        "schema_version": 1,
        "case_id": case_id,
        "identity_core": identity_core,
        "source_run": {
            "run_id": args.run,
            "job_id": res.job.get("job_id"),
            "evidence_dir_name": evidence_dir.name,
            "sha256sums_entries": res.sha256sums_count,
        },
        "task": {k: v["value"] for k, v in fields.items()},
        "input_hashes": input_hashes,
        "prompt": {"bytes": res.prompt_bytes, "sha256": sha256_text(task_md), "v2_template_match": True},
    })
    print(json.dumps({"status": "passed", "case_id": case_id, **_record_summary(res)}, indent=2))
    return 0


def cmd_materialize(args: argparse.Namespace) -> int:
    case_dir = Path(args.case).resolve()
    records_dir = Path(args.records).resolve()
    case = load_json(case_dir / "case.json")
    sha = args.sha or case["identity_core"]["base_revision"]
    dest = case_dir / "agent" / "source"
    res = materialize(Path(args.repo).resolve(), sha, dest)
    fields = {
        "repo": str(Path(args.repo).resolve().name),
        "pinned_sha": sha,
        "head": res.head,
        "commit_count": res.commit_count,
        "file_count": res.file_count,
        "remotes": res.remotes,
        "diagnostics": res.diagnostics,
    }
    write_record(records_dir, "source", "passed" if res.ok else "failed", **fields)
    print(json.dumps({"status": "passed" if res.ok else "failed", **fields}, indent=2))
    return 0 if res.ok else 1


def cmd_environment(args: argparse.Namespace) -> int:
    case_dir = Path(args.case).resolve()
    records_dir = Path(args.records).resolve()
    case = load_json(case_dir / "case.json")
    agent_dir = case_dir / "agent"
    source_dir = agent_dir / "source"
    if not source_dir.is_dir():
        return _fail("agent/source missing — run materialize first")

    selected = probe_environment(source_dir, path_prefix=_path_prefix(args))
    env_json = build_environment_json(selected)
    write_json_atomic(agent_dir / "environment.json", env_json)
    write_setup_sh(agent_dir)

    fields: dict = {"selected": selected, "deviations": env_json["deviations"]}
    ok = selected.get("node") is not None and selected.get("pnpm") is not None

    if not args.skip_checks:
        # Isolated copy: the pristine case source is never installed into.
        work = Path(args.workdir).resolve() if args.workdir else case_dir.parent / "env-check"
        if work.exists():
            shutil.rmtree(work)
        shutil.copytree(source_dir, work / "source", symlinks=True)
        results = run_setup_and_checks(work / "source", work / "logs", case["task"]["checks"], path_prefix=_path_prefix(args))
        fields["isolated_execution"] = {
            "setup_exit_code": results["setup"]["exit_code"],
            "setup_ref": results["setup"],
            "checks": [
                {k: c[k] for k in ("name", "command", "required", "exit_code", "error")}
                | {"stdout_ref": c["stdout_ref"], "stderr_ref": c["stderr_ref"]}
                for c in results["checks"]
            ],
            "log_dir": str(work / "logs"),
            "note": "existing-check success alone does not establish task completion",
        }
        ok = ok and results["setup"]["exit_code"] == 0 and all(
            c["exit_code"] == 0 for c in results["checks"] if c["required"]
        )
        fields["environment_ready"] = ok
    else:
        fields["isolated_execution"] = "skipped (--skip-checks); environment not verified"
        fields["environment_ready"] = False
        ok = False

    write_record(records_dir, "environment", "passed" if ok else "failed", **fields)
    print(json.dumps({"status": "passed" if ok else "failed", "selected": selected}, indent=2))
    return 0 if ok else 1


def cmd_check_inputs(args: argparse.Namespace) -> int:
    case_dir = Path(args.case).resolve()
    records_dir = Path(args.records).resolve()
    case = load_json(case_dir / "case.json")
    forbidden = [str(Path(args.evidence).resolve())] if args.evidence else []
    patch = Path(args.patch).resolve() if args.patch else None
    res = check_input_package(
        case_dir / "agent",
        expected_hashes=case.get("input_hashes"),
        forbidden_path_strings=forbidden,
        pinned_sha=case["identity_core"]["base_revision"],
        patch_file=patch,
    )
    fields = {"checks": res.checks, "diagnostics": res.diagnostics, "supplementary": res.supplementary}
    write_record(records_dir, "leakage", "passed" if res.ok else "failed", **fields)
    print(json.dumps({"status": "passed" if res.ok else "failed", "diagnostics": res.diagnostics}, indent=2))
    return 0 if res.ok else 1


def cmd_evaluate(args: argparse.Namespace) -> int:
    target = Path(args.target).resolve()
    records_dir = Path(args.records).resolve()
    res = evaluate_behavior(target, timeout=args.timeout)
    fields = {
        "target": target.name,
        "verdict": res.verdict,
        "strategy_used": res.strategy_used,
        "checks": res.checks,
        "diagnostics": res.diagnostics,
    }
    stage = args.stage or "evaluation"
    passed = res.verdict == "satisfied" or (
        args.expect == "unsatisfied" and res.verdict == "unsatisfied"
    )
    write_record(records_dir, stage, "passed" if passed else "failed", **fields)
    print(json.dumps({"status": "passed" if passed else "failed", "verdict": res.verdict, "strategy": res.strategy_used}, indent=2))
    return 0 if passed else 1


def cmd_controls(args: argparse.Namespace) -> int:
    """Negative (pristine) and positive (patched) controls, evaluator-side."""
    case_dir = Path(args.case).resolve()
    records_dir = Path(args.records).resolve()
    patch = Path(args.patch).resolve()
    if not patch.is_file():
        return _fail(f"positive-control patch absent: {patch}")

    prep = case_dir.parent / "controls"
    if prep.exists():
        shutil.rmtree(prep)
    # Prepared pristine tree: copy source and install deps (control setup
    # evidence is recorded separately from agent artifacts).
    pristine = prep / "pristine"
    shutil.copytree(case_dir / "agent" / "source", pristine, symlinks=True)
    import subprocess as sp
    ctl_env = {**os.environ, "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0"}
    if _path_prefix(args):
        ctl_env["PATH"] = f"{_path_prefix(args)}:{ctl_env.get('PATH', '')}"
    setup = sp.run(
        ["corepack", "pnpm", "install", "--frozen-lockfile", "--ignore-scripts"],
        cwd=pristine, capture_output=True, text=True, timeout=900,
        env=ctl_env,
    )
    fields: dict = {"pristine_setup_exit": setup.returncode}
    ok = True
    if setup.returncode != 0:
        fields["control_setup"] = "failed: pristine install failed — controls inconclusive"
        write_record(records_dir, "controls", "failed", **fields)
        print(json.dumps({"status": "failed", **fields}, indent=2))
        return 1

    neg = evaluate_behavior(pristine, timeout=args.timeout)
    fields["negative_control"] = {
        "verdict": neg.verdict, "strategy": neg.strategy_used, "checks": neg.checks,
    }
    ok = ok and neg.verdict != "satisfied"  # unsolved state must NOT satisfy

    patched = prep / "patched"
    err = apply_patch_to_copy(pristine, patch, patched)
    if err:
        fields["positive_control"] = {"verdict": f"inconclusive:patch apply failed: {err}"}
        ok = False
    else:
        pos = evaluate_behavior(patched, timeout=args.timeout)
        fields["positive_control"] = {
            "verdict": pos.verdict, "strategy": pos.strategy_used, "checks": pos.checks,
            "note": "original patch used as positive control; evaluator-side only, never published",
        }
        ok = ok and pos.verdict == "satisfied"
    fields["distinguishes_states"] = ok
    write_record(records_dir, "controls", "passed" if ok else "failed", **fields)
    print(json.dumps({"status": "passed" if ok else "failed",
                      "negative": fields["negative_control"]["verdict"],
                      "positive": fields["positive_control"]["verdict"]}, indent=2))
    return 0 if ok else 1


def cmd_run(args: argparse.Namespace) -> int:
    case_dir = Path(args.case).resolve()
    records_dir = Path(args.records).resolve()
    out_dir = Path(args.out).resolve()
    case = load_json(case_dir / "case.json")

    exec_path = record_path(records_dir, "execution")
    if exec_path.exists() and not args.force:
        return _fail("execution record exists — the single experiment run is already consumed; pass --force to relaunch deliberately")

    pf = preflight(records_dir)
    if not pf.ok:
        rec = build_execution_record(
            harness=args.harness, model="not-launched",
            task_sha256=sha256_file(case_dir / "agent" / "task.md"),
            pinned_sha=case["identity_core"]["base_revision"],
            setup={}, boundary_problems=[], launch={"termination_reason": "preflight_refused"},
            patch=None, timeout_s=args.timeout, stripped_env=[], env_keys=[],
        )
        rec["preflight"] = {"missing": pf.missing, "invalid": pf.invalid}
        write_json_atomic(exec_path, rec)
        return _fail(f"preflight refused: missing={pf.missing} invalid={pf.invalid}")

    workspace = out_dir / "workspace"
    logs = out_dir / "logs"
    setup = prepare_workspace(case_dir / "agent", workspace, logs, path_prefix=_path_prefix(args))
    if setup["exit_code"] != 0:
        rec = build_execution_record(
            harness=args.harness, model="not-launched",
            task_sha256=sha256_file(case_dir / "agent" / "task.md"),
            pinned_sha=case["identity_core"]["base_revision"],
            setup=setup, boundary_problems=[],
            launch={"termination_reason": "setup_failure"}, patch=None,
            timeout_s=args.timeout, stripped_env=[], env_keys=[],
        )
        write_json_atomic(exec_path, rec)
        return _fail("dependency setup failed — operational failure, not an agent verdict")

    problems = workspace_boundary_ok(workspace, case["identity_core"]["base_revision"])
    if problems:
        rec = build_execution_record(
            harness=args.harness, model="not-launched",
            task_sha256=sha256_file(case_dir / "agent" / "task.md"),
            pinned_sha=case["identity_core"]["base_revision"],
            setup=setup, boundary_problems=problems,
            launch={"termination_reason": "boundary_refused"}, patch=None,
            timeout_s=args.timeout, stripped_env=[], env_keys=[],
        )
        write_json_atomic(exec_path, rec)
        return _fail(f"boundary check failed: {problems}")

    task_text = (case_dir / "agent" / "task.md").read_text(encoding="utf-8")
    if args.harness == "opencode":
        from .run import OPENCODE_CONFIG
        write_json_atomic(workspace / "source" / "opencode.json", OPENCODE_CONFIG)
    model = args.model or ("opencode-go/deepseek-v4.1-flash" if args.harness == "opencode" else "harness-default")
    launch = launch_agent(args.harness, workspace, task_text, args.timeout, logs, path_prefix=_path_prefix(args))
    patch_info = None
    if launch.get("termination_reason") == "completed":
        patch_info = collect_patch(workspace / "source", out_dir / "artifacts")

    env_keys = [k for k in os.environ if k not in STRIPPED_ENV]
    rec = build_execution_record(
        harness=args.harness, model=model,
        task_sha256=sha256_file(case_dir / "agent" / "task.md"),
        pinned_sha=case["identity_core"]["base_revision"],
        setup=setup, boundary_problems=problems, launch=launch,
        patch=patch_info, timeout_s=args.timeout,
        stripped_env=STRIPPED_ENV, env_keys=env_keys,
    )
    write_json_atomic(exec_path, rec)
    print(json.dumps({"status": rec["status"], "termination": launch.get("termination_reason"),
                      "workspace": str(workspace)}, indent=2))
    return 0 if rec["status"] == "passed" else 1


def cmd_score(args: argparse.Namespace) -> int:
    records_dir = Path(args.records).resolve()
    workspace = Path(args.workspace).resolve()
    case = load_json(Path(args.case).resolve() / "case.json")

    exe = require_record(records_dir, "execution")
    if exe.state is not RecordState.VALID:
        fields = {"verdict": "operational_failure", "reason": f"execution record {exe.state.value}: {'; '.join(exe.diagnostics)[:200]}"}
        write_record(records_dir, "evaluation", "failed", **fields)
        return _fail(fields["reason"])

    source = workspace / "source"
    behavior = evaluate_behavior(source, timeout=args.timeout)
    fields: dict = {"behavior": {"verdict": behavior.verdict, "strategy": behavior.strategy_used, "checks": behavior.checks}}

    if args.run_declared_checks:
        import subprocess as sp
        log_dir = Path(args.log_dir).resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        check_outcomes = []
        score_env = dict(os.environ)
        if _path_prefix(args):
            score_env["PATH"] = f"{_path_prefix(args)}:{score_env.get('PATH', '')}"
        for chk in case["task"]["checks"]:
            try:
                proc = sp.run(["sh", "-c", chk["command"]], cwd=source,
                              capture_output=True, text=True, timeout=int(chk.get("timeout_seconds", 600)),
                              env=score_env)
                (log_dir / f"{chk['name']}.stdout.txt").write_text(proc.stdout[-20000:])
                (log_dir / f"{chk['name']}.stderr.txt").write_text(proc.stderr[-20000:])
                check_outcomes.append({"name": chk["name"], "exit_code": proc.returncode,
                                       "stdout_ref": {"file": f"{chk['name']}.stdout.txt"},
                                       "stderr_ref": {"file": f"{chk['name']}.stderr.txt"}})
            except sp.TimeoutExpired:
                check_outcomes.append({"name": chk["name"], "exit_code": None, "error": "timeout"})
        fields["declared_checks"] = check_outcomes

    verdict = behavior.verdict
    if verdict == "satisfied":
        classification = "agent_requirement_satisfied"
    elif verdict == "unsatisfied":
        classification = "agent_requirement_failure"
    else:
        classification = "operational_failure"
    fields["verdict"] = verdict
    fields["classification"] = classification
    write_record(records_dir, "evaluation", "passed", **fields)
    print(json.dumps({"status": "passed", "verdict": verdict, "classification": classification}, indent=2))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    records_dir = Path(args.records).resolve()
    text = generate_report(records_dir)
    extra = [str(Path(args.evidence).resolve())] if args.evidence else []
    problems = sanitize_scan(text, extra_forbidden=extra)
    if problems:
        return _fail(f"report failed sanitization: {problems}")
    out = Path(args.out).resolve()
    write_text_atomic(out, text)
    print(json.dumps({"status": "passed", "out": str(out)}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="t2e", description="single-case Trace2Eval PoC tooling (R1)")
    p.add_argument("--version", action="version", version=f"t2e {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="validate evidence and extract case inputs")
    e.add_argument("--evidence", required=True)
    e.add_argument("--run", required=True)
    e.add_argument("--task-record")
    e.add_argument("--readiness")
    e.add_argument("--case", required=True)
    e.add_argument("--records", required=True)
    e.set_defaults(fn=cmd_extract)

    m = sub.add_parser("materialize", help="materialize the pinned commit into case/agent/source")
    m.add_argument("--repo", required=True, help="local path (preferred, read-only) or URL")
    m.add_argument("--sha")
    m.add_argument("--case", required=True)
    m.add_argument("--records", required=True)
    m.set_defaults(fn=cmd_materialize)

    env = sub.add_parser("environment", help="environment.json + setup.sh + isolated check execution")
    env.add_argument("--case", required=True)
    env.add_argument("--records", required=True)
    env.add_argument("--workdir", help="isolated execution dir (default <case>/../env-check)")
    env.add_argument("--node-bin", help="toolchain bin dir prepended to PATH (default: $T2E_NODE_BIN)")
    env.add_argument("--skip-checks", action="store_true")
    env.set_defaults(fn=cmd_environment)

    ci = sub.add_parser("check-inputs", help="agent package leakage/provenance checks")
    ci.add_argument("--case", required=True)
    ci.add_argument("--records", required=True)
    ci.add_argument("--evidence", help="private evidence dir (its path is forbidden in inputs)")
    ci.add_argument("--patch", help="positive-control patch (supplementary overlap only)")
    ci.set_defaults(fn=cmd_check_inputs)

    ev = sub.add_parser("evaluate", help="behavior checks against a target tree")
    ev.add_argument("--target", required=True, help="materialized tree with deps installed")
    ev.add_argument("--records", required=True)
    ev.add_argument("--stage", help="record stage name (default: evaluation)")
    ev.add_argument("--expect", choices=["satisfied", "unsatisfied"], help="required verdict for record to pass")
    ev.add_argument("--timeout", type=int, default=120)
    ev.set_defaults(fn=cmd_evaluate)

    co = sub.add_parser("controls", help="negative/positive control evaluation")
    co.add_argument("--case", required=True)
    co.add_argument("--records", required=True)
    co.add_argument("--patch", required=True)
    co.add_argument("--node-bin")
    co.add_argument("--timeout", type=int, default=120)
    co.set_defaults(fn=cmd_controls)

    r = sub.add_parser("run", help="one independent agent execution")
    r.add_argument("--case", required=True)
    r.add_argument("--records", required=True)
    r.add_argument("--out", required=True, help="run output dir (workspace/, logs/, artifacts/)")
    r.add_argument("--harness", choices=["claude", "opencode"], default="claude")
    r.add_argument("--model")
    r.add_argument("--timeout", type=int, default=2400)
    r.add_argument("--node-bin")
    r.add_argument("--force", action="store_true", help="relaunch deliberately (consumes another run)")
    r.set_defaults(fn=cmd_run)

    s = sub.add_parser("score", help="score the produced workspace")
    s.add_argument("--workspace", required=True)
    s.add_argument("--case", required=True)
    s.add_argument("--records", required=True)
    s.add_argument("--log-dir", required=True)
    s.add_argument("--run-declared-checks", action="store_true")
    s.add_argument("--node-bin")
    s.add_argument("--timeout", type=int, default=120)
    s.set_defaults(fn=cmd_score)

    rep = sub.add_parser("report", help="sanitized report markdown")
    rep.add_argument("--records", required=True)
    rep.add_argument("--out", required=True)
    rep.add_argument("--evidence", help="private evidence dir (path forbidden in report)")
    rep.set_defaults(fn=cmd_report)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        return _fail("interrupted", 130)


if __name__ == "__main__":
    sys.exit(main())
