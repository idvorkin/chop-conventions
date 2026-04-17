"""Diagnose git repo state for the up-to-date skill.

Runs git + gh in parallel and emits a single JSON blob describing:
    remotes (with hygiene issues), branch state, worktree state, PR state.

The skill reads the JSON and decides what action to take — this function
does NOT mutate anything.

Packaged entry point (`uv tool install ./skills/up-to-date/`):

    up-to-date-diag           # prints JSON to stdout
    up-to-date-diag --pretty  # pretty-printed JSON

Tested as a library via test_diagnose.py (pure functions importable).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

FORK_ORGS = ["idvorkin-ai-tools"]

# Hostname pattern for the dev-VM class (`c-5004`, `C-5004`, etc.).
_DEV_HOSTNAME_RE = re.compile(r"^c-\d+$", re.IGNORECASE)

# Canonical slot names emitted into `shared_claude_md.expected_symlinks`
# and matching action-kind output from `compute_slot_action`.
_SLOTS = ("global", "machine", "dev_machine")


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


@dataclass(frozen=True)
class MachineInfo:
    """Classification result for the machine running `diagnose.py`.

    `machine` is one of `"mac" | "orbstack-dev" | "unknown"`.
    `dev_machine` is True iff the host is served to the user over
    Tailscale (Tailscale present AND hostname matches dev-VM pattern).
    `reasons` is a human-readable evidence list — both for debugging
    and for surfacing in the diagnose output.
    """

    machine: str
    dev_machine: bool
    reasons: list[str]


@dataclass(frozen=True)
class WorktreeRef:
    """One `(path, branch)` pair parsed from `git worktree list --porcelain`.

    Detached and bare worktrees are omitted at parse time — they have no
    branch to prune, so they're not cleanup candidates. Primary-vs-linked
    status is decided by the caller (primary is always first in the
    porcelain output).
    """

    path: str
    branch: str


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


def parse_worktree_list(raw: str) -> list[WorktreeRef]:
    """Parse `git worktree list --porcelain` output into branch-bearing entries.

    Porcelain format is blank-line-separated blocks like:

        worktree /path/to/repo
        HEAD <sha>
        branch refs/heads/main

        worktree /path/to/repo/.worktrees/feature
        HEAD <sha>
        branch refs/heads/feature

        worktree /path/to/repo/.worktrees/detached
        HEAD <sha>
        detached

    Branchless worktrees (detached HEAD, bare) are skipped — they have no
    branch to prune. Preserves input order so the caller can flag the first
    entry as the primary checkout.

    Path may contain spaces; the parser preserves everything after the
    `worktree ` prefix literally.
    """
    entries: list[WorktreeRef] = []
    current_path: str | None = None
    for line in raw.splitlines():
        if line.startswith("worktree "):
            current_path = line[len("worktree ") :]
        elif line.startswith("branch ") and current_path is not None:
            branch = line[len("branch ") :]
            if branch.startswith("refs/heads/"):
                branch = branch[len("refs/heads/") :]
            entries.append(WorktreeRef(path=current_path, branch=branch))
            current_path = None
        elif line == "" and current_path is not None:
            # Blank line ends a block without a `branch` line — skip.
            current_path = None
    return entries


def parse_left_right_count(raw: str) -> tuple[int, int] | None:
    """Parse `git rev-list --left-right --count A...B` output."""
    parts = raw.split()
    if len(parts) != 2:
        return None
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return None


def parse_symbolic_ref_output(raw: str, src: str) -> str | None:
    """Parse `git symbolic-ref refs/remotes/<src>/HEAD` output.

    Input looks like `refs/remotes/origin/main` or `refs/remotes/upstream/master`.
    Returns the branch name (last segment) or None if the ref doesn't match
    the expected `refs/remotes/<src>/<branch>` shape.
    """
    prefix = f"refs/remotes/{src}/"
    stripped = raw.strip()
    if stripped.startswith(prefix):
        branch = stripped[len(prefix) :]
        return branch or None
    return None


# ---------- Machine detection (pure) ----------


def classify_machine(
    system: str,
    mac_ver_nonempty: bool,
    home_developer_exists: bool,
) -> tuple[str, list[str]]:
    """Classify the machine type from already-evaluated booleans.

    Pure: callers pass in pre-computed signals, so the test suite never
    needs to mock `platform` or `pathlib`. Returns
    `(machine, reasons)` where `machine` is one of
    `"mac" | "orbstack-dev" | "unknown"`.
    """
    reasons: list[str] = []
    if system == "Darwin":
        if mac_ver_nonempty:
            reasons.append("platform.system()==Darwin + mac_ver non-empty")
            return "mac", reasons
        reasons.append(
            "platform.system()==Darwin but mac_ver empty — falling through"
        )
        return "unknown", reasons
    if system == "Linux":
        if home_developer_exists:
            reasons.append("Linux + /home/developer present")
            return "orbstack-dev", reasons
        reasons.append("Linux but /home/developer absent")
        return "unknown", reasons
    reasons.append(f"unrecognized platform.system()={system!r}")
    return "unknown", reasons


def classify_dev_machine(
    tailscale_present: bool,
    hostname: str,
) -> tuple[bool, list[str]]:
    """Classify whether this host is served to the user over Tailscale.

    Both conditions must hold:
    1. Tailscale is installed (binary in PATH or well-known install paths).
    2. The hostname matches `^c-\\d+$` (case-insensitive).

    Returns `(dev_machine, reasons)`.
    """
    reasons: list[str] = []
    if tailscale_present:
        reasons.append("tailscale present")
    else:
        reasons.append("tailscale not found in PATH or well-known paths")
    if _DEV_HOSTNAME_RE.match(hostname):
        reasons.append(f"hostname={hostname} matches ^c-\\d+$")
    else:
        reasons.append(f"hostname={hostname} does not match ^c-\\d+$")
    return (tailscale_present and bool(_DEV_HOSTNAME_RE.match(hostname))), reasons


def _tailscale_present() -> bool:
    """Thin I/O wrapper: True if Tailscale is discoverable on this host.

    Checks PATH first (covers Mac+Homebrew and Linux) and falls back to
    two well-known absolute paths.
    """
    if shutil.which("tailscale") is not None:
        return True
    for candidate in ("/usr/bin/tailscale", "/opt/homebrew/bin/tailscale"):
        if Path(candidate).exists():
            return True
    return False


def detect_machine() -> MachineInfo:
    """Build a MachineInfo by probing `platform`, `pathlib`, `socket`.

    This is the thin I/O wrapper around `classify_machine` and
    `classify_dev_machine`. Each OS probe runs exactly once; the pure
    classifiers receive only booleans and strings. Intentionally not
    unit-tested — the classifiers are.
    """
    system = platform.system()
    mac_ver = platform.mac_ver()[0]
    home_developer_exists = Path("/home/developer").is_dir()
    machine, machine_reasons = classify_machine(
        system=system,
        mac_ver_nonempty=bool(mac_ver),
        home_developer_exists=home_developer_exists,
    )
    hostname = socket.gethostname()
    dev_machine, dev_reasons = classify_dev_machine(
        tailscale_present=_tailscale_present(),
        hostname=hostname,
    )
    return MachineInfo(
        machine=machine,
        dev_machine=dev_machine,
        reasons=machine_reasons + dev_reasons,
    )


# ---------- Shared CLAUDE.md (pure, stat-based) ----------


def resolve_chop_root(env: dict[str, str], home: Path) -> Path | None:
    """Return an absolute Path to a chop-conventions checkout or None.

    Preference order:
    1. `CHOP_CONVENTIONS_ROOT` environment variable.
    2. `<home>/gits/chop-conventions` fallback.

    A candidate is accepted only if it contains `claude-md/global.md`.
    Pure on `(env, home)` with one stat per candidate.
    """
    candidates: list[Path] = []
    env_root = env.get("CHOP_CONVENTIONS_ROOT")
    if env_root:
        candidates.append(Path(env_root))
    candidates.append(home / "gits" / "chop-conventions")
    for candidate in candidates:
        try:
            marker = candidate / "claude-md" / "global.md"
            if marker.is_file():
                return candidate
        except OSError:
            continue
    return None


def _slot_plan(
    chop_root: Path,
    home: Path,
    machine_info: MachineInfo,
    enabled: bool,
) -> dict[str, dict[str, Any]]:
    """Compute `expected_symlinks` (path + target + should_install per slot).

    Pure: takes already-resolved inputs.
    """
    base = home / ".claude" / "claude-md"
    cm = chop_root / "claude-md"
    machine = machine_info.machine
    machine_target_exists = (cm / "machines" / f"{machine}.md").is_file()
    plan: dict[str, dict[str, Any]] = {
        "global": {
            "path": str(base / "global.md"),
            "target": str(cm / "global.md"),
            "should_install": enabled,
        },
        "machine": {
            "path": str(base / "machine.md"),
            # Unknown / missing machine file → target points at the would-be
            # file and should_install=false; the skill will report that no
            # machine-type fragment is available rather than crash.
            "target": str(cm / "machines" / f"{machine}.md"),
            "should_install": enabled and machine_target_exists,
        },
        "dev_machine": {
            "path": str(base / "dev-machine.md"),
            "target": str(cm / "dev-machine.md"),
            "should_install": enabled and machine_info.dev_machine,
        },
    }
    return plan


def _inspect_slot(path: Path) -> dict[str, Any]:
    """Return the current filesystem state of a single slot path.

    Pure on the filesystem — calls lstat + readlink only. Never mutates.
    """
    is_symlink = path.is_symlink()
    exists = path.exists() or is_symlink  # lexists semantics
    resolves_to: str | None = None
    if is_symlink:
        try:
            resolves_to = os.readlink(path)
            # Normalize to an absolute path when the stored link is relative,
            # so comparisons against the plan's `target` (always absolute)
            # are meaningful.
            if not os.path.isabs(resolves_to):
                resolves_to = str((path.parent / resolves_to).resolve(strict=False))
        except OSError:
            resolves_to = None
    return {
        "exists": exists,
        "is_symlink": is_symlink,
        "resolves_to": resolves_to,
    }


def compute_slot_action(
    slot: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> dict[str, Any] | None:
    """Decide the single action (if any) needed to bring a slot to its expected state.

    Returns one of `create_symlink`, `replace_stale_symlink`,
    `remove_obsolete_symlink`, `report_user_file`, or `None` (slot is
    already correct). Pure.
    """
    should_install = expected["should_install"]
    target = expected["target"]
    path = expected["path"]
    if actual["is_symlink"]:
        if should_install:
            if actual["resolves_to"] == target:
                return None
            return {
                "kind": "replace_stale_symlink",
                "slot": slot,
                "path": path,
                "target": target,
                "current_target": actual["resolves_to"],
            }
        return {
            "kind": "remove_obsolete_symlink",
            "slot": slot,
            "path": path,
            "current_target": actual["resolves_to"],
        }
    if actual["exists"]:
        # Real file (or directory) sitting at the slot path — never touch it.
        return {
            "kind": "report_user_file",
            "slot": slot,
            "path": path,
        }
    # Slot is missing.
    if should_install:
        return {
            "kind": "create_symlink",
            "slot": slot,
            "path": path,
            "target": target,
        }
    return None


def _slot_drift(
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> bool:
    """True iff the slot's actual state disagrees with its expected state."""
    should_install = expected["should_install"]
    if not actual["exists"]:
        return should_install
    if not actual["is_symlink"]:
        # Real file — drift regardless of should_install; skill must
        # report it.
        return True
    if should_install:
        return actual["resolves_to"] != expected["target"]
    # symlink present but should_install=false → drift (obsolete).
    return True


def check_shared_claude_md(
    chop_root: Path,
    home: Path,
    enabled: bool,
    machine_info: MachineInfo,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Compute the `shared_claude_md` block + any errors to append.

    Called only when `resolve_chop_root` returned a real Path. Pure
    with respect to its inputs; the filesystem reads are via
    `_inspect_slot` which only stats, never writes.
    """
    errors: list[dict[str, Any]] = []
    claude_md_dir = home / ".claude" / "claude-md"
    # The parent directory MUST be a real directory, not a symlink.
    # A symlink here would redirect `.enabled`, `hooks-trusted.json`,
    # and every slot into attacker-controlled space.
    if claude_md_dir.is_symlink():
        errors.append(
            {
                "subsystem": "shared_claude_md",
                "code": "claude_md_dir_is_symlink",
                "message": (
                    f"{claude_md_dir} is a symlink; refusing to inspect slots. "
                    "Remove or replace with a real directory."
                ),
                "path": str(claude_md_dir),
            }
        )

    expected = _slot_plan(chop_root, home, machine_info, enabled)
    actual: dict[str, dict[str, Any]] = {}
    actions: list[dict[str, Any]] = []
    for slot in _SLOTS:
        slot_path = Path(expected[slot]["path"])
        state = _inspect_slot(slot_path)
        state["drift"] = _slot_drift(expected[slot], state)
        actual[slot] = state
        # If the parent dir is a symlink, skip action emission — the
        # skill will read the error first and abort.
        if claude_md_dir.is_symlink():
            continue
        action = compute_slot_action(slot, expected[slot], state)
        if action is not None:
            actions.append(action)

    block: dict[str, Any] = {
        "machine_info": {
            "machine": machine_info.machine,
            "dev_machine": machine_info.dev_machine,
            "reasons": list(machine_info.reasons),
        },
        "chop_root": str(chop_root),
        "enabled": enabled,
        "expected_symlinks": expected,
        "actual": actual,
        "actions": actions,
    }
    return block, errors


# ---------- post-up-to-date hook detection ----------


def check_post_up_to_date(repo_toplevel: Path | None) -> tuple[str | None, list[dict[str, Any]]]:
    """Locate `<repo>/.claude/post-up-to-date.md` and enforce symlink refusal.

    Returns `(path_or_none, errors)`. If the hook exists as a symlink,
    `path_or_none` is `None` and an error is emitted so the skill
    refuses to execute it.
    """
    errors: list[dict[str, Any]] = []
    if repo_toplevel is None:
        return None, errors
    hook_path = repo_toplevel / ".claude" / "post-up-to-date.md"
    if hook_path.is_symlink():
        errors.append(
            {
                "subsystem": "post_up_to_date",
                "code": "hook_is_symlink",
                "message": (
                    "Refusing to treat a symlinked post-up-to-date.md as a "
                    "trusted hook — symlink targets drift outside the repo's "
                    "commit history. Replace with a regular file or use "
                    "`@`-imports from the markdown instead."
                ),
                "path": str(hook_path),
            }
        )
        return None, errors
    if hook_path.is_file():
        return str(hook_path), errors
    return None, errors


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


def gh_pr_list_merged_heads(limit: int = 200) -> dict[str, str]:
    """Return {headRefName: headRefOid} for MERGED PRs in the current repo.

    Closes the squash-merge blind spot in patch-id absorption: a squash
    merge rewrites the diff into one commit whose patch-id differs from
    any individual branch commit, so `git cherry` labels the branch as
    unique work even though the PR landed. Querying `gh pr list` for
    MERGED PRs is the authoritative fallback.

    Returning the OID (not just the name) lets callers verify that the
    local branch tip still matches the SHA that was merged — protecting
    the case where the user added post-merge commits to a branch whose
    PR already landed. Newest-first ordering from `gh pr list`; first
    occurrence wins when multiple PRs share a headRefName.

    Returns `{}` on any failure (no gh auth, network error, not a GitHub
    repo) — callers should treat empty as "no extra absorption signal".
    """
    proc = _run(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "merged",
            "--limit",
            str(limit),
            "--json",
            "headRefName,headRefOid",
        ],
        check=False,
    )
    if proc.returncode != 0:
        return {}
    try:
        entries = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}
    if not isinstance(entries, list):
        return {}
    result: dict[str, str] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        name = e.get("headRefName")
        oid = e.get("headRefOid")
        if isinstance(name, str) and isinstance(oid, str):
            result.setdefault(name, oid)
    return result


def detect_default_branch(src: str) -> str:
    """Detect the default branch of a remote. Returns 'main' as last-resort fallback.

    Handles repos using 'main', 'master', or any other default branch name.
    Order of checks:
    1. `git symbolic-ref refs/remotes/<src>/HEAD` — what `git clone` sets up.
    2. Probe for `<src>/main` and `<src>/master` via `git show-ref`.
    3. Fall back to 'main' so callers always get a string.
    """
    sym = git_proc("symbolic-ref", f"refs/remotes/{src}/HEAD", check=False)
    if sym.returncode == 0:
        parsed = parse_symbolic_ref_output(sym.stdout, src)
        if parsed:
            return parsed
    for candidate in ("main", "master"):
        probe = git_proc(
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/remotes/{src}/{candidate}",
            check=False,
        )
        if probe.returncode == 0:
            return candidate
    return "main"


# ---------- Orchestrator ----------


def run_diagnose() -> dict[str, Any]:
    """Collect full diagnosis as a JSON-serializable dict.

    Top-level `errors` is a heterogeneous list: legacy git/gh failures
    are plain strings; shared-CLAUDE.md and post-up-to-date errors are
    dicts with `{subsystem, code, message, ...}` so the skill can
    filter by subsystem.
    """
    errors: list[Any] = []

    # Remote hygiene — needs to happen before fetch so we know the source name.
    remotes_raw = git("remote", "-v", check=False)
    remotes = parse_remotes(remotes_raw)
    analysis = classify_remotes(remotes, FORK_ORGS)
    src = analysis.source

    # Run fetch, current-branch PR lookup, and merged-PR-heads lookup in
    # parallel — all three hit different endpoints and don't depend on each
    # other. gh pr view/list read local branch state or remote API, not the
    # remote fetch result.
    with ThreadPoolExecutor(max_workers=3) as pool:
        fetch_fut = pool.submit(_run, ["git", "fetch", "--all", "--prune"], False)
        pr_fut = pool.submit(
            gh_pr_view_json,
            "state,number,title,mergeable,reviewDecision,reviews,comments",
        )
        merged_heads_fut = pool.submit(gh_pr_list_merged_heads)
        fetch_proc = fetch_fut.result()
        pr_data = pr_fut.result()
        merged_pr_heads = merged_heads_fut.result()

    if fetch_proc.returncode != 0:
        errors.append(f"git fetch failed: {fetch_proc.stderr.strip()}")

    # Detect the source's default branch (main, master, or other) AFTER fetch,
    # so remote refs are up-to-date. Done serially before the parallel block
    # because every subsequent query depends on it.
    default_branch = detect_default_branch(src)
    src_default = f"{src}/{default_branch}"

    # Post-fetch git queries are independent; run them in parallel.
    with ThreadPoolExecutor(max_workers=7) as pool:
        branch_name_fut = pool.submit(git_proc, "branch", "--show-current", check=False)
        divergence_fut = pool.submit(
            git_proc,
            "rev-list",
            "--left-right",
            "--count",
            f"{src_default}...HEAD",
            check=False,
        )
        behind_commits_fut = pool.submit(
            git_proc, "log", "--oneline", f"HEAD..{src_default}", check=False
        )
        uncommitted_fut = pool.submit(git_proc, "status", "--porcelain", check=False)
        stashes_fut = pool.submit(git_proc, "stash", "list", check=False)
        worktree_fut = pool.submit(
            git_proc, "worktree", "list", "--porcelain", check=False
        )
        local_branches_fut = pool.submit(
            git_proc,
            "for-each-ref",
            "refs/heads/",
            "--format=%(refname:short)\t%(objectname)",
            check=False,
        )
        branch_name_proc = branch_name_fut.result()
        divergence_proc = divergence_fut.result()
        behind_commits_proc = behind_commits_fut.result()
        uncommitted_proc = uncommitted_fut.result()
        stash_proc = stashes_fut.result()
        worktree_proc = worktree_fut.result()
        local_branches_proc = local_branches_fut.result()

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
            f"git log HEAD..{src_default} failed: {behind_commits_proc.stderr.strip()}"
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

    # `is_main` means "on the source's default branch" regardless of whether
    # that branch is literally named main or master. Kept as `is_main` for
    # JSON output-field stability; SKILL.md prose treats it as the
    # "on-default-branch" signal.
    is_main = branch_name == default_branch
    behind_commits = [ln for ln in behind_commits_raw.splitlines() if ln][:10]

    # Local branches to check for absorption (skip the default branch, skip empty).
    # Each line is `<name>\t<sha>`. We need SHAs to detect the post-merge-work
    # case: a branch whose PR merged (in merged_pr_heads) but whose local tip
    # has advanced past the merged head — unsafe to auto-absorb.
    local_branch_shas: dict[str, str] = {}
    if local_branches_proc.returncode != 0:
        errors.append(
            f"git for-each-ref refs/heads/ failed: {local_branches_proc.stderr.strip()}"
        )
        local_branch_names: list[str] = []
    else:
        for ln in local_branches_proc.stdout.splitlines():
            if not ln:
                continue
            name, _, sha = ln.partition("\t")
            if name and sha:
                local_branch_shas[name] = sha
        local_branch_names = sorted(local_branch_shas.keys())

    # Parse worktree porcelain output. Primary is the first entry.
    if worktree_proc.returncode != 0:
        errors.append(f"git worktree list failed: {worktree_proc.stderr.strip()}")
        worktree_entries: list[WorktreeRef] = []
    else:
        worktree_entries = parse_worktree_list(worktree_proc.stdout)

    # Run per-branch `git cherry` in parallel. This subsumes the old
    # single-HEAD cherry call — HEAD is in local_branch_names if it's not
    # detached, so we get its result from the same batch.
    #
    # Include any worktree branch not already in local_branch_names (e.g.
    # a worktree branch from a different remote context). De-dupe via set.
    cherry_targets = set(local_branch_names)
    for wt in worktree_entries:
        if wt.branch:
            cherry_targets.add(wt.branch)
    # Skip the source's default branch — we never audit it against itself
    # for absorption (it IS the absorption target).
    cherry_targets.discard(default_branch)

    cherry_by_branch: dict[str, CherryAnalysis] = {}
    if cherry_targets:
        with ThreadPoolExecutor(max_workers=min(10, len(cherry_targets))) as pool:
            cherry_futs = {
                b: pool.submit(git_proc, "cherry", "-v", src_default, b, check=False)
                for b in cherry_targets
            }
            for b, fut in cherry_futs.items():
                proc = fut.result()
                if proc.returncode != 0:
                    errors.append(
                        f"git cherry -v {src_default} {b} failed: {proc.stderr.strip()}"
                    )
                    continue
                cherry_by_branch[b] = parse_cherry_status(proc.stdout.strip())

    # HEAD's cherry result flows into the existing branch block.
    cherry = cherry_by_branch.get(
        branch_name, CherryAnalysis(unique_commits=[], equivalent_commits=[])
    )
    ahead_patch_unique_commits = cherry.unique_commits[:10]
    ahead_patch_equivalent_commits = cherry.equivalent_commits[:10]
    can_force_align = is_main and ahead > 0 and not cherry.unique_commits

    leftover_commits = [] if is_main else ahead_patch_unique_commits

    # Absorbable branches: local branches whose work is fully in $src_default,
    # caught via either (a) zero unique patch-ids from `git cherry`, or
    # (b) a MERGED PR whose recorded head SHA still matches the local
    # branch tip. (b) catches squash-merges that (a) misses — the squashed
    # commit's patch-id differs from the branch's commits. Requiring the
    # SHA match protects the post-merge-work case: if the user added
    # commits to the branch AFTER the PR merged, the local tip has
    # advanced past `headRefOid` and the branch still holds unsynced
    # work; we classify it as `squash_merged_diverged_branches` and do
    # NOT mark it absorbable.
    #
    # Excludes the currently checked-out branch since deleting it requires
    # switching away first — callers should surface it separately if they
    # want that.
    absorbable_branches: list[str] = []
    squash_absorbed: set[str] = set()
    squash_diverged: set[str] = set()
    for b in sorted(local_branch_names):
        if b == default_branch:
            continue
        if b == branch_name:
            continue  # never auto-prune the checked-out branch
        analysis_for_b = cherry_by_branch.get(b)
        patch_absorbed = (
            analysis_for_b is not None and not analysis_for_b.unique_commits
        )
        merged_head_sha = merged_pr_heads.get(b)
        local_sha = local_branch_shas.get(b)
        if merged_head_sha is not None and local_sha is not None:
            pr_merged_safe = merged_head_sha == local_sha
            pr_merged_diverged = not pr_merged_safe
        else:
            pr_merged_safe = False
            pr_merged_diverged = False
        if patch_absorbed or pr_merged_safe:
            absorbable_branches.append(b)
        if pr_merged_safe and not patch_absorbed:
            squash_absorbed.add(b)
        if pr_merged_diverged and not patch_absorbed:
            squash_diverged.add(b)

    # Worktree classification: first entry is primary, rest are linked.
    # For each linked worktree, flag "absorbed" if its branch has zero
    # patch-unique commits vs $src/main.
    worktrees_out: list[dict[str, Any]] = []
    for idx, wt in enumerate(worktree_entries):
        is_primary = idx == 0
        analysis_for_wt = cherry_by_branch.get(wt.branch)
        if is_primary:
            # Primary checkout is never a deletion candidate regardless of
            # absorption state. Expose absorbed=False so any consumer that
            # only checks `absorbed` still treats it safely.
            absorbed = False
            unmerged_count: int | None = None
        elif analysis_for_wt is None:
            # No cherry result (branch skipped because it's main, or errored).
            # Conservative: treat as not-absorbed unless a merged PR's head
            # SHA still matches the local branch tip.
            merged_head_sha = merged_pr_heads.get(wt.branch)
            local_sha = local_branch_shas.get(wt.branch)
            pr_merged_safe = (
                merged_head_sha is not None
                and local_sha is not None
                and merged_head_sha == local_sha
            )
            absorbed = pr_merged_safe
            unmerged_count = 0 if pr_merged_safe else None
        else:
            unique = analysis_for_wt.unique_commits
            patch_absorbed = len(unique) == 0
            merged_head_sha = merged_pr_heads.get(wt.branch)
            local_sha = local_branch_shas.get(wt.branch)
            pr_merged_safe = (
                merged_head_sha is not None
                and local_sha is not None
                and merged_head_sha == local_sha
            )
            absorbed = patch_absorbed or pr_merged_safe
            # When the PR merged via squash AND the local tip still matches
            # the merged head, the branch's commits are still patch-id-unique
            # but the work is fully in main — report 0 unmerged. When the
            # local tip has advanced past the merged head, the branch has
            # post-merge work; fall through to len(unique) to reflect it.
            unmerged_count = 0 if pr_merged_safe else len(unique)
        worktrees_out.append(
            {
                "path": wt.path,
                "branch": wt.branch,
                "is_primary": is_primary,
                "absorbed": absorbed,
                "unmerged_count": unmerged_count,
            }
        )

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

    # Machine detection — pure Python, no shelling out.
    machine_info = detect_machine()

    # Resolve chop-conventions root so we know where to point symlinks.
    home = Path.home()
    env = dict(os.environ)
    chop_root = resolve_chop_root(env, home)
    shared_block: dict[str, Any] | None = None
    if chop_root is None:
        probed: list[str] = []
        if env.get("CHOP_CONVENTIONS_ROOT"):
            probed.append(env["CHOP_CONVENTIONS_ROOT"])
        probed.append(str(home / "gits" / "chop-conventions"))
        errors.append(
            {
                "subsystem": "shared_claude_md",
                "code": "chop_root_unresolved",
                "message": (
                    "Could not locate a chop-conventions checkout containing "
                    "`claude-md/global.md`. Set CHOP_CONVENTIONS_ROOT or clone "
                    "to ~/gits/chop-conventions."
                ),
                "probed": probed,
            }
        )
    else:
        enabled = (home / ".claude" / "claude-md" / ".enabled").is_file()
        shared_block, shared_errors = check_shared_claude_md(
            chop_root=chop_root,
            home=home,
            enabled=enabled,
            machine_info=machine_info,
        )
        errors.extend(shared_errors)

    # Locate the repo toplevel for the post-up-to-date hook. `git
    # rev-parse --show-toplevel` is the canonical way — NOT cwd.
    toplevel_proc = git_proc("rev-parse", "--show-toplevel", check=False)
    if toplevel_proc.returncode == 0:
        repo_toplevel: Path | None = Path(toplevel_proc.stdout.strip())
    else:
        repo_toplevel = None
    post_up_to_date_path, hook_errors = check_post_up_to_date(repo_toplevel)
    errors.extend(hook_errors)

    result: dict[str, Any] = {
        "remotes": {
            "entries": [asdict(r) for r in analysis.entries],
            "source": analysis.source,
            "is_fork_workflow": analysis.is_fork_workflow,
            "issues": [asdict(i) for i in analysis.issues],
        },
        "branch": {
            "name": branch_name,
            "is_main": is_main,
            "default_branch_name": default_branch,
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
        "worktrees": worktrees_out,
        "absorbable_branches": absorbable_branches,
        "squash_merged_branches": sorted(squash_absorbed),
        "squash_merged_diverged_branches": sorted(squash_diverged),
        "pr": pr_block,
        "post_up_to_date_path": post_up_to_date_path,
        "errors": errors,
    }
    # Per spec: when resolve_chop_root returns None, omit the
    # `shared_claude_md` key entirely rather than emitting an empty
    # block. The error in `errors[]` is the signal.
    if shared_block is not None:
        result["shared_claude_md"] = shared_block
    return result


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


def cli_main() -> None:
    """Console-script entry point. Wired via `[project.scripts] up-to-date-diag = ...`."""
    raise SystemExit(main())


if __name__ == "__main__":
    cli_main()
