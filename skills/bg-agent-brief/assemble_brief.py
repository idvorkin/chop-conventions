#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "typer>=0.12",
# ]
# ///
"""Assemble a background-agent dispatch brief (or a SendMessage follow-up).

The dispatch-prompt skeleton a main session hand-writes for every
substantive background agent is the single highest-volume reinvention
measured across real sessions (~178 KB of near-identical prompt prose in
one 27-hour session across 49 dispatches, 43 sharing the same skeleton).
This helper turns that retyping into a form-fill: pass a few flags, get a
structured brief on stdout to paste into the Agent tool's `prompt`.

The skeleton, in order (measured element frequencies out of 49 dispatches):

  1. One-sentence role + single deliverable.
  2. READ FIRST, in order (26/49) — CLAUDE.md -> README -> design bead.
  3. Constraints / workspace rules (26/49) — worktree/cwd/edit fences.
  4. The work — numbered, user's verbatim words, gotchas inline.
  5. VERIFY BEFORE YOU REPORT DONE (28/49) — concrete checks + clean up
     test artifacts (only 5/49 had this; it bit twice — default it ON).
  6. Repo push rules — Bitbucket vs GitHub, fork-vs-direct, never main.
  7. Report contract (43/49) — exactly what to return.

`followup` emits the SendMessage course-correction form used 9 times in
one session (ADDITIONAL REQUIREMENT / SCOPE REDUCTION / GOVERNING DESIGN
PRINCIPLE, verbatim).

Pure assembly (`assemble_brief`, `assemble_followup`, `push_rules_block`)
is importable without `typer` — the CLI is wired in `_build_app()` behind
the `__main__` guard, per chop-conventions Python rules. Unit-tested in
test_assemble_brief.py with no subprocess mocking.

Usage:
    assemble_brief.py brief --role "..." --read-first CLAUDE.md \
        --work "..." --verify "curl -sf localhost:8778 -> 200" \
        --push github-fork --repo-slug idvorkin/chop-conventions \
        --report "PR URL" --report "screenshot path"
    assemble_brief.py followup --kind scope-reduction --verbatim "..."
"""

import sys
from typing import Any


# ---------- Pure functions (unit-tested, no deps) ----------


PUSH_TARGETS = ("github-fork", "github-direct", "bitbucket", "none")

_KIND_HEADERS = {
    "additional-requirement": "ADDITIONAL REQUIREMENT",
    "scope-reduction": "SCOPE REDUCTION",
    "governing-principle": "GOVERNING DESIGN PRINCIPLE",
}

# The default test-artifact cleanup line. Only 5/49 dispatches included a
# cleanup instruction and its absence bit twice — a scratch bead / marker /
# seeded log line left in real data is the igor2-88g.114 defect class. So
# it is ON by default here.
CLEANUP_LINE = (
    "- Clean up EVERY test artifact you created — scratch beads, marker "
    "rows, seeded log lines, temp files, throwaway PRs/branches. A test "
    "bead, marker, or log line left behind in real data is a real defect "
    "(the igor2-88g.114 class). Leaving test state behind is a failure, "
    "not a nit."
)


def _numbered(items: list[str]) -> str:
    """Render items as a 1-based numbered list."""
    return "\n".join(f"{i}. {item}" for i, item in enumerate(items, start=1))


def _bulleted(items: list[str]) -> str:
    """Render items as a `- ` bullet list."""
    return "\n".join(f"- {item}" for item in items)


def push_rules_block(
    push: str,
    repo_slug: str | None = None,
    default_branch: str = "main",
    branch: str | None = None,
) -> str:
    """Return the '# Repo push rules' section for the chosen push target.

    Raises ValueError on an unknown push target so a typo fails loudly
    rather than emitting a silently-empty push section.
    """
    if push not in PUSH_TARGETS:
        raise ValueError(
            f"unknown push target {push!r}; choose one of {PUSH_TARGETS}"
        )

    common = (
        "- NEVER push to main/master. NEVER `git push --force`. NEVER "
        "`gh pr merge` — the human owns every merge."
    )
    slug = repo_slug or "<owner>/<repo>"
    br = branch or "<branch>"

    if push == "none":
        return (
            "# Repo push rules\n"
            "- No push or PR for this task — return results in your final "
            "report only."
        )
    if push == "bitbucket":
        return (
            "# Repo push rules — BITBUCKET origin\n"
            f"- `gh pr create` does NOT work here: origin is Bitbucket, not "
            f"GitHub. Push the feature branch to origin, then open the PR in "
            f"a browser:\n"
            f"  https://bitbucket.org/{slug}/pull-requests/new?source={br}"
            f"&dest={default_branch}\n"
            "- Print that PR-create URL in your final report (the dispatcher "
            "cannot run `gh` against this repo).\n"
            f"{common}"
        )
    if push == "github-direct":
        return (
            "# Repo push rules\n"
            "- Push the feature branch to `origin`, then open the PR with "
            "`gh pr create` (no `--repo` flag — origin is canonical).\n"
            f"{common}"
        )
    # github-fork
    return (
        "# Repo push rules — fork workflow\n"
        "- You are authenticated as a fork account. Push the feature branch "
        "to `origin` (the fork), NEVER to the canonical remote.\n"
        f"- Open the PR against canonical with: `gh pr create --repo {slug}`\n"
        f"{common}"
    )


def assemble_brief(
    *,
    role: str,
    worktree: str | None = None,
    read_first: list[str] | None = None,
    constraints: list[str] | None = None,
    work: list[str] | None = None,
    verify: list[str] | None = None,
    cleanup: bool = True,
    push: str = "github-fork",
    repo_slug: str | None = None,
    default_branch: str = "main",
    branch: str | None = None,
    report: list[str] | None = None,
) -> str:
    """Assemble the full background-agent brief as a single string.

    Only `role` is required. Optional sections (READ FIRST, constraints,
    the work) are omitted entirely when empty rather than left as stub
    headers. VERIFY, push rules, and the report contract always render —
    VERIFY because it carries the default test-artifact cleanup line, and
    the report contract because a dispatch with no return spec is a
    guaranteed under-report.
    """
    read_first = read_first or []
    constraints = constraints or []
    work = work or []
    verify = verify or []
    report = report or []

    parts: list[str] = []

    # 1. One-sentence role + deliverable.
    header = (
        f"You are a background agent. {role.strip()}\n\n"
        "Work autonomously through to the deliverable — you own the full "
        "lifecycle. The originating session cannot see your work, so "
        "everything it needs must come back in your final report."
    )
    if worktree:
        header += (
            f"\n\nYour FIRST action: `cd {worktree}`. Do all work there. "
            "Note: cwd does NOT persist between Bash calls in a subagent — "
            "pass `-C <path>` to git or re-`cd` at the top of each Bash "
            "call. A worktree shares its parent's `.git/`, so hooks, "
            "config, and branches are shared with concurrent agents."
        )
    parts.append(header)

    # 2. READ FIRST, in order.
    if read_first:
        parts.append(
            "# READ FIRST, in order\n"
            "Read these before touching anything — later entries assume the "
            "context of earlier ones:\n" + _numbered(read_first)
        )

    # 3. Constraints / workspace rules.
    if constraints:
        parts.append("# Constraints\n" + _bulleted(constraints))

    # 4. The work.
    if work:
        parts.append("# The work\n" + _numbered(work))

    # 5. VERIFY BEFORE YOU REPORT DONE (+ cleanup default).
    verify_lines = list(verify)
    verify_body = (
        "Do NOT report done until each of these passes — run them, do not "
        "assume:\n" + _bulleted(verify_lines)
        if verify_lines
        else "Exercise the real surface you changed — run the tests, curl "
        "the endpoint, screenshot the viewport. Do NOT report done on "
        "assumption."
    )
    if cleanup:
        verify_body += "\n" + CLEANUP_LINE
    parts.append("# VERIFY BEFORE YOU REPORT DONE\n" + verify_body)

    # 6. Repo push rules.
    parts.append(push_rules_block(push, repo_slug, default_branch, branch))

    # 7. Report contract.
    if report:
        report_body = (
            "Your final message MUST return exactly these, and nothing "
            "else (no preamble, no sign-off):\n" + _bulleted(report)
        )
    else:
        report_body = (
            "Your final message MUST return, and nothing else:\n"
            "- The PR URL on its own line\n"
            "- A 3-5 bullet summary of what changed and why\n"
            "- The specific confirmations the dispatcher needs to trust the "
            "work (paths, checks that passed)"
        )
    parts.append("# Report contract\n" + report_body)

    return "\n\n".join(parts) + "\n"


def assemble_followup(kind: str, verbatim: str) -> str:
    """Assemble a SendMessage course-correction for a running agent.

    Used to inject a mid-flight correction into an in-flight background
    agent (via SendMessage / the agent's task id) without restating the
    whole brief. Raises ValueError on an unknown kind.
    """
    if kind not in _KIND_HEADERS:
        raise ValueError(
            f"unknown kind {kind!r}; choose one of {tuple(_KIND_HEADERS)}"
        )
    label = _KIND_HEADERS[kind]
    return (
        f"{label} from Igor, verbatim: '{verbatim.strip()}'\n\n"
        "Fold this into the work already in flight — do not restart. If it "
        "conflicts with what you have done so far, this instruction wins; "
        "adjust and note the change in your final report."
    )


# ---------- CLI (typer lazy-imported) ----------


def _build_app() -> Any:
    """Wire up the Typer app. Imported only when run as a script so tests
    and module-importers need no `typer` on their path."""
    import typer

    app = typer.Typer(
        add_completion=False,
        no_args_is_help=True,
        help="Assemble a background-agent dispatch brief or a SendMessage "
        "follow-up. Fill the form instead of retyping the skeleton.",
    )

    @app.command()
    def brief(  # pyright: ignore[reportUnusedFunction]
        role: str = typer.Option(
            ..., "--role", help="One sentence: what this agent is + its deliverable."
        ),
        worktree: str = typer.Option(
            "", "--worktree", help="Absolute worktree path to cd into (optional)."
        ),
        read_first: list[str] = typer.Option(
            [], "--read-first", help="Ordered read-first path (repeatable)."
        ),
        constraint: list[str] = typer.Option(
            [], "--constraint", help="Workspace/constraint rule (repeatable)."
        ),
        work: list[str] = typer.Option(
            [], "--work", help="Work item, user's verbatim words (repeatable)."
        ),
        verify: list[str] = typer.Option(
            [], "--verify", help="Concrete verification check (repeatable)."
        ),
        cleanup: bool = typer.Option(
            True,
            "--cleanup/--no-cleanup",
            help="Inject the test-artifact cleanup line (default ON).",
        ),
        push: str = typer.Option(
            "github-fork",
            "--push",
            help=f"Push target: one of {PUSH_TARGETS}.",
        ),
        repo_slug: str = typer.Option(
            "", "--repo-slug", help="owner/repo for the PR-create line."
        ),
        default_branch: str = typer.Option(
            "main", "--default-branch", help="PR base branch (default main)."
        ),
        branch: str = typer.Option(
            "", "--branch", help="Feature branch name (for the Bitbucket URL)."
        ),
        report: list[str] = typer.Option(
            [], "--report", help="Report-contract item to return (repeatable)."
        ),
    ) -> None:
        """Emit the assembled dispatch brief to stdout."""
        sys.stdout.write(
            assemble_brief(
                role=role,
                worktree=worktree or None,
                read_first=list(read_first),
                constraints=list(constraint),
                work=list(work),
                verify=list(verify),
                cleanup=cleanup,
                push=push,
                repo_slug=repo_slug or None,
                default_branch=default_branch,
                branch=branch or None,
                report=list(report),
            )
        )

    @app.command()
    def followup(  # pyright: ignore[reportUnusedFunction]
        kind: str = typer.Option(
            ...,
            "--kind",
            help="One of: additional-requirement, scope-reduction, "
            "governing-principle.",
        ),
        verbatim: str = typer.Option(
            ..., "--verbatim", help="Igor's words, verbatim."
        ),
    ) -> None:
        """Emit a SendMessage course-correction to stdout."""
        sys.stdout.write(assemble_followup(kind, verbatim) + "\n")

    return app


if __name__ == "__main__":
    _build_app()()
