"""Historical v2 task-prompt template, ported from coding-worker
src/dsh/prompts.ts @28a21f6ffc7de906ceeda8100fbd6a2ae1239bac
(PROMPT_VERSION = "v2", single-issue shape).

The port reproduces the template byte-for-byte — including its line-join
semantics (`lines.join("\\n")`, no trailing newline) — because the
evidence check compares the regenerated prompt against the recorded
initial-prompt event.
"""

from __future__ import annotations

from typing import Any


def build_prompt_v2(job: dict[str, Any]) -> str:
    issue = job["issue"]
    checks = job.get("checks") or []
    constraints_job = job.get("constraints") or {}

    lines: list[str] = [
        "You are an autonomous coding worker. Implement the issue below in this repository.",
        "",
        "## Issue",
        f"Title: {issue['title']}",
        "",
        issue["body"],
    ]
    ac = issue.get("acceptance_criteria") or []
    if len(ac) > 0:
        lines += ["", "## Acceptance criteria"] + [f"- {c}" for c in ac]

    constraints: list[str] = []
    allowed = constraints_job.get("allowed_paths")
    if allowed:
        constraints.append(f"Only modify files under: {', '.join(allowed)}")
    forbidden = constraints_job.get("forbidden_paths")
    if forbidden:
        constraints.append(f"Never modify or create: {', '.join(forbidden)}")
    commands = constraints_job.get("commands") or {}
    if commands.get("declared_only") is True:
        extra = [a for a in (commands.get("allow") or []) if str(a).strip() != ""]
        constraints.append(
            "Run only the declared checks through shell commands."
            + (f" Also allowed: {', '.join(extra)}." if extra else "")
            + " Any other shell command is recorded as an execution policy violation."
        )
    notes = constraints_job.get("notes")
    if notes:
        constraints.append(notes)
    if constraints:
        lines += ["", "## Constraints"] + [f"- {c}" for c in constraints]

    if len(checks) > 0:
        lines += ["", "## Checks to satisfy"]
        for chk in checks:
            tag = "[required]" if chk.get("required") else "[optional]"
            lines.append(f"- {tag} {chk['name']}: `{chk['command']}`")

    lines += [
        "",
        "## Instructions",
        "- Work directly in the checked-out repository; make the smallest change that satisfies the criteria.",
        "- Run the required checks yourself and fix failures before finishing.",
        "- Do not commit, push, or create pull requests.",
        "- End with a concise summary of what you changed and the check results.",
        "- Report only what you actually ran: machine-recorded events are compared against your answer. Finish with a `MODEL: <provider>/<id>` line naming the model that produced the answer.",
    ]
    return "\n".join(lines)
