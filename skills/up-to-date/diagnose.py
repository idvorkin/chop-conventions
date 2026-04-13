#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Diagnose git repo state for the up-to-date skill.

Runs git + gh in parallel and emits a single JSON blob describing:
    remotes (with hygiene issues), branch state, worktree state, PR state.

The skill reads the JSON and decides what action to take — this script
does NOT mutate anything.

Usage:
    ./diagnose.py           # prints JSON to stdout
    ./diagnose.py --pretty  # pretty-printed JSON

Tested as a library via test_diagnose.py (pure functions importable).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any

FORK_ORGS = ["idvorkin-ai-tools"]


# ---------- Data types ----------


@dataclass(frozen=True)
class Remote:
    name: str
    url: str


@dataclass
class RemoteIssue:
    kind: str  # non_standard_name | swapped_remotes | fork_without_canonical
    detail: str
    fix: str


@dataclass
class RemoteAnalysis:
    entries: list[Remote]
    source: str  # "upstream" or "origin"
    is_fork_workflow: bool
    issues: list[RemoteIssue] = field(default_factory=list)


@dataclass(frozen=True)
class CherryAnalysis:
    unique_commits: list[str]
    equivalent_commits: list[str]


# ---------- Pure functions (tested) ----------


def parse_remotes(raw: str) -> list[Remote]:
    """Parse `git remote -v` output into a deduped list of Remotes.

    Each remote appears twice (fetch + push); we keep one entry per name.
    """
    seen: dict[str, Remote] = {}
    for line in raw.strip().splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name, url = parts[0], parts[1]
        seen.setdefault(name, Remote(name=name, url=url))
    return list(seen.values())


def is_fork_url(url: str, fork_orgs: list[str]) -> bool:
    """True if a git URL's owner segment matches a known fork org.

    Matches both SSH (`git@github.com:org/repo`) and HTTPS
    (`https://github.com/org/repo`) URL forms, anchored on the org segment
    so `idvorkin` does not false-match against `idvorkin-ai-tools`.
    """
    for org in fork_orgs:
        # SSH: git@host:org/...   HTTPS: https://host/org/...
        pattern = rf"(?::|/){re.escape(org)}(?:/|$)"
        if re.search(pattern, url):
            return True
    return False


def classify_remotes(remotes: list[Remote], fork_orgs: list[str]) -> RemoteAnalysis:
    """Determine source of truth, fork-workflow status, and hygiene issues."""
    issues: list[RemoteIssue] = []
    by_name = {r.name: r for r in remotes}

    # Source of truth: prefer upstream, else origin, else first remote.
    if "upstream" in by_name:
        source = "upstream"
    elif "origin" in by_name:
        source = "origin"
    elif remotes:
        source = remotes[0].name
    else:
        source = "origin"

    # Check 1: non-standard remote names
    for r in remotes:
        if r.name not in ("origin", "upstream"):
            issues.append(
                RemoteIssue(
                    kind="non_standard_name",
                    detail=f"remote '{r.name}' should be named 'origin' or 'upstream'",
                    fix=f"git remote rename {r.name} <origin|upstream>",
                )
            )

    fork_remotes = [r for r in remotes if is_fork_url(r.url, fork_orgs)]
    canonical_remotes = [r for r in remotes if not is_fork_url(r.url, fork_orgs)]
    is_fork_workflow = bool(fork_remotes and canonical_remotes)

    # Check 2: swapped remotes — origin pointing at canonical while fork also exists
    if fork_remotes and canonical_remotes:
        origin = by_name.get("origin")
        upstream = by_name.get("upstream")
        origin_is_canonical = origin is not None and not is_fork_url(
            origin.url, fork_orgs
        )
        upstream_is_fork = upstream is not None and is_fork_url(upstream.url, fork_orgs)
        if origin_is_canonical or upstream_is_fork:
            issues.append(
                RemoteIssue(
                    kind="swapped_remotes",
                    detail="origin should point to your fork; upstream should point to canonical",
                    fix=(
                        "git remote rename origin upstream && "
                        "git remote rename <fork> origin && "
                        "git branch --set-upstream-to=upstream/main main"
                    ),
                )
            )

    # Check 3: lone fork — fork remote exists but no canonical to PR against
    if fork_remotes and not canonical_remotes:
        fork = fork_remotes[0]
        issues.append(
            RemoteIssue(
                kind="fork_without_canonical",
                detail=f"'{fork.name}' is a fork but no canonical 'upstream' remote exists",
                fix="git remote add upstream <canonical-repo-url>",
            )
        )

    return RemoteAnalysis(
        entries=remotes,
        source=source,
        is_fork_workflow=is_fork_workflow,
        issues=issues,
    )


def parse_cherry_status(raw: str) -> CherryAnalysis:
    """Split `git cherry -v` output into unique and patch-equivalent commits."""
    unique_commits: list[str] = []
    equivalent_commits: list[str] = []
    for line in raw.splitlines():
        if line.startswith("+ "):
            unique_commits.append(line[2:])
        elif line.startswith("- "):
            equivalent_commits.append(line[2:])
    return CherryAnalysis(
        unique_commits=unique_commits,
        equivalent_commits=equivalent_commits,
    )


def parse_left_right_count(raw: str) -> tuple[int, int] | None:
    """Parse `git rev-list --left-right --count A...B` output."""
    parts = raw.split()
    if len(parts) != 2:
        return None
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return None


# ---------- Subprocess helpers ----------


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Thin wrapper around subprocess.run capturing text output."""
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def git(*args: str, check: bool = True) -> str:
    """Run `git <args>` and return stdout (stripped)."""
    result = _run(["git", *args], check=check)
    return result.stdout.strip()


def git_proc(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run `git <args>` and return the full CompletedProcess."""
    return _run(["git", *args], check=check)


def gh_pr_view_json(fields: str) -> dict[str, Any] | None:
    """Run `gh pr view --json <fields>` and return parsed dict, or None if no PR."""
    proc = _run(["gh", "pr", "view", "--json", fields], check=False)
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


# ---------- Orchestrator ----------


def run_diagnose() -> dict[str, Any]:
    """Collect full diagnosis as a JSON-serializable dict."""
    errors: list[str] = []

    # Remote hygiene — needs to happen before fetch so we know the source name.
    remotes_raw = git("remote", "-v", check=False)
    remotes = parse_remotes(remotes_raw)
    analysis = classify_remotes(remotes, FORK_ORGS)
    src = analysis.source

    # Run fetch and PR lookup in parallel — they don't depend on each other.
    # gh pr view reads local branch state, not the remote fetch result.
    with ThreadPoolExecutor(max_workers=2) as pool:
        fetch_fut = pool.submit(_run, ["git", "fetch", "--all", "--prune"], False)
        pr_fut = pool.submit(
            gh_pr_view_json,
            "state,number,title,mergeable,reviewDecision,reviews,comments",
        )
        fetch_proc = fetch_fut.result()
        pr_data = pr_fut.result()

    if fetch_proc.returncode != 0:
        errors.append(f"git fetch failed: {fetch_proc.stderr.strip()}")

    # Post-fetch git queries are independent; run them in parallel.
    with ThreadPoolExecutor(max_workers=5) as pool:
        branch_name_fut = pool.submit(git_proc, "branch", "--show-current", check=False)
        divergence_fut = pool.submit(
            git_proc,
            "rev-list",
            "--left-right",
            "--count",
            f"{src}/main...HEAD",
            check=False,
        )
        behind_commits_fut = pool.submit(
            git_proc, "log", "--oneline", f"HEAD..{src}/main", check=False
        )
        uncommitted_fut = pool.submit(git_proc, "status", "--porcelain", check=False)
        stashes_fut = pool.submit(git_proc, "stash", "list", check=False)
        branch_name_proc = branch_name_fut.result()
        divergence_proc = divergence_fut.result()
        behind_commits_proc = behind_commits_fut.result()
        uncommitted_proc = uncommitted_fut.result()
        stash_proc = stashes_fut.result()

    if branch_name_proc.returncode != 0:
        errors.append(
            f"git branch --show-current failed: {branch_name_proc.stderr.strip()}"
        )
    branch_name = branch_name_proc.stdout.strip()

    behind = 0
    ahead = 0
    if divergence_proc.returncode != 0:
        errors.append(
            "git rev-list --left-right --count failed: "
            f"{divergence_proc.stderr.strip()}"
        )
    else:
        divergence = parse_left_right_count(divergence_proc.stdout.strip())
        if divergence is None:
            errors.append(
                "git rev-list --left-right --count returned unexpected output: "
                f"{divergence_proc.stdout.strip()!r}"
            )
        else:
            behind, ahead = divergence

    if behind_commits_proc.returncode != 0:
        errors.append(
            f"git log HEAD..{src}/main failed: {behind_commits_proc.stderr.strip()}"
        )
    behind_commits_raw = behind_commits_proc.stdout.strip()

    if uncommitted_proc.returncode != 0:
        errors.append(
            f"git status --porcelain failed: {uncommitted_proc.stderr.strip()}"
        )
    uncommitted_raw = uncommitted_proc.stdout.strip()

    if stash_proc.returncode != 0:
        errors.append(f"git stash list failed: {stash_proc.stderr.strip()}")
    stash_raw = stash_proc.stdout.strip()

    is_main = branch_name == "main"
    behind_commits = [ln for ln in behind_commits_raw.splitlines() if ln][:10]

    cherry = CherryAnalysis(unique_commits=[], equivalent_commits=[])
    if branch_name:
        # Use patch equivalence, not commit reachability, so rebased/cherry-picked
        # commits already present upstream do not show up as unique work.
        cherry_proc = git_proc("cherry", "-v", f"{src}/main", branch_name, check=False)
        if cherry_proc.returncode != 0:
            errors.append(
                f"git cherry -v {src}/main {branch_name} failed: {cherry_proc.stderr.strip()}"
            )
        else:
            cherry = parse_cherry_status(cherry_proc.stdout.strip())

    ahead_patch_unique_commits = cherry.unique_commits[:10]
    ahead_patch_equivalent_commits = cherry.equivalent_commits[:10]
    can_force_align = is_main and ahead > 0 and not cherry.unique_commits

    leftover_commits = [] if is_main else ahead_patch_unique_commits

    # Worktree state
    uncommitted = [ln for ln in uncommitted_raw.splitlines() if ln]
    stashes = [ln for ln in stash_raw.splitlines() if ln]

    # PR state — only on feature branches, and only if we got data
    pr_block: dict[str, Any] | None = None
    if not is_main and pr_data:
        reviews = pr_data.get("reviews", []) or []
        comments = pr_data.get("comments", []) or []
        pr_block = {
            "state": pr_data.get("state"),
            "number": pr_data.get("number"),
            "title": pr_data.get("title"),
            "mergeable": pr_data.get("mergeable"),
            "review_decision": pr_data.get("reviewDecision"),
            "recent_reviews": reviews[-3:],
            "recent_comments": comments[-3:],
        }

    return {
        "remotes": {
            "entries": [asdict(r) for r in analysis.entries],
            "source": analysis.source,
            "is_fork_workflow": analysis.is_fork_workflow,
            "issues": [asdict(i) for i in analysis.issues],
        },
        "branch": {
            "name": branch_name,
            "is_main": is_main,
            "behind": behind,
            "ahead": ahead,
            "behind_commits": behind_commits,
            "ahead_patch_unique_commits": ahead_patch_unique_commits,
            "ahead_patch_equivalent_commits": ahead_patch_equivalent_commits,
            "can_force_align": can_force_align,
            "leftover_commits": leftover_commits,
        },
        "worktree": {
            "uncommitted": uncommitted,
            "stashes": stashes,
        },
        "pr": pr_block,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose git repo state for up-to-date skill"
    )
    parser.add_argument("--pretty", action="store_true", help="pretty-print JSON")
    args = parser.parse_args()

    data = run_diagnose()
    indent = 2 if args.pretty else None
    json.dump(data, sys.stdout, indent=indent)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
