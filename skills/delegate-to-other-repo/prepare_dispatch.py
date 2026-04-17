#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "typer>=0.12",
# ]
# ///
"""Prepare a delegated-worktree dispatch for the delegate-to-other-repo skill.

Replaces Phases 1-3 of the skill's bash recipe with a single typed helper:

  * resolve the target repo path from user input (abs/rel path or bare name)
  * fetch origin (+ upstream in parallel when present)
  * refresh refs/remotes/origin/HEAD
  * resolve default branch via symbolic-ref -> gh fallback -> literal "main"
  * choose base ref (upstream/<default> if upstream exists, else origin/<default>)
  * validate base ref is reachable
  * sanitize + collision-check the task slug across heads AND origin refs
  * idempotently write .worktrees/ to .git/info/exclude
  * create the worktree on branch `delegated/<slug>`
  * resolve the parent session's Claude jsonl via pwd hash (pwd -P, [/.] -> -)
  * parse owner/repo slug from origin URL

Output: a single JSON object on stdout with all the data the parent needs
to render a brief. The parent still owns brief rendering — this helper
only collects inputs.

Pure functions (slug sanitization, default-branch chain, session-log hash,
remote URL parsing, base-ref selection) are importable from
test_prepare_dispatch.py without any deps (typer is lazy-imported inside
_build_app()).

Usage:
    prepare_dispatch.py --target <name|path> --slug <slug> --task "<desc>"
    prepare_dispatch.py ... --dry-run   # validate + emit JSON, do not mutate
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable


# ---------- Pure functions (unit-tested) ----------


# Slug sanitization mirrors worktree-recipe.md §4 — lowercase, non-alnum
# collapsed to `-`, strip leading/trailing `-`, cut to 40 chars, re-strip.
_SLUG_INVALID_RE = re.compile(r"[^a-z0-9]+")
_SLUG_MAX_LEN = 40


def sanitize_slug(raw: str) -> str | None:
    """Return a kebab-case slug, or None if the input produced nothing usable.

    Rules match worktree-recipe.md §4:
      1. Lowercase
      2. Collapse runs of non-[a-z0-9] to a single `-`
      3. Strip leading/trailing `-`
      4. Truncate to 40 chars, re-strip trailing `-`

    Non-ASCII input collapses entirely to `-` under step 2 and is stripped
    to an empty string by step 3 — caller falls back to a timestamp form.
    """
    lowered = raw.lower()
    collapsed = _SLUG_INVALID_RE.sub("-", lowered).strip("-")
    if not collapsed:
        return None
    truncated = collapsed[:_SLUG_MAX_LEN].rstrip("-")
    return truncated or None


def timestamp_slug(now: _dt.datetime | None = None) -> str:
    """Fallback slug when sanitize returns None."""
    n = now if now is not None else _dt.datetime.now()
    return f"task-{n.strftime('%Y%m%d-%H%M%S')}"


def resolve_unique_slug(
    base_slug: str,
    ref_exists: Callable[[str], bool],
    now: _dt.datetime | None = None,
) -> str:
    """Append -2..-9 suffixes if `delegated/<slug>` collides, else timestamp form.

    `ref_exists(candidate)` should return True iff either
    `refs/heads/delegated/<candidate>` or
    `refs/remotes/origin/delegated/<candidate>` exists — caller owns the
    actual git invocation. Checking both is load-bearing: heads-only missed
    a case where origin had the name, which then rejected the push as
    non-fast-forward (force-push is prohibited).
    """
    if not ref_exists(base_slug):
        return base_slug
    for i in range(2, 10):
        candidate = f"{base_slug}-{i}"
        if not ref_exists(candidate):
            return candidate
    return timestamp_slug(now)


def choose_default_branch(
    symbolic_ref_out: str | None,
    gh_default_out: str | None,
) -> str:
    """Pick a default-branch name from the chain symbolic-ref -> gh -> 'main'.

    Each step MUST be an explicit guard. Piping through `|| echo main`
    swallows empty output and falsely claims success — verified in the
    worktree-recipe notes (T=/tmp/nonexistent recipe returned '').
    """
    if symbolic_ref_out:
        # `git symbolic-ref --short refs/remotes/origin/HEAD` returns
        # `origin/main`. Strip any `origin/` prefix defensively.
        name = symbolic_ref_out.strip()
        if name.startswith("origin/"):
            name = name[len("origin/") :]
        if name:
            return name
    if gh_default_out:
        name = gh_default_out.strip()
        if name:
            return name
    return "main"


def choose_base(
    default_branch: str,
    upstream_has_ref: bool,
) -> tuple[str, str]:
    """Return `(base_remote, base_ref)` — upstream preferred when reachable.

    When both `upstream` and `origin` exist (fork workflow), canonical main
    lives on upstream and `origin/<default>` may lag. Basing the worktree
    on stale origin forces rebase at runtime — a real incident 2026-04-16.
    """
    if upstream_has_ref:
        return "upstream", f"upstream/{default_branch}"
    return "origin", f"origin/{default_branch}"


# Owner/repo parse covers both HTTPS and SSH remote URL forms.
# HTTPS: https://github.com/owner/repo[.git]
# SSH:   git@github.com:owner/repo[.git]
_REMOTE_SLUG_RE = re.compile(
    r"""
    ^
    (?:https?://[^/]+/|git@[^:]+:)   # scheme-or-ssh host prefix
    (?P<owner>[^/]+)/(?P<repo>[^/]+?)
    (?:\.git)?$
    """,
    re.VERBOSE,
)


def parse_repo_slug(url: str) -> str | None:
    """Return `owner/repo` from a git remote URL, or None on no match."""
    m = _REMOTE_SLUG_RE.match(url.strip())
    if not m:
        return None
    return f"{m['owner']}/{m['repo']}"


def session_log_hash_of(path: str) -> str:
    """Hash a cwd path into Claude Code's project-dir convention.

    Two gotchas (both bite in practice — see SKILL.md §Session log resolution):

      1. Both `/` and `.` become `-`. A repo at
         `/home/foo/gits/bar.github.io` hashes to
         `-home-foo-gits-bar-github-io`, NOT ...`bar.github.io`. The regex
         uses `[/.]` to catch both.
      2. Callers MUST pass the physical path (os.path.realpath / pwd -P),
         not the logical one. Claude hashes the physical path; a symlinked
         shortcut produces a hash that matches no project dir.
    """
    return re.sub(r"[/.]", "-", path)


def find_newest_jsonl(project_dir: Path) -> str | None:
    """Return the newest `*.jsonl` under `project_dir` by mtime, or None.

    Stdlib only — this is a ~/bin/ls -t equivalent that doesn't shell out.
    """
    if not project_dir.is_dir():
        return None
    candidates = sorted(
        project_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return str(candidates[0]) if candidates else None


def resolve_session_log(
    cwd_physical: str,
    repo_toplevel: str | None,
    home: Path,
) -> str | None:
    """Find the parent session's jsonl via cwd hash, then repo-toplevel hash.

    Both lookups use the same hash convention (`[/.]` -> `-`). Returns None
    if neither yields a jsonl — parent will omit the historical-context
    section of the brief. Parallel sessions in the same cwd resolve to
    "whichever jsonl was most recently written" — an accepted v1 ambiguity.
    """
    base = home / ".claude" / "projects"
    cwd_dir = base / session_log_hash_of(cwd_physical)
    found = find_newest_jsonl(cwd_dir)
    if found:
        return found
    if repo_toplevel and repo_toplevel != cwd_physical:
        top_dir = base / session_log_hash_of(repo_toplevel)
        return find_newest_jsonl(top_dir)
    return None


def resolve_target_path(
    target: str, cwd: Path, home: Path
) -> tuple[Path | None, str | None]:
    """Return `(resolved_path, error)` for the caller's target argument.

    Rules mirror SKILL.md §1a:
      * absolute path -> use it
      * relative path -> resolve against cwd
      * bare name -> `~/gits/<name>`
      * `owner/repo` slug -> error (skill does not clone)

    Does not stat the path — caller validates existence via git-is-inside.
    """
    if not target:
        return None, "empty target"
    # owner/repo slug: exactly one `/`, no other path separators or dots
    if (
        target.count("/") == 1
        and not target.startswith((".", "/"))
        and "\\" not in target
    ):
        owner, repo = target.split("/", 1)
        if owner and repo and "/" not in repo:
            return None, (
                f"'{target}' looks like an owner/repo slug. This skill does not "
                f"clone repos. Run `gh repo clone {target} ~/gits/{repo}` first, "
                "then retry with `--target " + repo + "`."
            )
    p = Path(target)
    if p.is_absolute():
        return p, None
    if "/" in target or target.startswith("."):
        return (cwd / p).resolve(), None
    # bare name -> ~/gits/<name>
    return home / "gits" / target, None


# ---------- Thin I/O wrappers (not unit-tested — mocked via fake_git in tests) ----------


def _run(
    cmd: list[str], *, check: bool = False, cwd: str | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check, cwd=cwd)


def _git(target: str, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    return _run(["git", "-C", target, *args], check=check)


def _remote_exists(target: str, name: str) -> bool:
    proc = _git(target, "remote")
    if proc.returncode != 0:
        return False
    return name in {ln.strip() for ln in proc.stdout.splitlines() if ln.strip()}


def _rev_parse_verify(target: str, ref: str) -> bool:
    proc = _git(target, "rev-parse", "--verify", "--quiet", ref)
    return proc.returncode == 0


def _ref_exists_anywhere(target: str) -> Callable[[str], bool]:
    """Return a closure that checks both heads and origin refs for a slug."""

    def _check(slug: str) -> bool:
        for ref in (
            f"refs/heads/delegated/{slug}",
            f"refs/remotes/origin/delegated/{slug}",
        ):
            if _rev_parse_verify(target, ref):
                return True
        return False

    return _check


def _get_repo_slug(target: str) -> str | None:
    proc = _git(target, "remote", "get-url", "origin")
    if proc.returncode != 0:
        return None
    return parse_repo_slug(proc.stdout.strip())


def _symbolic_ref_origin_head(target: str) -> str | None:
    proc = _git(target, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _gh_default_branch(slug: str | None) -> str | None:
    if not slug:
        return None
    proc = _run(
        [
            "gh",
            "repo",
            "view",
            slug,
            "--json",
            "defaultBranchRef",
            "-q",
            ".defaultBranchRef.name",
        ]
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _fetch_remote(target: str, name: str) -> subprocess.CompletedProcess:
    return _git(target, "fetch", name, "--quiet")


def _git_common_dir(target: str) -> Path | None:
    # `--path-format=absolute` is load-bearing: plain `--git-common-dir`
    # returns a path relative to cwd, and this helper never cd's into the
    # target. Without `--path-format=absolute` the append below would land
    # in the caller's cwd (verified incident on 2026-04-16).
    proc = _git(target, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    return Path(out) if out else None


def _ensure_exclude(target: str) -> tuple[bool, str | None]:
    """Idempotently append `.worktrees/` to `<git-common-dir>/info/exclude`.

    Returns `(wrote, error)`. `wrote=False` and `error=None` means the
    entry was already present.
    """
    common = _git_common_dir(target)
    if common is None:
        return False, "git rev-parse --git-common-dir failed"
    info = common / "info"
    exclude_file = info / "exclude"
    try:
        if exclude_file.is_file():
            existing = exclude_file.read_text()
            for line in existing.splitlines():
                if line.strip() == ".worktrees/":
                    return False, None
        info.mkdir(parents=True, exist_ok=True)
        with exclude_file.open("a", encoding="utf-8") as f:
            f.write("\n# Added by delegate-to-other-repo skill\n.worktrees/\n")
    except OSError as e:
        return False, f"write to {exclude_file} failed: {e}"
    return True, None


def _worktree_add(
    target: str, path: str, branch: str, base_ref: str
) -> subprocess.CompletedProcess:
    return _git(target, "worktree", "add", path, "-b", branch, base_ref)


# ---------- Orchestrator ----------


def run_prepare(
    target_raw: str,
    slug_raw: str,
    task: str,
    dry_run: bool,
    cwd: Path,
    home: Path,
) -> dict[str, Any]:
    """Execute all prepare phases and return a JSON-serializable dict.

    On any pre-mutation error, returns early with a populated `errors` list
    and an unpopulated `worktree_path`. When `dry_run=True`, skips the
    mutation steps (worktree add, exclude write) but still returns the
    JSON that would have been emitted.
    """
    errors: list[str] = []
    result: dict[str, Any] = {
        "worktree_path": None,
        "branch": None,
        "base_ref": None,
        "base_remote": None,
        "default_branch": None,
        "target_repo_slug": None,
        "session_log": None,
        "task": task,
        "dry_run": dry_run,
        "errors": errors,
    }

    # ---- resolve target path ----
    target_path, err = resolve_target_path(target_raw, cwd, home)
    if err or target_path is None:
        errors.append(err or "could not resolve target")
        return result
    target_str = str(target_path)
    result["target"] = target_str

    if not target_path.exists():
        errors.append(f"target does not exist: {target_str}")
        return result

    inside = _git(target_str, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        errors.append(f"target is not a git repo: {target_str}")
        return result

    # Parse slug from origin URL upfront — we need it as a gh fallback input
    # and for the final JSON output.
    repo_slug = _get_repo_slug(target_str)
    result["target_repo_slug"] = repo_slug
    if repo_slug is None:
        errors.append(
            f"could not parse owner/repo from `git -C {target_str} remote get-url origin`"
        )
        return result

    # ---- fetch origin (+ upstream in parallel if present) ----
    has_upstream = _remote_exists(target_str, "upstream")
    with ThreadPoolExecutor(max_workers=2) as pool:
        origin_fut = pool.submit(_fetch_remote, target_str, "origin")
        upstream_fut = (
            pool.submit(_fetch_remote, target_str, "upstream") if has_upstream else None
        )
        origin_proc = origin_fut.result()
        upstream_proc = upstream_fut.result() if upstream_fut else None

    if origin_proc.returncode != 0:
        errors.append(f"git fetch origin failed: {origin_proc.stderr.strip()}")
        return result
    if upstream_proc is not None and upstream_proc.returncode != 0:
        # Non-fatal — we can still fall back to origin/<default>.
        errors.append(
            f"git fetch upstream failed (continuing with origin base): "
            f"{upstream_proc.stderr.strip()}"
        )
        has_upstream = False

    # Refresh origin/HEAD — plain `git fetch` does NOT. Idempotent no-op.
    _git(target_str, "remote", "set-head", "origin", "--auto")

    # ---- default branch chain ----
    sym = _symbolic_ref_origin_head(target_str)
    gh_out = _gh_default_branch(repo_slug) if not sym else None
    default_branch = choose_default_branch(sym, gh_out)
    result["default_branch"] = default_branch

    # ---- base ref selection ----
    upstream_ref_reachable = has_upstream and _rev_parse_verify(
        target_str, f"upstream/{default_branch}"
    )
    base_remote, base_ref = choose_base(default_branch, upstream_ref_reachable)
    result["base_remote"] = base_remote
    result["base_ref"] = base_ref

    if not _rev_parse_verify(target_str, base_ref):
        errors.append(f"{base_ref} is not reachable in {target_str} after fetch")
        return result

    # ---- slug sanitization + collision resolution ----
    clean = sanitize_slug(slug_raw) or sanitize_slug(task) or timestamp_slug()
    final_slug = resolve_unique_slug(clean, _ref_exists_anywhere(target_str))
    branch = f"delegated/{final_slug}"
    worktree_path = str(target_path / ".worktrees" / f"delegated-{final_slug}")
    result["slug"] = final_slug
    result["branch"] = branch
    result["worktree_path"] = worktree_path

    # ---- session log resolution ----
    cwd_physical = str(cwd.resolve())
    toplevel_proc = _git(".", "rev-parse", "--show-toplevel")
    repo_toplevel = (
        toplevel_proc.stdout.strip() if toplevel_proc.returncode == 0 else None
    )
    result["session_log"] = resolve_session_log(cwd_physical, repo_toplevel, home)

    if dry_run:
        return result

    # ---- mutations (skipped on --dry-run) ----
    _wrote, exclude_err = _ensure_exclude(target_str)
    if exclude_err:
        errors.append(exclude_err)
        return result

    wt_proc = _worktree_add(target_str, worktree_path, branch, base_ref)
    if wt_proc.returncode != 0:
        errors.append(f"git worktree add failed: {wt_proc.stderr.strip()}")
        # The previously-populated worktree_path is now misleading since
        # nothing was created; clear it so the parent doesn't dispatch.
        result["worktree_path"] = None
        return result

    return result


# ---------- CLI ----------


def _build_app():
    """Wire up the Typer app. Called only when executed as a script so
    tests and module-importers don't need `typer` on their PYTHONPATH."""
    import typer

    app = typer.Typer(
        add_completion=False,
        help="Prepare a delegated-worktree dispatch (delegate-to-other-repo Phase 1-3).",
        no_args_is_help=True,
    )

    @app.callback(invoke_without_command=True)
    def main(
        target: str = typer.Option(
            ...,
            "--target",
            help="Target repo: absolute path, relative path, or bare name (resolved under ~/gits/).",
        ),
        slug: str = typer.Option(
            ...,
            "--slug",
            help="Task slug; sanitized and collision-checked against refs/heads/ AND refs/remotes/origin/.",
        ),
        task: str = typer.Option(
            ...,
            "--task",
            help="Task description (passed through to the brief — no semantic interpretation).",
        ),
        dry_run: bool = typer.Option(
            False,
            "--dry-run",
            help="Validate + emit JSON, but do not create the worktree or write .git/info/exclude.",
        ),
        pretty: bool = typer.Option(
            False, "--pretty", help="Pretty-print JSON output."
        ),
    ) -> None:
        """Prepare a worktree and emit structured JSON for the parent skill."""
        data = run_prepare(
            target_raw=target,
            slug_raw=slug,
            task=task,
            dry_run=dry_run,
            cwd=Path.cwd(),
            home=Path.home(),
        )
        indent = 2 if pretty else None
        json.dump(data, sys.stdout, indent=indent)
        sys.stdout.write("\n")
        raise typer.Exit(1 if data["errors"] else 0)

    return app


if __name__ == "__main__":
    _build_app()()
